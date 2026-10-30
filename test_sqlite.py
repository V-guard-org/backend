"""Regressões da persistência SQLite: python3 test_sqlite.py."""

from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = tempfile.TemporaryDirectory(prefix="vguard-sqlite-tests-")
os.environ["DATABASE_PATH"] = str(Path(ROOT.name, "unused.db"))
os.environ["DASHBOARD_PASSWORD"] = "teste"

from fastapi.testclient import TestClient
import app
import database


class SQLiteTests(unittest.TestCase):
    def setUp(self):
        self.contexts = contextlib.ExitStack()
        self.addCleanup(self.contexts.close)
        self.directory = tempfile.TemporaryDirectory(dir=ROOT.name)
        self.contexts.callback(self.directory.cleanup)
        self.path = Path(self.directory.name, "backend.db")
        self.contexts.enter_context(patch.object(app, "DATABASE_PATH", self.path))
        self.client = self.contexts.enter_context(TestClient(app.app))
        self.headers = {"X-Device-Id": "pi", "Authorization": "Bearer " + app.new_device("pi")}

    def test_json_boolean_and_persistence(self):
        event_id = str(uuid.uuid4())
        data = {"texto": "atenção 🚗", "booleano": False, "vazio": None, "lista": [1, 1.25]}
        body = {"type": "distração", "ts": 1000.125, "score": None,
                "data": data, "window": [{"ts": 999.5, "source": "gps", "data": data}]}
        url = "/api/events/" + event_id
        self.assertEqual(self.client.put(url, json=body, headers=self.headers).status_code, 200)
        app.connect()  # Reaplicar o schema não apaga nada.
        result = self.client.get(url, auth=("vguard", "teste")).json()
        for key in ("type", "ts", "score", "data", "window"):
            self.assertEqual(result[key], body[key])
        self.assertIs(result["has_photo"], False)
        self.assertIs(result["photo_pending"], False)
        with database.connection(self.path) as conn:
            row = conn.execute("SELECT data, received_at FROM events").fetchone()
            self.assertIsInstance(row["data"], str)
            self.assertEqual(json.loads(row["data"]), data)
            self.assertTrue(row["received_at"].endswith("Z"))

    def test_constraints_rollback_and_pragmas(self):
        with database.connection(self.path) as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            with app.db() as cur:
                cur.execute("INSERT INTO telemetry VALUES (?, ?, ?)", ("pi", 1, "{}"))
                cur.execute("INSERT INTO telemetry VALUES (?, ?, ?)", ("inexistente", 2, "{}"))
        with app.db() as cur:
            self.assertEqual(cur.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0], 0)
        for invalid in ("texto inválido", "[]"):
            with self.assertRaises(sqlite3.IntegrityError), app.db() as cur:
                cur.execute("INSERT INTO telemetry VALUES (?, ?, ?)", ("pi", 3, invalid))

    def test_busy_returns_503_then_retry_succeeds(self):
        body = {"items": [{"ts": 1, "data": {"rpm": 2000}}]}
        with patch.object(database, "BUSY_TIMEOUT_SECONDS", 0.05), database.connection(self.path) as lock:
            lock.execute("BEGIN IMMEDIATE")
            response = self.client.post("/api/telemetry", json=body, headers=self.headers)
            self.assertEqual(response.status_code, 503)
            lock.rollback()
        for _ in range(2):
            self.assertEqual(self.client.post("/api/telemetry", json=body, headers=self.headers).status_code, 200)
        with app.db() as cur:
            self.assertEqual(cur.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0], 1)

    def test_parallel_batches_are_idempotent(self):
        def send(index):
            body = {"items": [{"ts": n, "data": {"rpm": n}} for n in range(index, index + 20)]}
            return self.client.post("/api/telemetry", json=body, headers=self.headers).status_code
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(send, range(20))), [200] * 20)
        rows = self.client.get("/api/telemetry", auth=("vguard", "teste")).json()
        self.assertEqual([r["ts"] for r in rows], list(range(39)))
        self.assertEqual([r["data"]["rpm"] for r in rows], list(range(39)))
        self.assertEqual(self.client.post("/api/telemetry", json={"items": []}, headers=self.headers).status_code, 200)

    def test_negative_limit_is_rejected(self):
        self.assertEqual(self.client.get("/api/events?limit=-1", auth=("vguard", "teste")).status_code, 422)

    def test_missing_database_is_not_recreated(self):
        missing = self.path.with_name("missing.db")
        with patch.object(app, "DATABASE_PATH", missing):
            self.assertEqual(self.client.post("/api/health", json={}, headers=self.headers).status_code, 503)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
