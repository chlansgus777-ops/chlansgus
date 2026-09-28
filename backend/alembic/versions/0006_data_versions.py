"""data versions: a per-table change counter kept by SQLite triggers, so the readiness cache check is one small read
instead of a count over every stored price bar (performance overhaul 2026-09-28). Adds a table and triggers only —
no existing row is changed or removed.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = ("price_bars", "securities", "fundamentals_quarterly", "ingestion_manifest", "estimate_snapshots")


def upgrade() -> None:
    op.create_table(
        "data_versions",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    if op.get_bind().dialect.name != "sqlite":
        return  # other databases: no counters — the store falls back to its full check
    for t in TABLES:
        op.execute(f"INSERT OR IGNORE INTO data_versions (name, version) VALUES ('{t}', 0)")
        for kind in ("INSERT", "UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER IF NOT EXISTS dv_{t}_{kind.lower()} AFTER {kind} ON {t} BEGIN "
                       f"UPDATE data_versions SET version = version + 1 WHERE name = '{t}'; END")


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for t in TABLES:
            for kind in ("insert", "update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS dv_{t}_{kind}")
    op.drop_table("data_versions")
