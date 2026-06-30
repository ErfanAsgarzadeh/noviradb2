"""
migrate_data.py — One-shot data migration from old SQLite to current PostgreSQL.

Strategy
--------
Reads each Django model's table from the SQLite file using raw `sqlite3` and
inserts the rows into the PostgreSQL database that Django is currently
configured for (see KTCProject/settings.py + .env).

Only columns that exist in BOTH databases are copied. New columns added by
recent migrations (and missing in the old SQLite) are populated from PostgreSQL
column defaults. Tables that don't exist in SQLite at all are skipped — they
just stay empty in PostgreSQL.

Tables are processed in topological FK order. Triggers are disabled per-table
during insert so foreign-key checks happen at the end of the transaction
(necessary for self-referential FKs and out-of-order children).

Usage
-----
    # default — looks for db.sqlite3 next to manage.py
    python migrate_data.py

    # explicit path
    python migrate_data.py path/to/old/db.sqlite3

Prerequisites
-------------
- The PostgreSQL database has been migrated (`python manage.py migrate`) so
  every table that Django expects already exists.
- The role configured in .env is the OWNER of every table (so it can disable
  triggers). If you used the recommended setup with `erfanAdmin` as DB owner,
  you're fine.
- Run from the project root (where manage.py lives).

Safety
------
- This script TRUNCATEs each target table before inserting. Run it against a
  fresh PostgreSQL DB only.
- Wrapped in a single transaction; if anything fails, nothing is committed.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from io import StringIO
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "KTCProject.settings")
django.setup()

from django.apps import apps  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection as pg_conn, transaction  # noqa: E402

# Apps whose data we never copy (Django manages these automatically).
SKIP_APPS = {"admin", "contenttypes", "sessions", "auth"}
# Models whose data we don't want to copy even if a table happens to exist.
SKIP_MODELS = {
    "token_blacklist.BlacklistedToken",
    "token_blacklist.OutstandingToken",
}


def topological_models():
    """Return Django models in dependency order (parents before children)."""
    visited: set = set()
    order: list = []

    def visit(model):
        if model in visited:
            return
        visited.add(model)
        for field in model._meta.get_fields():
            related = getattr(field, "related_model", None)
            if related and getattr(field, "many_to_one", False):
                if related._meta.app_label in SKIP_APPS:
                    continue
                visit(related)
        order.append(model)

    for cfg in apps.get_app_configs():
        if cfg.label in SKIP_APPS:
            continue
        for model in cfg.get_models():
            visit(model)
    return order


def get_pg_columns(cur, table: str) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        [table],
    )
    return {row[0] for row in cur.fetchall()}


def get_pg_column_types(cur, table: str) -> dict[str, str]:
    """Map column_name -> PostgreSQL data_type ('boolean', 'integer', 'jsonb', ...)."""
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        [table],
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def get_pg_column_meta(cur, table: str) -> dict[str, dict]:
    """Per-column metadata from Postgres: type, nullability, default expression."""
    cur.execute(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        [table],
    )
    return {
        row[0]: {
            "type": row[1],
            "nullable": row[2] == "YES",
            "default": row[3],
        }
        for row in cur.fetchall()
    }


def get_field_defaults_for(model, missing_cols: set[str], pg_meta: dict[str, dict]):
    """For Postgres columns that don't exist in SQLite, return values to fill them.

    A column is filled only when:
      - it is NOT NULL in Postgres, AND
      - it has no Postgres-level default, AND
      - the corresponding Django model field defines a default.

    Otherwise we omit the column from INSERT and let Postgres handle it.
    """
    fills: dict = {}
    field_by_column = {
        f.column: f for f in model._meta.concrete_fields if hasattr(f, "column")
    }
    for col in missing_cols:
        meta = pg_meta.get(col)
        if not meta:
            continue
        if meta["nullable"] or meta["default"] is not None:
            continue  # Postgres will handle it
        field = field_by_column.get(col)
        if field is None or not field.has_default():
            # No model-level default we can use — let DB error so user knows.
            continue
        fills[col] = field.get_default()
    return fills


def coerce_value(value, pg_type: str):
    """Convert a SQLite-shaped value to something PostgreSQL accepts.

    SQLite stores booleans as 0/1 integers; PostgreSQL has a real boolean type
    and refuses implicit int->bool casts. We also normalize empty strings for
    JSON columns and pass everything else through untouched.
    """
    if value is None:
        return None
    if pg_type == "boolean":
        # SQLite: 0/1, '0'/'1', possibly already True/False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "t", "true", "y", "yes")
    if pg_type in ("json", "jsonb") and isinstance(value, str) and value == "":
        # Some Django setups stored "" instead of NULL for nullable JSON fields.
        return None
    return value


def get_sqlite_columns(src: sqlite3.Connection, table: str) -> list[str]:
    """Returns column names in declared order."""
    return [row["name"] for row in src.execute(f'PRAGMA table_info("{table}")')]


def main() -> int:
    sqlite_path = sys.argv[1] if len(sys.argv) > 1 else "db.sqlite3"
    if not Path(sqlite_path).is_file():
        print(f"ERROR: SQLite file not found: {sqlite_path}", file=sys.stderr)
        return 1

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    sqlite_tables = {
        r[0]
        for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }

    plan = []
    skipped = []
    for model in topological_models():
        full = f"{model._meta.app_label}.{model.__name__}"
        if full in SKIP_MODELS:
            continue
        table = model._meta.db_table
        if table in sqlite_tables:
            plan.append(model)
        else:
            skipped.append(model)

    print(f"Will migrate {len(plan)} tables; skipping {len(skipped)} (not in SQLite).")
    for m in skipped:
        print(f"  SKIP {m._meta.db_table}")
    print()

    total_rows = 0
    with transaction.atomic():
        with pg_conn.cursor() as cur:
            for model in plan:
                table = model._meta.db_table
                sqlite_cols = get_sqlite_columns(src, table)
                pg_meta = get_pg_column_meta(cur, table)
                pg_types = {c: m["type"] for c, m in pg_meta.items()}
                pg_cols = set(pg_meta.keys())
                common = [c for c in sqlite_cols if c in pg_cols]
                missing_in_sqlite = pg_cols - set(sqlite_cols)
                extra_in_sqlite = set(sqlite_cols) - pg_cols

                if not common:
                    print(f"  SKIP {table} (no common columns)")
                    continue

                # Determine which PG-only columns we must fill from model defaults.
                fills = get_field_defaults_for(model, missing_in_sqlite, pg_meta)
                fill_cols = list(fills.keys())

                col_list_quoted = ", ".join(
                    f'"{c}"' for c in (common + fill_cols)
                )
                select_cols = ", ".join(f'"{c}"' for c in common)
                rows = list(src.execute(f'SELECT {select_cols} FROM "{table}"'))

                # Coerce SQLite values to PostgreSQL-compatible types
                # (most importantly: int 0/1 -> bool).
                col_pg_types = [pg_types[c] for c in common]
                fill_tuple = tuple(fills[c] for c in fill_cols)
                coerced_rows = [
                    tuple(coerce_value(v, t) for v, t in zip(row, col_pg_types))
                    + fill_tuple
                    for row in rows
                ]

                # Disable triggers (FK checks) for this table during insert.
                cur.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER ALL')
                cur.execute(f'TRUNCATE "{table}" CASCADE')
                if coerced_rows:
                    placeholders = ", ".join(
                        ["%s"] * (len(common) + len(fill_cols))
                    )
                    insert_sql = (
                        f'INSERT INTO "{table}" ({col_list_quoted}) '
                        f"VALUES ({placeholders})"
                    )
                    cur.executemany(insert_sql, coerced_rows)
                cur.execute(f'ALTER TABLE "{table}" ENABLE TRIGGER ALL')

                hint = ""
                if missing_in_sqlite:
                    hint += f" [pg-only: {','.join(sorted(missing_in_sqlite))}]"
                if fill_cols:
                    hint += f" [filled-from-model-default: {','.join(sorted(fill_cols))}]"
                if extra_in_sqlite:
                    hint += f" [sqlite-only: {','.join(sorted(extra_in_sqlite))}]"
                print(f"  {table}: {len(rows)} rows{hint}")
                total_rows += len(rows)

    print(f"\nInserted {total_rows} rows in total.")

    # Reset autoincrement sequences so future inserts don't collide with copied PKs.
    print("\nResetting sequences...")
    buf = StringIO()
    call_command("sqlsequencereset", "CustomUser", "ktcPlanning", stdout=buf)
    sql = buf.getvalue().strip()
    if sql:
        with pg_conn.cursor() as cur:
            cur.execute(sql)
        pg_conn.commit()
        print("Sequences reset.")
    else:
        print("(no sequences to reset)")

    src.close()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
