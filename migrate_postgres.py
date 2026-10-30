"""Importa uma fotografia consistente do PostgreSQL para um SQLite novo.

POSTGRES_DSN='dbname=vguard' python3 migrate_postgres.py --output vguard.db --photos-dir fotos
Instale requirements-migration.txt apenas no ambiente que executa a importação.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid

import database

# Identificadores fixos: nenhum nome de tabela/coluna vem da linha de comando.
TABLES = {
    "devices": ("id", "token_hash", "created_at"),
    "events": ("id", "device_id", "type", "ts", "score", "data", "event_window",
               "photo", "received_at", "photo_expected"),
    "telemetry": ("device_id", "ts", "data"),
    "health": ("device_id", "received_at", "data"),
}


def convert(column, value):
    if column in ("data", "event_window"):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, bool):
        return int(value)
    return value


def migrate(dsn, output, photos_dir):
    import psycopg  # A API não depende de nenhum driver PostgreSQL.

    output = database.resolve_path(output)
    photos_dir = Path(photos_dir).resolve()
    if any(Path(str(output) + suffix).exists() for suffix in ("", "-wal", "-shm", "-journal")):
        raise FileExistsError("Destino já existe; escolha um arquivo novo: " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".vguard-import-", suffix=".db", dir=output.parent)
    os.close(fd)
    staging = Path(filename)
    counts = {}
    try:
        # Sem WAL no arquivo intermediário: ao fechar, todos os dados estão no .db.
        database.initialize(staging, wal=False)
        with psycopg.connect(dsn) as source, database.transaction(staging) as dest:
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for table, columns in TABLES.items():
                names = ", ".join(columns)
                placeholders = ", ".join("?" for _ in columns)
                counts[table] = 0
                with source.cursor(name="export_" + table) as cursor:
                    cursor.execute(f"SELECT {names} FROM {table}")
                    while rows := cursor.fetchmany(1000):
                        batch = []
                        for row in rows:
                            values = tuple(convert(c, v) for c, v in zip(columns, row))
                            if table == "events":
                                photo = values[columns.index("photo")]
                                if photo is not None:
                                    path = (photos_dir / photo).resolve()
                                    if path.parent != photos_dir or not path.is_file():
                                        raise ValueError("Foto referenciada ausente ou fora da pasta: " + photo)
                            batch.append(values)
                        dest.executemany(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", batch)
                        counts[table] += len(batch)
                expected = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                actual = dest.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if expected != actual or actual != counts[table]:
                    raise ValueError("Contagem divergente: " + table)
            if dest.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Importação contém vínculos inválidos")
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Importação falhou na verificação de integridade")
        # Publicação atômica sem sobrescrever um destino criado por outro processo.
        os.link(staging, output)
        return counts
    finally:
        staging.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="SQLite novo; nunca sobrescreve um arquivo")
    parser.add_argument("--photos-dir", required=True, help="Pasta com as fotos já copiadas/preservadas")
    args = parser.parse_args()
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        parser.error("defina POSTGRES_DSN (formato libpq ou URL PostgreSQL)")
    try:
        counts = migrate(dsn, args.output, args.photos_dir)
    except ImportError:
        parser.exit(1, "Instale: pip install -r requirements-migration.txt\n")
    # Não imprimir DSN, credenciais, hashes ou conteúdo dos registros.
    print("Importação concluída:", json.dumps(counts))
    print("SQLite:", database.resolve_path(args.output))


if __name__ == "__main__":
    main()
