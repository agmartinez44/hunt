"""SQLite schema and connection for one Hunt workspace."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    source TEXT,
    url TEXT,
    title_posted TEXT,
    title_ours TEXT,
    location_country TEXT,
    location_city TEXT,
    modality TEXT,
    office_days_per_week REAL,
    engagement TEXT,
    duration_months INTEGER,
    comp_amount REAL,
    comp_currency TEXT,
    comp_unit TEXT,
    comp_notes TEXT,
    tax_home_for_net TEXT,
    languages_required TEXT NOT NULL DEFAULT '[]',
    recruiter TEXT,
    cv_variant_id TEXT,
    knockouts TEXT NOT NULL DEFAULT '[]',
    extra TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    fx_as_of TEXT,
    display_currency TEXT,
    derived_hour REAL,
    derived_day REAL,
    derived_month REAL,
    derived_year REAL,
    derived_net_month REAL,
    derived_clears_floor INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_updated ON applications(updated_at);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    kind TEXT NOT NULL,
    body TEXT,
    actor TEXT NOT NULL DEFAULT 'cli',
    at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_application ON events(application_id, at);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (application_id, filename)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_application ON artifacts(application_id);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    last_run_at TEXT,
    last_status TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES sources(id),
    external_id TEXT,
    url TEXT,
    title TEXT,
    company TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_source_external
ON listings(source_id, external_id)
WHERE source_id IS NOT NULL AND external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS inbox_items (
    id TEXT PRIMARY KEY,
    listing_id TEXT REFERENCES listings(id),
    status TEXT NOT NULL,
    why_keep TEXT,
    why_risk TEXT,
    knockouts_json TEXT NOT NULL DEFAULT '[]',
    triage_json TEXT,
    application_id TEXT REFERENCES applications(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_items(status);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    state TEXT NOT NULL,
    target_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_V1)
    cols = _columns(conn, "events")
    if "actor" not in cols:
        conn.execute(
            "ALTER TABLE events ADD COLUMN actor TEXT NOT NULL DEFAULT 'cli'"
        )
    job_cols = _columns(conn, "jobs")
    if "result_json" not in job_cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}'"
        )
    inbox_cols = _columns(conn, "inbox_items")
    if "triage_json" not in inbox_cols:
        conn.execute("ALTER TABLE inbox_items ADD COLUMN triage_json TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_source_external
        ON listings(source_id, external_id)
        WHERE source_id IS NOT NULL AND external_id IS NOT NULL
        """
    )
    row = conn.execute(
        "SELECT MAX(version) AS v FROM schema_migrations"
    ).fetchone()
    current = int(row["v"] or 0)
    if current < SCHEMA_VERSION:
        from hunt.core.ids import now_iso

        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, now_iso()),
        )
    conn.commit()
