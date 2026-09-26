"""append-only recommendation versions, corporate actions (splits), estimate snapshots, SEC guidance

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recommendations") as b:
        b.add_column(sa.Column("supersedes_id", sa.Integer(), sa.ForeignKey("recommendations.id", name="fk_rec_supersedes"), nullable=True))
        b.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        b.create_index("ix_recommendations_supersedes_id", ["supersedes_id"])
    op.create_table(
        "corporate_actions",
        sa.Column("ticker", sa.String(16), primary_key=True),
        sa.Column("execution_date", sa.Date(), primary_key=True),
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("split_from", sa.Float(), nullable=False),
        sa.Column("split_to", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bars_adjusted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "estimate_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("period", sa.String(24), nullable=False),
        sa.Column("period_type", sa.String(8), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("eps_estimate", sa.Float(), nullable=True),
        sa.Column("revenue_estimate", sa.Float(), nullable=True),
        sa.Column("analyst_count", sa.Integer(), nullable=True),
        sa.Column("eps_high", sa.Float(), nullable=True),
        sa.Column("eps_low", sa.Float(), nullable=True),
        sa.Column("provider_revisions", sa.JSON(), nullable=True),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_estimate_snapshots_ticker", "estimate_snapshots", ["ticker"])
    op.create_index("ix_estimate_snapshots_observed_on", "estimate_snapshots", ["observed_on"])
    op.create_index("ux_estimate_snapshot", "estimate_snapshots", ["ticker", "provider", "period", "observed_on"], unique=True)
    op.create_table(
        "guidance",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.String(300), nullable=False),
        sa.Column("metric", sa.String(24), nullable=False),
        sa.Column("period_label", sa.String(40), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(12), nullable=False),
        sa.Column("sentence", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_guidance_ticker", "guidance", ["ticker"])
    op.create_index("ux_guidance", "guidance", ["ticker", "accession", "metric", "sentence"])


def downgrade() -> None:
    op.drop_index("ux_guidance", "guidance")
    op.drop_index("ix_guidance_ticker", "guidance")
    op.drop_table("guidance")
    op.drop_index("ux_estimate_snapshot", "estimate_snapshots")
    op.drop_index("ix_estimate_snapshots_observed_on", "estimate_snapshots")
    op.drop_index("ix_estimate_snapshots_ticker", "estimate_snapshots")
    op.drop_table("estimate_snapshots")
    op.drop_table("corporate_actions")
    with op.batch_alter_table("recommendations") as b:
        b.drop_index("ix_recommendations_supersedes_id")
        b.drop_column("version")
        b.drop_column("supersedes_id")
