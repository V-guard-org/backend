"""
Testa as rotas contra um SQLite temporário, isolado de qualquer banco existente.

    python3 test_app.py
"""

import os
from pathlib import Path
import tempfile
import uuid

# Sempre isolado, mesmo que o shell aponte para o banco de produção.
TEST_DIR = tempfile.TemporaryDirectory(prefix="vguard-test-")
os.environ["DATABASE_PATH"] = os.path.join(TEST_DIR.name, "backend.db")
os.environ["PHOTOS_DIR"] = os.path.join(TEST_DIR.name, "fotos")
os.environ["DASHBOARD_PASSWORD"] = "senha-de-teste"

# O teste de servir HTML é independente do checkout do dashboard.
os.environ["DASHBOARD_DIR"] = TEST_DIR.name
Path(TEST_DIR.name, "index.html").write_text("<html>V-GUARD</html>", encoding="utf-8")

from fastapi.testclient import TestClient

import app

JPEG = b"\xff\xd8" + b"\x00" * 1000 + b"\xff\xd9"
VIEWER = ("vguard", "senha-de-teste")


def reset():
    with app.db() as cur:
        for table in ("health", "telemetry", "events", "devices"):
            cur.execute("DELETE FROM " + table)
    return {"X-Device-Id": "vguard-01", "Authorization": "Bearer " + app.new_device("vguard-01")}


def event_body(ts, type="fadiga", **extra):
    return {"id": "ignorado", "type": type, "ts": ts, "score": 0.8,
            "data": {"obd": {"ts": ts, "speed": 60}},
            "window": [{"ts": ts - 1, "source": "obd", "data": {"speed": 58}}], **extra}


def count(table):
    with app.db() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM " + table)
        return cur.fetchone()["n"]


def test_device_auth(client, pi):
    bad = {**pi, "Authorization": "Bearer errado"}
    assert client.post("/api/health", json={}, headers=bad).status_code == 401
    assert client.post("/api/health", json={}, headers={"X-Device-Id": "vguard-01"}).status_code == 401
    other = {**pi, "X-Device-Id": "outro"}
    assert client.post("/api/health", json={}, headers=other).status_code == 401


def test_idempotent_ingestion(client, pi):
    # Telemetria: o mesmo lote duas vezes (confirmação perdida) não duplica.
    batch = {"items": [{"id": i, "ts": 1000.0 + i, "data": {"imu": {"ax": 0.1}}} for i in range(5)]}
    for _ in range(2):
        r = client.post("/api/telemetry", json=batch, headers=pi)
        assert r.status_code == 200, r.text
    assert count("telemetry") == 5

    # O SQLite da Pi reaproveita ids depois da limpeza: id repetido com ts novo é linha nova.
    reused = {"items": [{"id": 0, "ts": 2000.0, "data": {}}]}
    assert client.post("/api/telemetry", json=reused, headers=pi).status_code == 200
    assert count("telemetry") == 6

    # Evento: o mesmo UUID duas vezes vira um evento só.
    event_id = str(uuid.uuid4())
    for _ in range(2):
        r = client.put("/api/events/" + event_id, json=event_body(1002.5, has_photo=True), headers=pi)
        assert r.status_code == 200, r.text
    assert count("events") == 1

    # Tolerância: campo desconhecido e tipo novo não são recusados.
    r = client.put("/api/events/%s" % uuid.uuid4(), json=event_body(1003.0, "frenagem", novo=1), headers=pi)
    assert r.status_code == 200, r.text
    return event_id


def test_photo(client, pi, event_id):
    url = "/api/events/%s/photo" % event_id
    jpeg = {**pi, "Content-Type": "image/jpeg"}

    # Foto de evento que ainda não chegou: 409, o Sync tenta de novo.
    assert client.put("/api/events/%s/photo" % uuid.uuid4(), content=JPEG, headers=jpeg).status_code == 409
    # Recusas definitivas: não é JPEG, ou grande demais.
    assert client.put(url, content=b"GIF89a", headers=jpeg).status_code == 400
    big = b"\xff\xd8" + b"\x00" * app.MAX_PHOTO_BYTES
    assert client.put(url, content=big, headers=jpeg).status_code == 413

    # Antes da foto, o dashboard sabe que ela ainda está no cartão da Pi.
    assert client.get(url, auth=VIEWER).status_code == 404
    detail = client.get("/api/events/" + event_id, auth=VIEWER).json()
    assert detail["photo_pending"] and not detail["has_photo"]
    for _ in range(2):      # reenvio sobrescreve, sem erro
        assert client.put(url, content=JPEG, headers=jpeg).status_code == 200
    r = client.get(url, auth=VIEWER)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == JPEG
    assert not [n for n in os.listdir(app.PHOTOS_DIR) if n.endswith(".tmp")]


def test_dashboard(client, pi, event_id):
    # Sem login, ou com senha errada, nada do dashboard abre.
    for path in ["/", "/api/health", "/api/events", "/api/telemetry", "/api/events/" + event_id]:
        assert client.get(path).status_code == 401, path
        assert client.get(path, auth=("vguard", "errada")).status_code == 401, path

    r = client.get("/", auth=VIEWER)
    assert r.status_code == 200 and b"V-GUARD" in r.content

    # Antes de qualquer POST /health: campos do dispositivo vazios, sem quebrar.
    h = client.get("/api/health", auth=VIEWER).json()
    assert h["last_contact"] is None and h["pending"] is None and h["last_event"] == 1003.0

    health = {"ts": 1.0, "link": "wifi", "pending": {"telemetry": 0, "events": 0, "photos": 0},
              "storage": {"used": 0.4, "free_bytes": 10}, "temperature": 51.5,
              "cellular_bytes": 1234, "last_contact": None}
    assert client.post("/api/health", json=health, headers=pi).status_code == 200
    h = client.get("/api/health", auth=VIEWER).json()
    assert h["link"] == "wifi" and h["temperature"] == 51.5 and h["storage"]["used"] == 0.4
    assert h["last_contact"] > 1.0          # horário do servidor, não o do pacote

    events = client.get("/api/events", auth=VIEWER).json()
    assert [e["ts"] for e in events] == [1003.0, 1002.5]
    assert events[1]["has_photo"] and not events[1]["photo_pending"]
    assert not events[0]["has_photo"] and not events[0]["photo_pending"]   # evento sem foto
    assert client.get("/api/events?limit=1", auth=VIEWER).json() == events[:1]

    detail = client.get("/api/events/" + event_id, auth=VIEWER).json()
    assert detail["data"]["obd"]["speed"] == 60 and detail["window"][0]["source"] == "obd"
    assert client.get("/api/events/%s" % uuid.uuid4(), auth=VIEWER).status_code == 404

    telemetry = client.get("/api/telemetry", auth=VIEWER).json()
    assert [r["ts"] for r in telemetry] == sorted(r["ts"] for r in telemetry)
    assert len(telemetry) == 6


if __name__ == "__main__":
    with TestClient(app.app) as client:
        pi = reset()
        test_device_auth(client, pi)
        event_id = test_idempotent_ingestion(client, pi)
        test_photo(client, pi, event_id)
        test_dashboard(client, pi, event_id)
    print("OK")
