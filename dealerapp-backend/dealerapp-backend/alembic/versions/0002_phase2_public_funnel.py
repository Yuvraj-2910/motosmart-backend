"""Phase 2 - public funnel, auto-assignment, notifications

Creates exchange_values, test_ride_bookings, notifications.

Revision ID: 0002_phase2
Revises: 0001_phase1
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase2"
down_revision: Union[str, None] = "0001_phase1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exchange_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand", sa.String(length=60), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("base_value", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("condition_factor_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exchange_values"),
    )
    op.create_index("ix_exchange_values_brand", "exchange_values", ["brand"])
    op.create_index("ix_exchange_values_model", "exchange_values", ["model"])

    op.create_table(
        "test_ride_bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bike_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("preferred_date", sa.Date(), nullable=False),
        sa.Column("preferred_time", sa.Time(), nullable=True),
        sa.Column("dealer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="REQUESTED"),
        sa.Column("linked_lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bike_model_id"],
            ["bike_models.id"],
            name="fk_test_ride_bookings_bike_model_id_bike_models",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["dealer_id"],
            ["dealers.id"],
            name="fk_test_ride_bookings_dealer_id_dealers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["linked_lead_id"],
            ["leads.id"],
            name="fk_test_ride_bookings_linked_lead_id_leads",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_test_ride_bookings"),
    )
    op.create_index("ix_test_ride_bookings_mobile", "test_ride_bookings", ["mobile"])
    op.create_index("ix_test_rides_dealer_status", "test_ride_bookings", ["dealer_id", "status"])

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_type", sa.String(length=20), nullable=False),
        # Polymorphic over recipient_type (employee or customer) - deliberately
        # no foreign key.
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=1000), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    # Serves the polling query: unread notifications for one recipient.
    op.create_index(
        "ix_notifications_recipient",
        "notifications",
        ["recipient_type", "recipient_id", "is_read"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_recipient", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_test_rides_dealer_status", table_name="test_ride_bookings")
    op.drop_index("ix_test_ride_bookings_mobile", table_name="test_ride_bookings")
    op.drop_table("test_ride_bookings")

    op.drop_index("ix_exchange_values_model", table_name="exchange_values")
    op.drop_index("ix_exchange_values_brand", table_name="exchange_values")
    op.drop_table("exchange_values")
