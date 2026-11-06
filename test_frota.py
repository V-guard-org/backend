"""
Confere a física da frota simulada, sem banco: nenhum carro pode andar mais
num segundo do que a própria velocidade permite (o erro antigo fazia o carro
pular centenas de metros por segundo e o mapa virava uma mancha).

    python3 test_frota.py
"""

import time

import frota_demo as frota


def test_cars_move_continuously():
    now = time.time()
    for k, car in enumerate(frota.CARS):
        if car[8] == "parado":
            continue
        for t in range(int(now), int(now) + 2 * 3600):
            a, b = frota.get_position(k, t), frota.get_position(k, t + 1)
            step = frota.haversine(a[0], a[1], b[0], b[1])
            fastest = max(frota.get_speed(k, t), frota.get_speed(k, t + 1)) / 3.6
            assert step <= fastest + 1, (car[0], t, round(step), fastest)


def test_integral_matches_speed():
    # A integral fechada cresce, a cada segundo, o fator de velocidade daquele trecho.
    now = time.time()
    for k in range(len(frota.CARS)):
        for t in range(int(now), int(now) + 600, 7):
            grew = frota.traffic(k, t + 1)[1] - frota.traffic(k, t)[1]
            assert abs(grew - frota.traffic(k, t + 0.5)[0]) < 0.01, (frota.CARS[k][0], t, grew)


def test_routes_follow_the_streets():
    # Com rotas_demo.json, cada rota tem o traçado das ruas, não só os pontos de passagem em reta.
    for nome, pontos in frota.ROTAS.items():
        assert len(getattr(frota, "ROTA_" + nome.upper())) > 10 * len(pontos), nome


if __name__ == "__main__":
    test_routes_follow_the_streets()
    test_cars_move_continuously()
    test_integral_matches_speed()
    print("OK")
