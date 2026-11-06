"""
V-GUARD — Frota de demonstração com rotas reais.

Cria carros SIMULADOS (devices.simulated = true) circulando por Curitiba e
grava direto no banco do backend o que a Pi deles enviaria: GPS, OBD-II,
IMU, saúde e, de vez em quando, um evento (sem foto). O carro de verdade
não é tocado; no dashboard, os simulados aparecem com o selo "simulado".

Rotas:
  sim-01: UTFPR ↔ UFPR (Praça Santos Andrade) — trajeto diário urbano
  sim-02: Loop pelo bairro Batel — tráfego intenso, velocidade baixa
  sim-03: BR-116 em alta velocidade — trecho norte-sul
  sim-04: Rota turística pelo Parque Barigui — velocidade moderada
  sim-05: Parado (Corolla preto)
  sim-06: Sem contato há 2 h (Gol cinza)

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

TICK = 5          # s entre gravações (a telemetria continua a 1 linha/s)
HISTORY = 900     # s de trajeto já percorrido na primeira vez
KEEP = 3 * 3600   # telemetria simulada mais velha que isto é apagada
OFFLINE_FOR = 2 * 3600  # o carro "sem contato" parou de falar há 2 h

Json = psycopg2.extras.Json


# ──────────────────────────────────────────────────────────────────────────────
# ROTAS: listas de (lat, lon) que os carros percorrem
# ──────────────────────────────────────────────────────────────────────────────

# sim-01: UTFPR (Av. Sete de Setembro) ↔ UFPR Politécnico (Jardim Botânico)
# Trajeto diário urbano pelas avenidas Sete de Setembro e Pres. Affonso Camargo
ROTA_UTFPR_UFPR = [
    (-25.4395, -49.2687),  # UTFPR — Av. Sete de Setembro, 3165 (ponto inicial)
    (-25.4393, -49.2675),  # Segue leste na Sete de Setembro
    (-25.4392, -49.2660),  # Passa pela Vila Capanema / Mercado Municipal
    (-25.4391, -49.2645),  # Aproximação do Centro Cívico
    (-25.4395, -49.2620),  # Continuação pela Sete de Setembro
    (-25.4400, -49.2600),  # Embarque na Av. Presidente Affonso Camargo
    (-25.4410, -49.2580),  # Segue sentido Jardim Botânico
    (-25.4420, -49.2560),  # Trecho central da Affonso Camargo
    (-25.4430, -49.2540),  # Continua leste
    (-25.4440, -49.2520),  # Aproximação do Jardim Botânico
    (-25.4450, -49.2500),  # Região do Horto Municipal
    (-25.4455, -49.2485),  # Vira na Av. Pref. Omar Gonçalves Sabbag
    (-25.4458, -49.2470),  # UFPR Politécnico (SEPT) — ponto final
]

# sim-02: Loop pelo Batel (tráfego intenso)
ROTA_BATEL = [
    (-25.4350, -49.2790),  # Norte do Batel
    (-25.4340, -49.2810),  # Al. Dr. Carlos de Carvalho
    (-25.4335, -49.2835),  # Continua
    (-25.4340, -49.2860),  # Rua Comendador Araújo
    (-25.4355, -49.2875),  # Sudoeste do Batel
    (-25.4375, -49.2880),  # Av. do Batel
    (-25.4390, -49.2870),  # Sudeste
    (-25.4395, -49.2845),  # Rua Brigadeiro Franco
    (-25.4390, -49.2820),  # Volta ao centro
    (-25.4380, -49.2800),  # Nordeste
    (-25.4365, -49.2790),  # Fecha o loop
    (-25.4350, -49.2790),  # Ponto inicial
]

# sim-03: BR-116 (Linha Verde / Contorno Sul) em alta velocidade
ROTA_BR116 = [
    (-25.4050, -49.2550),  # Norte - região do Atuba
    (-25.4150, -49.2570),  # Segue sul
    (-25.4250, -49.2590),  # Linha Verde norte
    (-25.4350, -49.2610),  # Centro-sul
    (-25.4450, -49.2630),  # Região do Portão
    (-25.4550, -49.2650),  # Contorno sul
    (-25.4650, -49.2670),  # Sul
    (-25.4750, -49.2690),  # Aproxima de São José dos Pinhais
    (-25.4850, -49.2710),  # Sul da BR-116
    (-25.4950, -49.2730),  # Extremo sul
]

# sim-04: Loop pelo Parque Barigui (rota turística)
ROTA_BARIGUI = [
    (-25.4250, -49.2920),  # Nordeste do parque
    (-25.4235, -49.2945),  # Norte
    (-25.4230, -49.2975),  # Noroeste
    (-25.4240, -49.3000),  # Oeste
    (-25.4260, -49.3015),  # Sudoeste
    (-25.4285, -49.3020),  # Sul
    (-25.4310, -49.3010),  # Sudeste
    (-25.4325, -49.2985),  # Leste-sul
    (-25.4320, -49.2955),  # Leste
    (-25.4305, -49.2930),  # Nordeste
    (-25.4280, -49.2915),  # Fecha norte
    (-25.4250, -49.2920),  # Ponto inicial
]


# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DOS CARROS
# ──────────────────────────────────────────────────────────────────────────────

CARS = [
    # id, modelo, placa, cor, rota, velocidade_base (km/h), eventos/h, estado, modo
    ("sim-01", "HB20", "BEE4R22", "Branco", ROTA_UTFPR_UFPR, 45, 2, "rodando", "idavolta"),
    ("sim-02", "Onix", "RLV2C19", "Prata", ROTA_BATEL, 25, 6, "rodando", "loop"),
    ("sim-03", "Strada", "QTP7H41", "Vermelho", ROTA_BR116, 100, 1, "rodando", "idavolta"),
    ("sim-04", "Kwid", "SDA9J05", "Azul", ROTA_BARIGUI, 30, 3, "rodando", "loop"),
    ("sim-05", "Corolla", "PXZ3F88", "Preto", None, 0, 0, "parado", "parado"),
    ("sim-06", "Gol", "MHK1B27", "Cinza", None, 0, 0, "sem contato", "parado"),
]

IDS = [c[0] for c in CARS]


# ──────────────────────────────────────────────────────────────────────────────
# INTERPOLAÇÃO DE ROTAS
# ──────────────────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    """Distância em metros entre dois pontos GPS."""
    R = 6371000  # raio da Terra em metros
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def precompute_route(waypoints):
    """Calcula distâncias acumuladas ao longo da rota."""
    dists = [0.0]
    for i in range(1, len(waypoints)):
        d = haversine(waypoints[i-1][0], waypoints[i-1][1],
                      waypoints[i][0], waypoints[i][1])
        dists.append(dists[-1] + d)
    return dists


def interpolate_position(waypoints, cum_dists, distance):
    """Dada uma distância percorrida, retorna (lat, lon) interpolado."""
    total = cum_dists[-1]
    if total == 0:
        return waypoints[0]

    # Limita a distância ao total
    distance = distance % total if total > 0 else 0

    # Encontra o segmento
    for i in range(1, len(cum_dists)):
        if distance <= cum_dists[i]:
            seg_start = cum_dists[i - 1]
            seg_end = cum_dists[i]
            seg_len = seg_end - seg_start
            if seg_len == 0:
                return waypoints[i]
            frac = (distance - seg_start) / seg_len
            lat = waypoints[i-1][0] + frac * (waypoints[i][0] - waypoints[i-1][0])
            lon = waypoints[i-1][1] + frac * (waypoints[i][1] - waypoints[i-1][1])
            return (lat, lon)

    return waypoints[-1]


def get_position(car_index, t):
    """Calcula posição do carro no instante t (função pura)."""
    car = CARS[car_index]
    route = car[4]
    speed_kmh = car[5]
    mode = car[8]

    if route is None or mode == "parado":
        # Carro parado: posição fixa
        if car[0] == "sim-05":
            return (-25.4470, -49.2600)  # Estacionamento UTFPR
        else:
            return (-25.4350, -49.3000)  # Garagem

    # Converte velocidade para m/s
    speed_ms = speed_kmh / 3.6

    # Adiciona variação de velocidade (tráfego)
    variation = 0.7 + 0.3 * math.sin(t / 30 + car_index * 2)
    if car[0] == "sim-02":  # Batel: mais variação (semáforos)
        variation = 0.4 + 0.6 * abs(math.sin(t / 20 + car_index))
    elif car[0] == "sim-03":  # BR-116: mais constante
        variation = 0.9 + 0.1 * math.sin(t / 60)

    effective_speed = speed_ms * variation
    distance = effective_speed * t

    # Pré-computa distâncias da rota (cache simples)
    cum_dists = precompute_route(route)
    total_dist = cum_dists[-1]

    if mode == "loop":
        # Volta contínua
        pos = interpolate_position(route, cum_dists, distance)
    elif mode == "idavolta":
        # Ida e volta (ping-pong)
        cycle = total_dist * 2
        dist_in_cycle = distance % cycle
        if dist_in_cycle <= total_dist:
            pos = interpolate_position(route, cum_dists, dist_in_cycle)
        else:
            pos = interpolate_position(route, cum_dists, cycle - dist_in_cycle)
    else:
        pos = route[0]

    return pos


def get_speed(car_index, t):
    """Velocidade instantânea em km/h."""
    car = CARS[car_index]
    speed_kmh = car[5]
    mode = car[8]

    if mode == "parado" or speed_kmh == 0:
        return 0.0

    variation = 0.7 + 0.3 * math.sin(t / 30 + car_index * 2)
    if car[0] == "sim-02":
        variation = 0.4 + 0.6 * abs(math.sin(t / 20 + car_index))
    elif car[0] == "sim-03":
        variation = 0.9 + 0.1 * math.sin(t / 60)

    return round(speed_kmh * variation, 1)


# ──────────────────────────────────────────────────────────────────────────────
# GERAÇÃO DE TELEMETRIA
# ──────────────────────────────────────────────────────────────────────────────

def sample(k, t):
    """O que os sensores do carro k leem no instante t (função pura)."""
    car = CARS[k]
    state = car[7]
    moving = state == "rodando"

    lat, lon = get_position(k, t)
    speed = get_speed(k, t)

    # OBD-II
    rpm = round(900 + speed * 45) if moving else 850
    throttle = max(0.0, 18 + 12 * math.cos(t / 40 + k)) if moving else 0.0
    coolant = round(88 + 2 * math.sin(t / 300 + k))
    load = round(25 + throttle, 1)

    # IMU
    if moving:
        ax = round(0.5 * math.cos(t / 40 + k) + 0.2 * math.sin(t * 7), 2)
        ay = round(0.8 * math.sin(t / 15 + k), 2)
        az = round(9.81 + 0.3 * math.sin(t * 11), 2)
    else:
        ax, ay, az = 0.0, 0.0, 9.81

    return {
        "gps": {"lat": round(lat, 6), "lon": round(lon, 6), "speed": speed},
        "obd": {
            "speed": round(speed), "rpm": rpm, "coolant": coolant,
            "load": load, "map": round(35 + throttle * 1.5),
            "iat": 30, "throttle": round(throttle, 1), "voltage": 13.9,
            "age_ms": {"speed": 80, "rpm": 120, "coolant": 900, "load": 600,
                       "map": 700, "iat": 950, "throttle": 500, "voltage": 1000}
        },
        "imu": {"ax": ax, "ay": ay, "az": az},
    }


def stamped(k, t):
    return {s: {"ts": t, **v} for s, v in sample(k, t).items()}


def telemetry_rows(k, start, end):
    """Uma linha por segundo inteiro em (start, end], como o EventManager consolida."""
    for t in range(int(start) + 1, int(end) + 1):
        yield CARS[k][0], float(t), Json(stamped(k, t))


def event_row(k, ts):
    data = stamped(k, ts)
    window = [{"ts": ts + i / 2, "source": s, "data": sample(k, ts + i / 2)[s]}
              for i in range(-20, 21) for s in ("obd", "imu") if s == "imu" or i % 2 == 0]
    kind = random.choice(["fadiga", "distracao"])
    return (str(uuid.uuid4()), CARS[k][0], kind, ts, round(random.uniform(0.7, 0.98), 2),
            Json(data), Json(window))


def health_row(k, now):
    state = CARS[k][7]
    return (CARS[k][0], now, Json({
        "ts": now, "last_contact": now,
        "link": "wifi" if state == "parado" else "cellular",
        "pending": {"telemetry": 0, "events": 0, "photos": 0},
        "storage": {"used": 0.18 + 0.07 * k, "free_bytes": int((0.82 - 0.07 * k) * 29e9)},
        "temperature": round(52 + 2 * k + 3 * math.sin(now / 600), 1),
        "cellular_bytes": 0 if state == "parado" else int(2e6 + (now % 86400) * 300),
    }))


# ──────────────────────────────────────────────────────────────────────────────
# BANCO DE DADOS
# ──────────────────────────────────────────────────────────────────────────────

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
    """Cria os dispositivos simulados e o histórico inicial."""
    with app.db() as cur:
        for k, car in enumerate(CARS):
            dev_id, model, plate, color = car[0], car[1], car[2], car[3]
            token_hash = secrets.token_hex(16)  # token descartável
            cur.execute(
                "INSERT INTO devices (id, model, plate, color, simulated, token_hash)"
                " VALUES (%s, %s, %s, %s, true, %s)"
                " ON CONFLICT (id) DO UPDATE SET model=%s, plate=%s, color=%s, simulated=true",
                (dev_id, model, plate, color, token_hash, model, plate, color))

            # Histórico inicial para o carro não "nascer" do zero
            if car[7] == "rodando":
                start = now - HISTORY
                rows = list(telemetry_rows(k, start, now))
                insert(cur, telemetry=rows)
                # Eventos passados
                for _ in range(random.randint(1, 3)):
                    ets = random.uniform(start, now)
                    insert(cur, events=[event_row(k, ets)])

            # Saúde
            if car[7] != "sem contato":
                insert(cur, health=[health_row(k, now)])
            else:
                # Sem contato: última comunicação há OFFLINE_FOR
                old = now - OFFLINE_FOR
                cur.execute(
                    "INSERT INTO health VALUES (%s, %s, %s)"
                    " ON CONFLICT (device_id) DO UPDATE SET received_at=%s, data=%s",
                    (dev_id, old, Json({"ts": old, "last_contact": old, "link": "cellular",
                                        "pending": {"telemetry": 0, "events": 0, "photos": 0},
                                        "storage": {"used": 0.5, "free_bytes": int(0.5 * 29e9)},
                                        "temperature": 35.0, "cellular_bytes": 0}), old))


def erase():
    """Remove todos os dados simulados."""
    with app.db() as cur:
        cur.execute("DELETE FROM telemetry WHERE device_id = ANY(%s)", (IDS,))
        cur.execute("DELETE FROM events WHERE device_id = ANY(%s)", (IDS,))
        cur.execute("DELETE FROM health WHERE device_id = ANY(%s)", (IDS,))
        cur.execute("DELETE FROM devices WHERE id = ANY(%s)", (IDS,))
    print("frota simulada apagada.")


def run():
    app.connect()
    last = time.time()
    setup(last)
    print("frota simulada no ar (rotas reais): %s (Ctrl+C para)" % ", ".join(IDS))
    print("  sim-01: UTFPR ↔ UFPR (%.0f km/h)" % CARS[0][5])
    print("  sim-02: Loop Batel (%.0f km/h)" % CARS[1][5])
    print("  sim-03: BR-116 (%.0f km/h)" % CARS[2][5])
    print("  sim-04: Parque Barigui (%.0f km/h)" % CARS[3][5])
    print("  sim-05: parado | sim-06: sem contato")

    while True:
        time.sleep(TICK)
        now = time.time()
        live = [k for k, car in enumerate(CARS) if car[7] != "sem contato"]
        with app.db() as cur:
            insert(cur,
                   [row for k in live for row in telemetry_rows(k, last, now)],
                   [event_row(k, now) for k in live if random.random() < CARS[k][6] * TICK / 3600],
                   [health_row(k, now) for k in live])
            # Teto de espaço: a frota falsa não cresce sem fim no banco.
            cur.execute("DELETE FROM telemetry WHERE device_id = ANY(%s) AND ts < %s", (IDS, now - KEEP))
            cur.execute("DELETE FROM events WHERE device_id = ANY(%s) AND ts < %s", (IDS, now - 86400))
        last = now


if __name__ == "__main__":
    if "--apagar" in sys.argv:
        app.connect()
        erase()
    else:
        run()