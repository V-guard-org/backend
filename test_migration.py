"""Teste opt-in com PostgreSQL real; cria e remove somente um schema aleatório.

TEST_POSTGRES_DSN='...' python3 test_migration.py
"""

import contextlib
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import database
from migrate_postgres import migrate


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_DSN"), "defina TEST_POSTGRES_DSN para testar a importação real")
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.contexts = contextlib.ExitStack()
        self.addCleanup(self.contexts.close)
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        self.tmp = self.contexts.enter_context(tempfile.TemporaryDirectory(prefix="vguard-migration-"))
        self.output = Path(self.tmp, "backend.db")
        self.photos = Path(self.tmp, "photos")
        self.photos.mkdir()
        self.schema = "migration_test_" + uuid.uuid4().hex
        admin = self.contexts.enter_context(psycopg.connect(os.environ["TEST_POSTGRES_DSN"], autocommit=True))
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.contexts.callback(lambda: admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))))
        self.dsn = make_conninfo(os.environ["TEST_POSTGRES_DSN"], options="-c search_path=" + self.schema)
        self.source = self.contexts.enter_context(psycopg.connect(self.dsn, autocommit=True))
        self.source.execute(Path(__file__).with_name("tests").joinpath("fixtures/postgres.sql").read_text())
        self.event_id = uuid.uuid4()
        self.photo_name = str(self.event_id) + ".jpg"
        (self.photos / self.photo_name).write_bytes(b"\xff\xd8foto de teste\xff\xd9")
        self.source.execute("INSERT INTO devices VALUES (%s, %s, %s)", ("pi", hashlib.sha256(b"token-anterior").hexdigest(), "2026-10-30T12:00:00.123456-03:00"))
        self.data = {"texto": "atenção 🚗", "booleano": False, "vazio": None}
        self.source.execute("""INSERT INTO events
            (id, device_id, type, ts, score, data, event_window, photo, photo_expected)
            VALUES (%s, 'pi', 'fadiga', 1000.125, NULL, %s, '[]', %s, true)""",
            (self.event_id, json.dumps(self.data), self.photo_name))
        with self.source.cursor() as cur:
            cur.executemany("INSERT INTO telemetry VALUES ('pi', %s, %s)",
                            [(n + 0.125, json.dumps(self.data)) for n in range(1005)])
        self.source.execute("INSERT INTO health VALUES ('pi', 2000.5, %s)", (json.dumps({"link": "wifi"}),))

    def test_complete_import_and_no_overwrite(self):
        counts = migrate(self.dsn, self.output, self.photos)
        self.assertEqual(counts, {"devices": 1, "events": 1, "telemetry": 1005, "health": 1})
        database.initialize(self.output)
        with database.connection(self.output) as conn:
            device = conn.execute("SELECT * FROM devices").fetchone()
            self.assertEqual(device["token_hash"], hashlib.sha256(b"token-anterior").hexdigest())
            self.assertEqual(device["created_at"], "2026-10-30T15:00:00.123456Z")
            event = conn.execute("SELECT * FROM events").fetchone()
            self.assertEqual(event["id"], str(self.event_id))
            self.assertEqual(json.loads(event["data"]), self.data)
            self.assertEqual(event["photo_expected"], 1)
            self.assertIsNone(event["score"])
            self.assertEqual(event["ts"], 1000.125)
            self.assertEqual(json.loads(event["event_window"]), [])
            self.assertEqual(event["photo"], self.photo_name)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with patch.dict(os.environ, {"DATABASE_PATH": str(self.output), "DASHBOARD_PASSWORD": "teste"}):
            import app
            from fastapi.testclient import TestClient
        with patch.object(app, "DATABASE_PATH", self.output), patch.object(app, "PHOTOS_DIR", str(self.photos)):
            with TestClient(app.app) as client:
                headers = {"X-Device-Id": "pi", "Authorization": "Bearer token-anterior"}
                self.assertEqual(client.post("/api/health", json={"link": "wifi"}, headers=headers).status_code, 200)
                url = "/api/events/" + str(self.event_id)
                event = client.get(url, auth=("vguard", "teste")).json()
                self.assertEqual(event["data"], self.data)
                self.assertIs(event["has_photo"], True)
                self.assertIs(event["photo_pending"], False)
                self.assertEqual(client.get(url + "/photo", auth=("vguard", "teste")).content,
                                 (self.photos / self.photo_name).read_bytes())
                rows = client.get("/api/telemetry", auth=("vguard", "teste")).json()
                self.assertEqual(rows, [{"ts": n + 0.125, "data": self.data} for n in range(1005)])
        with self.assertRaises(FileExistsError):
            migrate(self.dsn, self.output, self.photos)
        self.assertEqual(self.source.execute("SELECT count(*) FROM telemetry").fetchone()[0], 1005)

    def test_missing_photo_does_not_publish_partial_database(self):
        (self.photos / self.photo_name).unlink()
        with self.assertRaises(ValueError):
            migrate(self.dsn, self.output, self.photos)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(Path(self.tmp).glob(".vguard-import-*")), [])

    def test_invalid_json_shape_rolls_back(self):
        self.source.execute("UPDATE telemetry SET data = '[]'::jsonb WHERE ts = 1004.125")
        with self.assertRaises(sqlite3.IntegrityError):
            migrate(self.dsn, self.output, self.photos)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(Path(self.tmp).glob(".vguard-import-*")), [])


if __name__ == "__main__":
    unittest.main()
