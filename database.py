"""SQLite schema, connection helper and reference-data seeding.

The database is created automatically on first run, so a fresh clone needs no
pre-built ``.db`` file. Reference tables (reason codes and per-bank code
mappings) are seeded from the CSV files in ``data/``.

Set ``NACH_DB_PATH`` to store the database somewhere else.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DEFAULT_DB_PATH = BASE / "nach_tracker.db"

# Tables rebuilt from raw_returns after every upload.
DERIVED_TABLES = ("charges", "presentation_attempts", "bounce_events")
# Tables holding uploaded data (everything except the reference tables).
TRANSACTION_TABLES = DERIVED_TABLES + ("raw_returns", "import_batches")

SCHEMA = """
CREATE TABLE IF NOT EXISTS master_reason_codes (
    canonical_code   TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    category         TEXT NOT NULL,   -- CUSTOMER_FAULT / MANDATE_ISSUE / TECHNICAL
    is_chargeable    TEXT NOT NULL CHECK (is_chargeable IN ('Y', 'N'))
);

CREATE TABLE IF NOT EXISTS bank_code_mapping (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sponsor_bank     TEXT NOT NULL,
    raw_code         TEXT NOT NULL,
    raw_description  TEXT,
    canonical_code   TEXT NOT NULL REFERENCES master_reason_codes(canonical_code),
    UNIQUE (sponsor_bank, raw_code)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name        TEXT NOT NULL,
    file_hash        TEXT NOT NULL UNIQUE,
    instrument_type  TEXT,
    sponsor_bank     TEXT,
    row_count        INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL,   -- PROCESSING / SUCCESS / FAILED
    error_message    TEXT,
    uploaded_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_batches_uploaded_at
    ON import_batches (uploaded_at DESC);

-- instrument_type: 'NACH' or 'CHEQUE'
-- primary_ref:     UMRN (NACH)      or cheque number (CHEQUE)
-- secondary_ref:   due date (NACH)  or drawee account number (CHEQUE)
-- dedup_key:       instrument_type|primary_ref|secondary_ref
-- row_hash:        fingerprint of the raw row, so an identical row that shows
--                  up in two different files is only stored once.
CREATE TABLE IF NOT EXISTS raw_returns (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_type    TEXT NOT NULL,
    sponsor_bank       TEXT NOT NULL,
    primary_ref        TEXT NOT NULL,
    secondary_ref      TEXT NOT NULL,
    presentation_seq   INTEGER NOT NULL,
    return_date        TEXT NOT NULL,
    raw_code           TEXT NOT NULL,
    raw_description    TEXT,
    account_no         TEXT NOT NULL,
    customer_name      TEXT NOT NULL,
    amount             REAL NOT NULL,
    canonical_code     TEXT NOT NULL,
    dedup_key          TEXT NOT NULL,
    row_hash           TEXT NOT NULL,
    import_batch_id    INTEGER REFERENCES import_batches(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_returns_row_hash ON raw_returns (row_hash);
CREATE INDEX IF NOT EXISTS idx_raw_returns_dedup_key ON raw_returns (dedup_key);

-- One row per unique mandate/cheque, however often it was re-presented.
CREATE TABLE IF NOT EXISTS bounce_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_type    TEXT NOT NULL,
    primary_ref        TEXT NOT NULL,
    secondary_ref      TEXT NOT NULL,
    dedup_key          TEXT NOT NULL UNIQUE,
    account_no         TEXT NOT NULL,
    customer_name      TEXT NOT NULL,
    amount             REAL NOT NULL,
    canonical_code     TEXT NOT NULL,
    sponsor_bank       TEXT NOT NULL,
    attempt_count      INTEGER NOT NULL DEFAULT 1
);

-- Links every raw return row to the bounce event it belongs to.
CREATE TABLE IF NOT EXISTS presentation_attempts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    bounce_event_id    INTEGER NOT NULL REFERENCES bounce_events(id),
    raw_return_id      INTEGER NOT NULL REFERENCES raw_returns(id),
    presentation_seq   INTEGER NOT NULL,
    return_date        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS charges (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    bounce_event_id    INTEGER NOT NULL UNIQUE REFERENCES bounce_events(id),
    instrument_type    TEXT NOT NULL,
    primary_ref        TEXT NOT NULL,
    customer_name      TEXT NOT NULL,
    account_no         TEXT NOT NULL,
    reason             TEXT NOT NULL,
    charge_amount      REAL NOT NULL,
    notice_text        TEXT NOT NULL
);
"""


def get_db_path() -> Path:
    return Path(os.environ.get("NACH_DB_PATH") or DEFAULT_DB_PATH)


def connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or get_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    with open(DATA_DIR / "master_reason_codes_seed.csv", newline="", encoding="utf-8") as f:
        conn.executemany(
            "INSERT OR IGNORE INTO master_reason_codes "
            "(canonical_code, display_name, category, is_chargeable) VALUES (?,?,?,?)",
            [(r["canonical_code"], r["display_name"], r["category"], r["is_chargeable"])
             for r in csv.DictReader(f)],
        )
    with open(DATA_DIR / "bank_code_mapping_seed.csv", newline="", encoding="utf-8") as f:
        conn.executemany(
            "INSERT OR IGNORE INTO bank_code_mapping "
            "(sponsor_bank, raw_code, raw_description, canonical_code) VALUES (?,?,?,?)",
            [(r["sponsor_bank"], r["raw_code"], r["raw_description"], r["canonical_code"])
             for r in csv.DictReader(f)],
        )


def init_db(conn: sqlite3.Connection) -> None:
    """Create any missing tables and seed the reference data (idempotent)."""
    conn.executescript(SCHEMA)
    _seed(conn)
    conn.commit()


def reset_uploaded_data(conn: sqlite3.Connection) -> None:
    """Delete everything that came from uploads; keep the reference tables."""
    for table in TRANSACTION_TABLES:
        conn.execute(f"DELETE FROM {table}")  # names come from the constant above
    conn.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)"
                 % ",".join("?" * len(TRANSACTION_TABLES)), TRANSACTION_TABLES)
    conn.commit()
