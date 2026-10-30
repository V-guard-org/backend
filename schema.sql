-- V-GUARD — SQLite. Aplicado pelo app.py ao subir.
-- JSON como texto; datas de auditoria em ISO 8601 UTC; ts permanece epoch em segundos.

CREATE TABLE IF NOT EXISTS devices (
    id         TEXT PRIMARY KEY NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY NOT NULL, -- UUID validado pela API
    device_id      TEXT NOT NULL REFERENCES devices (id),
    type           TEXT NOT NULL,
    ts             REAL NOT NULL,
    score          REAL,
    data           TEXT NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object'),
    event_window   TEXT NOT NULL CHECK (json_valid(event_window) AND json_type(event_window) = 'array'),
    photo          TEXT,
    received_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    photo_expected INTEGER NOT NULL DEFAULT 0 CHECK (photo_expected IN (0, 1))
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts DESC);

-- A Pi pode reaproveitar ids locais: deduplicar por dispositivo e instante.
CREATE TABLE IF NOT EXISTS telemetry (
    device_id TEXT NOT NULL REFERENCES devices (id),
    ts        REAL NOT NULL,
    data      TEXT NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object'),
    PRIMARY KEY (device_id, ts)
);
-- A consulta do dashboard e MAX(ts) não filtram por device_id.
CREATE INDEX IF NOT EXISTS telemetry_ts ON telemetry (ts DESC);

CREATE TABLE IF NOT EXISTS health (
    device_id   TEXT PRIMARY KEY NOT NULL REFERENCES devices (id),
    received_at REAL NOT NULL,
    data        TEXT NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object')
);
