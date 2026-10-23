-- V-GUARD — Modelo do backend (PostgreSQL). Aplicado pelo app.py ao subir.

CREATE TABLE IF NOT EXISTS devices (
    id         TEXT PRIMARY KEY,           -- X-Device-Id (VGUARD_DEVICE na Pi)
    token_hash TEXT NOT NULL,              -- sha256 do token; o token em si não fica no banco
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Um evento por UUID gerado na Pi (RF12): o reenvio cai no mesmo id.
CREATE TABLE IF NOT EXISTS events (
    id           UUID PRIMARY KEY,
    device_id    TEXT NOT NULL REFERENCES devices (id),
    type         TEXT NOT NULL,
    ts           DOUBLE PRECISION NOT NULL,
    score        DOUBLE PRECISION,
    data         JSONB NOT NULL,           -- última amostra de cada sensor no instante do evento
    event_window JSONB NOT NULL,           -- amostras de ±10 s (RF17)
    photo        TEXT,                     -- arquivo em PHOTOS_DIR; NULL até chegar pelo Wi-Fi
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts DESC);

-- Chave pelo ts, não pelo id local da Pi: o SQLite da Pi reaproveita ids
-- quando a limpeza esvazia a tabela, e o reenvio seria descartado.
CREATE TABLE IF NOT EXISTS telemetry (
    device_id TEXT NOT NULL REFERENCES devices (id),
    ts        DOUBLE PRECISION NOT NULL,
    data      JSONB NOT NULL,
    PRIMARY KEY (device_id, ts)
);

-- Só o último POST /health de cada dispositivo (RF32).
CREATE TABLE IF NOT EXISTS health (
    device_id   TEXT PRIMARY KEY REFERENCES devices (id),
    received_at DOUBLE PRECISION NOT NULL,  -- quando o servidor recebeu: "última comunicação"
    data        JSONB NOT NULL
);
