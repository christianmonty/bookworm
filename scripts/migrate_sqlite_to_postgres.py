#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import List, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


TABLES_IN_ORDER = ["books", "book_image", "book_extraction"]  # respects FKs


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def sqlite_path_from_default() -> Path:
    return repo_root() / "local_store" / "bookworm.db"


def make_pg_engine(pg_url: str) -> Engine:
    return create_engine(pg_url)


def rows_from_sqlite(db_path: Path, table: str) -> Tuple[List[str], List[tuple]]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    cols = rows[0].keys() if rows else []
    data = [tuple(r[c] for c in cols) for r in rows]
    con.close()
    return list(cols), data


def truncate_postgres_tables(pg: Engine) -> None:
    # Truncate in reverse order to satisfy FK constraints
    with pg.begin() as conn:
        conn.execute(text("TRUNCATE TABLE book_extraction RESTART IDENTITY CASCADE"))
        conn.execute(text("TRUNCATE TABLE book_image RESTART IDENTITY CASCADE"))
        conn.execute(text("TRUNCATE TABLE books RESTART IDENTITY CASCADE"))


def _coerce_value(table: str, col: str, val):
    # SQLite booleans often come through as 0/1 ints
    BOOL_COLS = {
        ("books", "jacket_included"),
        # add more here if you later add other Boolean columns
    }

    if val is None:
        return None

    if (table, col) in BOOL_COLS:
        # Accept 0/1, True/False, "0"/"1"
        if isinstance(val, bool):
            return val
        if isinstance(val, int):
            return bool(val)
        if isinstance(val, str) and val.strip() in {"0", "1"}:
            return val.strip() == "1"
        return val  # last resort; let Postgres complain if it's weird

    return val


def insert_into_postgres(pg: Engine, table: str, cols: List[str], data: List[tuple], batch_size: int = 500) -> int:
    if not data:
        return 0

    placeholders = ", ".join([f":v{i}" for i in range(len(cols))])
    col_list = ", ".join(cols)
    stmt = text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})")

    inserted = 0
    with pg.begin() as conn:
        for i in range(0, len(data), batch_size):
            chunk = data[i : i + batch_size]
            params = []
            for row in chunk:
                d = {}
                for j, col in enumerate(cols):
                    d[f"v{j}"] = _coerce_value(table, col, row[j])
                params.append(d)
            conn.execute(stmt, params)
            inserted += len(chunk)
    return inserted



def fix_sequences(pg: Engine) -> None:
    # Ensure serial sequences continue after max(id)
    seq_sql = """
    SELECT setval(pg_get_serial_sequence(:table, 'id'),
                  COALESCE((SELECT MAX(id) FROM %s), 1),
                  true);
    """
    with pg.begin() as conn:
        for t in ["books", "book_image", "book_extraction"]:
            conn.execute(text(seq_sql % t), {"table": t})


def count_table_sqlite(db_path: Path, table: str) -> int:
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    n = cur.fetchone()[0]
    con.close()
    return int(n)


def count_table_pg(pg: Engine, table: str) -> int:
    with pg.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=str(sqlite_path_from_default()), help="Path to SQLite bookworm.db")
    ap.add_argument("--pg-url", default=os.environ.get("DATABASE_URL"), help="Postgres SQLAlchemy URL (DATABASE_URL)")
    ap.add_argument("--truncate", action="store_true", help="TRUNCATE Postgres tables before inserting")
    args = ap.parse_args()

    sqlite_db = Path(args.sqlite).resolve()
    if not sqlite_db.exists():
        print(f"SQLite DB not found: {sqlite_db}")
        return 2

    if not args.pg_url or not args.pg_url.startswith("postgres"):
        print("Set DATABASE_URL to your Postgres URL or pass --pg-url.")
        print("Example: postgresql+psycopg2://user:pass@127.0.0.1:5432/bookworm")
        return 2

    pg = make_pg_engine(args.pg_url)

    print("SQLite:", sqlite_db)
    print("Postgres URL:", args.pg_url)

    # Optional truncate
    if args.truncate:
        print("Truncating Postgres tables...")
        truncate_postgres_tables(pg)

    # Migrate tables in FK-safe order
    for t in TABLES_IN_ORDER:
        cols, data = rows_from_sqlite(sqlite_db, t)
        print(f"{t}: {len(data)} rows, cols={cols}")

        inserted = insert_into_postgres(pg, t, cols, data)
        print(f"Inserted into {t}: {inserted}")

    print("Fixing sequences...")
    fix_sequences(pg)

    # Verify counts
    print("Verifying counts...")
    ok = True
    for t in TABLES_IN_ORDER:
        s = count_table_sqlite(sqlite_db, t)
        p = count_table_pg(pg, t)
        print(f"{t}: sqlite={s}, postgres={p}")
        if s != p:
            ok = False

    if not ok:
        print("ERROR: row counts differ. Do not switch DATABASE_URL yet.")
        return 1

    print("OK: migration complete. You can now point your app DATABASE_URL at Postgres.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
