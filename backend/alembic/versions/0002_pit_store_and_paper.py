"""local PIT store fields, paper sizing/skip reason, research-integrity versions, outcome status, nullable LLM cost

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("securities") as b:
        b.add_column(sa.Column("first_seen", sa.Date(), nullable=True))
        b.add_column(sa.Column("sic", sa.Integer(), nullable=True))
        b.add_column(sa.Column("profile_updated_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("shares_outstanding", sa.Float(), nullable=True))
        b.add_column(sa.Column("shares_as_of", sa.Date(), nullable=True))
    with op.batch_alter_table("paper_positions") as b:
        b.add_column(sa.Column("max_buy", sa.Float(), nullable=True))
        b.add_column(sa.Column("notional", sa.Float(), nullable=True))
        b.add_column(sa.Column("skip_reason", sa.String(300), nullable=True))
    with op.batch_alter_table("recommendations") as b:
        b.add_column(sa.Column("code_version", sa.String(64), nullable=True))
        b.add_column(sa.Column("app_version", sa.String(32), nullable=True))
        b.add_column(sa.Column("llm_model_ids", sa.String(200), nullable=True))
    with op.batch_alter_table("recommendation_outcomes") as b:
        b.add_column(sa.Column("status", sa.String(24), nullable=False, server_default="OK"))
    with op.batch_alter_table("llm_calls") as b:
        b.alter_column("estimated_cost_usd", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("llm_calls") as b:
        b.alter_column("estimated_cost_usd", existing_type=sa.Float(), nullable=False)
    with op.batch_alter_table("recommendation_outcomes") as b:
        b.drop_column("status")
    with op.batch_alter_table("recommendations") as b:
        b.drop_column("llm_model_ids")
        b.drop_column("app_version")
        b.drop_column("code_version")
    with op.batch_alter_table("paper_positions") as b:
        b.drop_column("skip_reason")
        b.drop_column("notional")
        b.drop_column("max_buy")
    with op.batch_alter_table("securities") as b:
        for c in ("shares_as_of", "shares_outstanding", "profile_updated_at", "sic", "first_seen"):
            b.drop_column(c)
