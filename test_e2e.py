"""
Ponta a ponta: EventManager e Sync de verdade (../integracao/events)
enviando para o backend numa porta local, como a Raspberry Pi fará.

Cobre a política de enlace (4G sem fotos, Wi-Fi com fotos), a confirmação
perdida (reenvio sem duplicar) e o servidor fora do ar (nada sai da fila).

    python3 test_e2e.py
"""

import os
import socket
import sys
import tempfile
import threading
import time

# Sempre isolado, mesmo que o shell aponte para o banco de produção.
TEST_DIR = tempfile.TemporaryDirectory(prefix="vguard-test-")
os.environ["DATABASE_PATH"] = os.path.join(TEST_DIR.name, "backend.db")
os.environ["PHOTOS_DIR"] = os.path.join(TEST_DIR.name, "fotos")
os.environ["DASHBOARD_PASSWORD"] = "senha-de-teste"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get(
    "VGUARD_EVENTS", os.path.join(HERE, "..", "integracao", "events")
))

import httpx
import uvicorn

import app
import sync
from event_manager import EventManager

JPEG = b"\xff\xd8" + os.urandom(2000) + b"\xff\xd9"
VIEWER = ("vguard", "senha-de-teste")


def start_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=port, log_level="warning"))
    server.thread = threading.Thread(target=server.run, daemon=True)
    server.thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not server.thread.is_alive() or time.monotonic() >= deadline:
            server.should_exit = True
            raise RuntimeError("backend não iniciou em 10 s")
        time.sleep(0.05)
    sync.BACKEND_URL = "http://127.0.0.1:%d/api" % port
    return server, "http://127.0.0.1:%d" % port


def stop_server(server):
    # server.started nunca volta a False: espera a thread terminar.
    server.should_exit = True
    server.thread.join()


def drive(manager, start, seconds, events=()):
    """Telemetria a 1 Hz por `seconds` s; eventos em (segundo, tipo)."""
    events = dict(events)
    for t in range(seconds):
        ts = start + t
        manager.update("obd", {"speed": 50 + t, "rpm": 2000}, ts)
        manager.update("gps", {"lat": -25.44, "lon": -49.27, "speed": 51 + t}, ts + 0.002)
        if t in events:
            assert manager.record_event(events[t], 0.8, JPEG, ts + 0.004)


def counts(base):
    """(eventos, eventos com foto, linhas de telemetria) vistos pelo dashboard."""
    events = httpx.get(base + "/api/events", auth=VIEWER).json()
    telemetry = httpx.get(base + "/api/telemetry", auth=VIEWER).json()
    return len(events), sum(e["has_photo"] for e in events), len(telemetry)


def main():
    server, base = start_server()
    with app.db() as cur:
        for table in ("health", "telemetry", "events", "devices"):
            cur.execute("DELETE FROM " + table)
    sync.DEVICE_ID = "vguard-01"
    sync.DEVICE_TOKEN = app.new_device("vguard-01")

    manager = EventManager(os.path.join(TEST_DIR.name, "pi"))
    start = time.time() - 120
    # Os eventos só entram na fila quando a janela de ±10 s fecha.
    drive(manager, start, 40, [(5, "fadiga"), (20, "distracao")])
    rows = len(manager.pending_telemetry(10**6))
    link = ["cellular"]
    pi = sync.Sync(manager, link=lambda: link[0])

    # 4G: telemetria, metadados e fotos sobem pelo mesmo enlace (as fotos vão por último).
    assert pi.sync_once() == "cellular"
    assert manager.pending_counts() == {"telemetry": 0, "events": 0, "photos": 0}
    assert counts(base) == (2, 2, rows)
    events = httpx.get(base + "/api/events", auth=VIEWER).json()
    assert not any(e["photo_pending"] for e in events)

    # Wi-Fi: nada pendente, o ciclo só leva a saúde.
    link[0] = "wifi"
    assert pi.sync_once() == "wifi"
    assert counts(base) == (2, 2, rows)
    event_id = httpx.get(base + "/api/events", auth=VIEWER).json()[0]["id"]
    assert httpx.get(base + "/api/events/%s/photo" % event_id, auth=VIEWER).content == JPEG

    # A janela de ±10 s chegou junto com o evento (RF17).
    detail = httpx.get(base + "/api/events/" + event_id, auth=VIEWER).json()
    assert {w["source"] for w in detail["window"]} == {"obd", "gps"}
    assert detail["window"] == manager.window(event_id)

    # A saúde do dispositivo chega a cada ciclo (RF32).
    h = httpx.get(base + "/api/health", auth=VIEWER).json()
    assert h["link"] == "wifi" and h["last_contact"]
    # A saúde do fim do ciclo já mostra a fila vazia, não a de antes do envio.
    assert h["pending"] == {"telemetry": 0, "events": 0, "photos": 0}

    # Confirmação perdida: o servidor gravou, mas a resposta não chegou à Pi.
    # A Pi reenvia tudo, e o servidor não duplica nada (RF22, RNF26).
    with manager._lock, manager._db:
        manager._db.execute("UPDATE telemetry SET synced = 0")
        manager._db.execute("UPDATE events SET meta_synced = 0, photo_synced = 0")
    assert pi.sync_once() == "wifi"
    assert manager.pending_counts() == {"telemetry": 0, "events": 0, "photos": 0}
    assert counts(base) == (2, 2, rows)

    # Servidor fora do ar: o envio falha e o evento novo continua pendente (RF23).
    stop_server(server)
    drive(manager, start + 60, 30, [(5, "fadiga")])
    try:
        pi.sync_once()
        raise AssertionError("o envio deveria ter falhado")
    except OSError:
        pass
    assert manager.pending_counts()["events"] == 1
    rows += len(manager.pending_telemetry(10**6))

    # O servidor volta: o que estava pendente sobe, sem perder nada.
    server, base = start_server()
    assert pi.sync_once() == "wifi"
    assert manager.pending_counts() == {"telemetry": 0, "events": 0, "photos": 0}
    assert counts(base) == (3, 3, rows)

    # Sem enlace: nada é tentado.
    link[0] = None
    assert pi.sync_once() is None

    manager.close()
    stop_server(server)
    print("OK")


if __name__ == "__main__":
    main()
