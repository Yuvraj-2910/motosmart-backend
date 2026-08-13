"""Phase 4 - incentives + polish

Creates incentive_rules, employee_incentives.

Revision ID: 0004_phase4
Revises: 0003_phase3
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase4"
down_revision: Union[str, None] = "0003_phase3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incentive_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dealer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False, server_default="MONTHLY"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dealer_id"],
            ["dealers.id"],
            name="fk_incentive_rules_dealer_id_dealers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incentive_rules"),
    )
    op.create_index("ix_incentive_rules_dealer_id", "incentive_rules", ["dealer_id"])

    op.create_table(
        "employee_incentives",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=False),
        sa.Column("leads_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("test_rides_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sales_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "total_incentive",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_employee_incentives_employee_id_employees",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employee_incentives"),
        sa.UniqueConstraint(
            "employee_id", "period_month", name="uq_employee_incentive_period"
        ),
    )
    op.create_index("ix_employee_incentives_period_month", "employee_incentives", ["period_month"])


def downgrade() -> None:
    op.drop_index("ix_employee_incentives_period_month", table_name="employee_incentives")
    op.drop_table("employee_incentives")

    op.drop_index("ix_incentive_rules_dealer_id", table_name="incentive_rules")
    op.drop_table("incentive_rules")
