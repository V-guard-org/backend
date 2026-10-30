"""
V-GUARD — Backend (FastAPI + PostgreSQL).

Recebe o que o Sync da Raspberry Pi envia e serve o dashboard:

    Raspberry Pi (Sync)              Dashboard (navegador)
    POST /api/health                 GET /api/health
    POST /api/telemetry              GET /api/telemetry
    PUT  /api/events/{id}            GET /api/events, /api/events/{id}
    PUT  /api/events/{id}/photo      GET /api/events/{id}/photo
                                     GET /  (index.html do dashboard)

Contratos: integracao/events/sync.py (envio) e dashboard/README.md (consulta).
Documentação OpenAPI em /docs.

    uvicorn app:app                              # servidor
    python3 app.py novo-dispositivo vguard-01    # gera o token de um dispositivo
"""

import contextlib
import hashlib
import hmac
import os
import secrets
import sys
import time
import uuid
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool
from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))

DATABASE_URL = os.environ.get("DATABASE_URL", "dbname=vguard")
PHOTOS_DIR = os.environ.get("PHOTOS_DIR", os.path.join(HERE, "fotos"))
DASHBOARD_DIR = os.environ.get("DASHBOARD_DIR", os.path.join(HERE, "..", "dashboard"))
# Sem senha definida, o dashboard fica fechado: o servidor expõe fotos de motoristas.
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "vguard")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

# Uma foto VGA em JPEG tem ~50 kB; acima disto é erro, não foto.
MAX_PHOTO_BYTES = 5 * 1024 * 1024
# Teto de linhas por consulta (1 linha/s = ~3 h).
TELEMETRY_LIMIT = 10000


# ============================================================
# BANCO
# ============================================================

pool = None


def connect():
    global pool
    pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
    with db() as cur, open(os.path.join(HERE, "schema.sql")) as f:
        cur.execute(f.read())


@contextlib.contextmanager
def db():
    """Cursor numa transação: commit ao sair, rollback se der erro."""
    conn = pool.getconn()
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        pool.putconn(conn)


def hash_token(token):
    # Token aleatório de 256 bits: sha256 basta, não precisa de hash lento.
    return hashlib.sha256(token.encode()).hexdigest()


def new_device(device_id):
    """Cria (ou troca o token de) um dispositivo. Devolve o token, que só aparece aqui."""
    token = secrets.token_urlsafe(32)
    with db() as cur:
        cur.execute(
            "INSERT INTO devices (id, token_hash) VALUES (%s, %s)"
            " ON CONFLICT (id) DO UPDATE SET token_hash = EXCLUDED.token_hash",
            (device_id, hash_token(token))
        )
    return token


@contextlib.asynccontextmanager
async def lifespan(app):
    connect()
    yield
    pool.closeall()


app = FastAPI(title="V-GUARD", lifespan=lifespan)


@app.exception_handler(psycopg2.Error)
def database_down(request, error):
    # 503 = o Sync tenta de novo mais tarde; nada sai da fila da Pi.
    return JSONResponse({"detail": "banco indisponível"}, status_code=503)


# ============================================================
# AUTENTICAÇÃO
# ============================================================

def device(authorization: str = Header(""), x_device_id: str = Header("")):
    """Dispositivo dono do token. 401 faz o Sync tentar de novo, sem perder nada."""
    with db() as cur:
        cur.execute("SELECT token_hash FROM devices WHERE id = %s", (x_device_id,))
        row = cur.fetchone()

    token = authorization.removeprefix("Bearer ")
    if not row or not hmac.compare_digest(row["token_hash"], hash_token(token)):
        raise HTTPException(401, "dispositivo ou token inválido")
    return x_device_id


basic = HTTPBasic()


def viewer(credentials: HTTPBasicCredentials = Depends(basic)):
    if not DASHBOARD_PASSWORD:
        raise HTTPException(503, "defina DASHBOARD_PASSWORD para abrir o dashboard")
    ok = hmac.compare_digest(credentials.username.encode(), DASHBOARD_USER.encode())
    ok &= hmac.compare_digest(credentials.password.encode(), DASHBOARD_PASSWORD.encode())
    if not ok:
        raise HTTPException(401, "usuário ou senha inválidos", {"WWW-Authenticate": "Basic"})


# ============================================================
# RECEBIMENTO (Sync da Raspberry Pi)
# ============================================================

# Modelos tolerantes: campos extras são aceitos e ignorados. O Sync trata
# 400/413/422 como recusa definitiva (o item sai da fila e fica só no SD),
# então um campo novo na Pi não pode fazer o servidor recusar dados.

class TelemetryRow(BaseModel):
    ts: float
    data: dict


class TelemetryBatch(BaseModel):
    items: list[TelemetryRow]


class Event(BaseModel):
    type: str           # "fadiga", "distracao"; tipos novos (RF34, RF35) passam
    ts: float
    score: Optional[float] = None
    data: dict = {}
    has_photo: bool = False   # a foto existe na Pi, mesmo que ainda não tenha chegado
    window: list = []


@app.post("/api/health")
def receive_health(data: dict = Body(...), device_id: str = Depends(device)):
    with db() as cur:
        cur.execute(
            "INSERT INTO health VALUES (%s, %s, %s) ON CONFLICT (device_id)"
            " DO UPDATE SET received_at = EXCLUDED.received_at, data = EXCLUDED.data",
            (device_id, time.time(), psycopg2.extras.Json(data))
        )
    return {"ok": True}


@app.post("/api/telemetry")
def receive_telemetry(batch: TelemetryBatch, device_id: str = Depends(device)):
    # Reenvio de um lote já gravado (confirmação perdida) não duplica nada.
    with db() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO telemetry (device_id, ts, data) VALUES %s ON CONFLICT DO NOTHING",
            [(device_id, row.ts, psycopg2.extras.Json(row.data)) for row in batch.items]
        )
    return {"ok": True, "items": len(batch.items)}


@app.put("/api/events/{event_id}")
def receive_event(event_id: uuid.UUID, event: Event, device_id: str = Depends(device)):
    with db() as cur:
        cur.execute(
            "INSERT INTO events (id, device_id, type, ts, score, data, event_window, photo_expected)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (str(event_id), device_id, event.type, event.ts, event.score,
             psycopg2.extras.Json(event.data), psycopg2.extras.Json(event.window),
             event.has_photo)
        )
    return {"ok": True}


@app.put("/api/events/{event_id}/photo")
def receive_photo(
    event_id: uuid.UUID,
    jpeg: bytes = Body(..., media_type="image/jpeg"),
    device_id: str = Depends(device),
):
    if len(jpeg) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "foto grande demais")
    if not jpeg.startswith(b"\xff\xd8"):
        raise HTTPException(400, "não é um JPEG")

    with db() as cur:
        cur.execute(
            "SELECT 1 FROM events WHERE id = %s AND device_id = %s",
            (str(event_id), device_id)
        )
        if not cur.fetchone():
            # O Sync só manda a foto depois dos metadados; se chegou antes,
            # 409 faz ele tentar de novo no próximo ciclo.
            raise HTTPException(409, "evento ainda não recebido")

    # Arquivo antes da linha no banco: o banco nunca aponta para foto inexistente.
    name = "%s.jpg" % event_id
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    tmp = os.path.join(PHOTOS_DIR, name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(jpeg)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(PHOTOS_DIR, name))

    with db() as cur:
        cur.execute("UPDATE events SET photo = %s WHERE id = %s", (name, str(event_id)))
    return {"ok": True}


# ============================================================
# CONSULTA (dashboard) — rotas descritas no README do dashboard
# ============================================================

# ponytail: um dispositivo só; com a frota, as rotas ganham ?device= ou
# /api/devices/<id>/... e as consultas filtram por device_id.

def event_json(row):
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "ts": row["ts"],
        "score": row["score"],
        "data": row["data"],
        "has_photo": row["photo"] is not None,
        # foto tirada, mas ainda no cartão da Pi esperando o envio
        "photo_pending": row["photo_expected"] and row["photo"] is None,
    }


@app.get("/api/health", dependencies=[Depends(viewer)])
def health():
    with db() as cur:
        cur.execute("SELECT received_at, data FROM health ORDER BY received_at DESC LIMIT 1")
        row = cur.fetchone()
        cur.execute(
            "SELECT (SELECT MAX(ts) FROM events) AS event,"
            " (SELECT MAX(ts) FROM telemetry) AS telemetry"
        )
        last = cur.fetchone()

    h = row["data"] if row else {}
    return {
        "ts": time.time(),
        "pending": h.get("pending"),        # o que ainda está no cartão da Pi
        "storage": h.get("storage"),
        "last_event": last["event"],
        "last_telemetry": last["telemetry"],
        "link": h.get("link"),
        "temperature": h.get("temperature"),
        "cellular_bytes": h.get("cellular_bytes"),
        "last_contact": row["received_at"] if row else None,
    }


@app.get("/api/events", dependencies=[Depends(viewer)])
def events(limit: int = 200):
    with db() as cur:
        cur.execute("SELECT * FROM events ORDER BY ts DESC LIMIT %s", (limit,))
        return [event_json(r) for r in cur.fetchall()]


@app.get("/api/events/{event_id}", dependencies=[Depends(viewer)])
def event(event_id: uuid.UUID):
    with db() as cur:
        cur.execute("SELECT * FROM events WHERE id = %s", (str(event_id),))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "evento inexistente")
    return {**event_json(row), "window": row["event_window"]}


@app.get("/api/events/{event_id}/photo", dependencies=[Depends(viewer)])
def photo(event_id: uuid.UUID):
    with db() as cur:
        cur.execute("SELECT photo FROM events WHERE id = %s", (str(event_id),))
        row = cur.fetchone()
    # O nome do arquivo vem do banco, nunca da URL.
    path = row and row["photo"] and os.path.join(PHOTOS_DIR, row["photo"])
    if not path or not os.path.exists(path):
        raise HTTPException(404, "foto ainda não recebida")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/telemetry", dependencies=[Depends(viewer)])
def telemetry():
    with db() as cur:
        cur.execute(
            "SELECT ts, data FROM telemetry ORDER BY ts DESC LIMIT %s",
            (TELEMETRY_LIMIT,)
        )
        return list(reversed(cur.fetchall()))


@app.get("/", dependencies=[Depends(viewer)])
def dashboard():
    # no-store: uma versão nova do dashboard chega sem precisar limpar o cache.
    return FileResponse(os.path.join(DASHBOARD_DIR, "index.html"), headers={"Cache-Control": "no-store"})


# ============================================================

if __name__ == "__main__":
    if sys.argv[1:2] != ["novo-dispositivo"] or len(sys.argv) != 3:
        sys.exit("uso: python3 app.py novo-dispositivo <id>")
    connect()
    token = new_device(sys.argv[2])
    print("Dispositivo %s cadastrado. Na Raspberry Pi:\n" % sys.argv[2])
    print("    export VGUARD_DEVICE=%s" % sys.argv[2])
    print("    export VGUARD_TOKEN=%s\n" % token)
    print("O token não fica guardado no servidor: anote agora.")
