"""ingestion manifest (bounded, audited provider calls) and security master (CIK identity, renames, ticker reuse)

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("securities") as b:
        b.add_column(sa.Column("cik", sa.Integer(), nullable=True))
        b.add_column(sa.Column("predecessor", sa.String(16), nullable=True))
        b.add_column(sa.Column("successor", sa.String(16), nullable=True))
        b.add_column(sa.Column("renamed_on", sa.Date(), nullable=True))
        b.create_index("ix_securities_cik", ["cik"])
    op.create_table(
        "ticker_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("event", sa.String(12), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("cik", sa.Integer(), nullable=True),
        sa.Column("other_ticker", sa.String(16), nullable=True),
        sa.Column("other_cik", sa.Integer(), nullable=True),
        sa.Column("archived_as", sa.String(32), nullable=True),
        sa.Column("effective", sa.Date(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ticker_history_ticker", "ticker_history", ["ticker"])
    op.create_table(
        "ingestion_manifest",
        sa.Column("mode", sa.String(8), primary_key=True),
        sa.Column("dataset", sa.String(24), primary_key=True),
        sa.Column("ticker", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_manifest")
    op.drop_index("ix_ticker_history_ticker", table_name="ticker_history")
    op.drop_table("ticker_history")
    with op.batch_alter_table("securities") as b:
        b.drop_index("ix_securities_cik")
        b.drop_column("renamed_on")
        b.drop_column("successor")
        b.drop_column("predecessor")
        b.drop_column("cik")
