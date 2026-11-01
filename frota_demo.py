"""
V-GUARD — Frota de demonstração.

Cria carros SIMULADOS (devices.simulated = true) circulando por Curitiba e
grava direto no banco do backend o que a Pi deles enviaria: GPS, OBD-II,
IMU, saúde e, de vez em quando, um evento (sem foto). O carro de verdade
não é tocado; no dashboard, os simulados aparecem com o selo "simulado".

Roda no servidor, ao lado do backend, com o mesmo DATABASE_URL:

    python3 frota_demo.py            # cria os carros e os mantém andando (Ctrl+C para)
    python3 frota_demo.py --apagar   # remove os carros simulados e tudo deles
"""

import math
import random
import secrets
import sys
import time
import uuid

import psycopg2.extras

import app

TICK = 5                # s entre gravações (a telemetria continua a 1 linha/s)
HISTORY = 900           # s de trajeto já percorrido na primeira vez
KEEP = 3 * 3600         # telemetria simulada mais velha que isto é apagada
OFFLINE_FOR = 2 * 3600  # o carro "sem contato" parou de falar há 2 h
PERIOD = 300            # s por volta no circuito
Json = psycopg2.extras.Json

# id, modelo, placa, cor, centro do circuito, eventos por hora, estado
CARS = [
    ("sim-01", "HB20", "BEE4R22", "Branco", (-25.4284, -49.2733), 2, "rodando"),
    ("sim-02", "Onix", "RLV2C19", "Prata", (-25.4420, -49.2870), 6, "rodando"),
    ("sim-03", "Strada", "QTP7H41", "Vermelho", (-25.4530, -49.2800), 1, "rodando"),
    ("sim-04", "Kwid", "SDA9J05", "Azul", (-25.4100, -49.2600), 3, "rodando"),
    ("sim-05", "Corolla", "PXZ3F88", "Preto", (-25.4470, -49.2600), 0, "parado"),
    ("sim-06", "Gol", "MHK1B27", "Cinza", (-25.4350, -49.3000), 0, "sem contato"),
]
IDS = [c[0] for c in CARS]


def sample(k, t):
    """O que os sensores do carro k leem no instante t (função pura: serve
    para o trajeto, para o histórico e para a janela dos eventos)."""
    (lat, lon), state = CARS[k][4], CARS[k][6]
    moving = state != "parado"
    phase = k / len(CARS)
    angle = 2 * math.pi * (t / PERIOD + phase) if moving else 0
    speed = max(0.0, 38 + 18 * math.sin(t / 40 + k)) if moving else 0.0
    throttle = max(0.0, 18 + 12 * math.cos(t / 40 + k)) if moving else 0.0
    return {
        "gps": {"lat": round(lat + 0.006 * math.sin(angle), 6),
                "lon": round(lon + 0.0084 * math.sin(angle) * math.cos(angle), 6),
                "speed": round(speed + 1.2, 1) if moving else 0.0},
        "obd": {"speed": round(speed), "rpm": round(900 + speed * 45) if moving else 850,
                "coolant": round(88 + 2 * math.sin(t / 300 + k)), "load": round(25 + throttle, 1),
                "map": round(35 + throttle * 1.5), "iat": 30, "throttle": round(throttle, 1),
                "voltage": 13.9, "age_ms": {"speed": 80, "rpm": 120, "coolant": 900, "load": 600,
                                            "map": 700, "iat": 950, "throttle": 500, "voltage": 1000}},
        "imu": {"ax": round(18 / 40 / 3.6 * math.cos(t / 40 + k) + 0.2 * math.sin(t * 7), 2) if moving else 0.0,
                "ay": round(1.5 * math.sin(2 * angle), 2),
                "az": round(9.81 + 0.3 * math.sin(t * 11), 2)},
    }


def stamped(k, t):
    return {s: {"ts": t, **v} for s, v in sample(k, t).items()}


def telemetry_rows(k, start, end):
    """Uma linha por segundo inteiro em (start, end], como o EventManager consolida."""
    for t in range(int(start) + 1, int(end) + 1):
        yield CARS[k][0], float(t), Json(stamped(k, t))


def event_row(k, ts):
    data = stamped(k, ts)
    # janela de ±10 s (RF17): OBD a cada 1 s, IMU a cada 0,5 s
    window = [{"ts": ts + i / 2, "source": s, "data": sample(k, ts + i / 2)[s]}
              for i in range(-20, 21) for s in ("obd", "imu") if s == "imu" or i % 2 == 0]
    kind = random.choice(["fadiga", "distracao"])
    return (str(uuid.uuid4()), CARS[k][0], kind, ts, round(random.uniform(0.7, 0.98), 2),
            Json(data), Json(window))


def health_row(k, now):
    state = CARS[k][6]
    return (CARS[k][0], now, Json({
        "ts": now, "last_contact": now,
        "link": "wifi" if state == "parado" else "cellular",
        "pending": {"telemetry": 0, "events": 0, "photos": 0},
        "storage": {"used": 0.18 + 0.07 * k, "free_bytes": int((0.82 - 0.07 * k) * 29e9)},
        "temperature": round(52 + 2 * k + 3 * math.sin(now / 600), 1),
        "cellular_bytes": 0 if state == "parado" else int(2e6 + (now % 86400) * 300),
    }))


def insert(cur, telemetry=(), events=(), health=()):
    psycopg2.extras.execute_values(
        cur, "INSERT INTO telemetry (device_id, ts, data) VALUES %s ON CONFLICT DO NOTHING",
        list(telemetry))
    psycopg2.extras.execute_values(
        cur, "INSERT INTO events (id, device_id, type, ts, score, data, event_window)"
        " VALUES %s", list(events))
    psycopg2.extras.execute_values(
        cur, "INSERT INTO health VALUES %s ON CONFLICT (device_id)"
        " DO UPDATE SET received_at = EXCLUDED.received_at, data = EXCLUDED.data", list(health))


def setup(now):
    """Cadastra os carros. Na primeira vez, dá a cada um um trajeto e alguns eventos;
    nas seguintes, preenche o tempo em que o simulador ficou parado (sem salto no mapa)."""
    with app.db() as cur:
        for k, (device, model, plate, color, _, rate, state) in enumerate(CARS):
            # Token aleatório jogado fora: ninguém envia dados como carro simulado.
            # O WHERE impede marcar como simulado um dispositivo de verdade com o mesmo id.
            cur.execute(
                "INSERT INTO devices (id, token_hash, model, plate, color, simulated)"
                " VALUES (%s, %s, %s, %s, %s, true) ON CONFLICT (id) DO UPDATE SET"
                " model = EXCLUDED.model, plate = EXCLUDED.plate, color = EXCLUDED.color"
                " WHERE devices.simulated",
                (device, app.hash_token(secrets.token_urlsafe(32)), model, plate, color))
            cur.execute("SELECT MAX(ts) AS ts FROM telemetry WHERE device_id = %s", (device,))
            last = cur.fetchone()["ts"]
            end = now - OFFLINE_FOR if state == "sem contato" else now
            if last is None:
                past = [end - random.uniform(0, 6 * 3600) for _ in range(round(rate * 6 * random.random()))]
                insert(cur, telemetry_rows(k, end - HISTORY, end),
                       [event_row(k, ts) for ts in past], [health_row(k, end)])
            elif state != "sem contato":
                insert(cur, telemetry_rows(k, max(last, end - KEEP), end))


def run():
    app.connect()
    last = time.time()
    setup(last)
    print("frota simulada no ar: %s (Ctrl+C para)" % ", ".join(IDS))
    while True:
        time.sleep(TICK)
        now = time.time()
        live = [k for k, car in enumerate(CARS) if car[6] != "sem contato"]
        with app.db() as cur:
            insert(cur,
                   [row for k in live for row in telemetry_rows(k, last, now)],
                   [event_row(k, now) for k in live if random.random() < CARS[k][5] * TICK / 3600],
                   [health_row(k, now) for k in live])
            # Teto de espaço: a frota falsa não cresce sem fim no banco.
            cur.execute("DELETE FROM telemetry WHERE device_id = ANY(%s) AND ts < %s", (IDS, now - KEEP))
            cur.execute("DELETE FROM events WHERE device_id = ANY(%s) AND ts < %s", (IDS, now - 86400))
        last = now


def remove():
    app.connect()
    with app.db() as cur:
        cur.execute("SELECT id FROM devices WHERE simulated")
        ids = [r["id"] for r in cur.fetchall()]
        for table in ("health", "telemetry", "events"):
            cur.execute("DELETE FROM " + table + " WHERE device_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM devices WHERE id = ANY(%s)", (ids,))
    print("carros simulados removidos:", ", ".join(ids) or "nenhum")


if __name__ == "__main__":
    try:
        remove() if sys.argv[1:] == ["--apagar"] else run()
    except KeyboardInterrupt:
        pass
