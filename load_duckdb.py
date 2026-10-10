"""Load the cleaned invoice CSV into a persisted DuckDB table.

Rebuilds the table whenever the source CSV is newer than the DuckDB file,
so repeated agent runs don't re-parse the CSV every time. See
spec/query_agent_spec.md and assumptions/ASSUMPTIONS.md #7.

Usage:
    python load_duckdb.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
CLEAN_CSV_PATH = DATA_DIR / "cloudnova_invoices_clean.csv"
DUCKDB_PATH = DATA_DIR / "cloudnova.duckdb"
TABLE_NAME = "invoices_clean"


def needs_rebuild() -> bool:
    if not DUCKDB_PATH.exists():
        return True
    return CLEAN_CSV_PATH.stat().st_mtime > DUCKDB_PATH.stat().st_mtime


def build() -> None:
    if not CLEAN_CSV_PATH.exists():
        raise FileNotFoundError(
            f"{CLEAN_CSV_PATH} not found — run clean_invoices.py first."
        )
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        con.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
        con.execute(
            f"CREATE TABLE {TABLE_NAME} AS SELECT * FROM read_csv_auto(?)",
            [str(CLEAN_CSV_PATH)],
        )
        n_rows = con.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
        logger.info("Loaded %d rows into %s (%s)", n_rows, TABLE_NAME, DUCKDB_PATH)
    finally:
        con.close()


def ensure_loaded() -> Path:
    """Build the DuckDB table if missing or stale, return the DB file path."""
    if needs_rebuild():
        build()
    else:
        logger.info("%s is up to date, skipping rebuild", DUCKDB_PATH)
    return DUCKDB_PATH


if __name__ == "__main__":
    build()
