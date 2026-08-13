"""Phase 3 - customer side (vehicles, service, chatbot)

Creates vehicles, service_records, obd_telemetry, service_requests,
service_request_messages, chatbot_conversations, chatbot_messages.

Revision ID: 0003_phase3
Revises: 0002_phase2
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase3"
down_revision: Union[str, None] = "0002_phase2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bike_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vin", sa.String(length=40), nullable=True),
        sa.Column("registration_no", sa.String(length=20), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("odometer_km", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_vehicles_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bike_model_id"],
            ["bike_models.id"],
            name="fk_vehicles_bike_model_id_bike_models",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_vehicles"),
        sa.UniqueConstraint("vin", name="uq_vehicles_vin"),
    )
    op.create_index("ix_vehicles_customer_id", "vehicles", ["customer_id"])
    op.create_index("ix_vehicles_registration_no", "vehicles", ["registration_no"])

    op.create_table(
        "service_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("odometer_km", sa.Integer(), nullable=True),
        sa.Column("service_type", sa.String(length=80), nullable=True),
        sa.Column("cost", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("next_service_date", sa.Date(), nullable=True),
        sa.Column("next_service_km", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name="fk_service_records_vehicle_id_vehicles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service_records"),
    )
    op.create_index(
        "ix_service_records_vehicle_date", "service_records", ["vehicle_id", "service_date"]
    )

    op.create_table(
        "obd_telemetry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("odometer_km", sa.Integer(), nullable=True),
        sa.Column("battery_voltage", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("fuel_level", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("engine_temp", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("avg_speed", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("dtc_codes", sa.String(length=200), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name="fk_obd_telemetry_vehicle_id_vehicles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_obd_telemetry"),
    )
    op.create_index(
        "ix_obd_vehicle_recorded", "obd_telemetry", ["vehicle_id", "recorded_at"]
    )

    op.create_table(
        "service_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dealer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="OPEN"),
        sa.Column("preferred_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name="fk_service_requests_vehicle_id_vehicles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_service_requests_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dealer_id"],
            ["dealers.id"],
            name="fk_service_requests_dealer_id_dealers",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service_requests"),
    )
    op.create_index(
        "ix_service_requests_dealer_status", "service_requests", ["dealer_id", "status"]
    )
    op.create_index(
        "ix_service_requests_customer", "service_requests", ["customer_id", "status"]
    )

    op.create_table(
        "service_request_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_type", sa.String(length=20), nullable=False),
        # Polymorphic over sender_type (employee or customer) - no FK.
        sa.Column("sender_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["service_request_id"],
            ["service_requests.id"],
            name="fk_service_request_messages_service_request_id_service_requests",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service_request_messages"),
    )
    op.create_index(
        "ix_service_messages_request",
        "service_request_messages",
        ["service_request_id", "created_at"],
    )

    op.create_table(
        "chatbot_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_chatbot_conversations_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chatbot_conversations"),
    )
    op.create_index(
        "ix_chatbot_conversations_customer_id", "chatbot_conversations", ["customer_id"]
    )

    op.create_table(
        "chatbot_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chatbot_conversations.id"],
            name="fk_chatbot_messages_conversation_id_chatbot_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chatbot_messages"),
    )
    op.create_index(
        "ix_chatbot_messages_conv", "chatbot_messages", ["conversation_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_chatbot_messages_conv", table_name="chatbot_messages")
    op.drop_table("chatbot_messages")

    op.drop_index("ix_chatbot_conversations_customer_id", table_name="chatbot_conversations")
    op.drop_table("chatbot_conversations")

    op.drop_index("ix_service_messages_request", table_name="service_request_messages")
    op.drop_table("service_request_messages")

    op.drop_index("ix_service_requests_customer", table_name="service_requests")
    op.drop_index("ix_service_requests_dealer_status", table_name="service_requests")
    op.drop_table("service_requests")

    op.drop_index("ix_obd_vehicle_recorded", table_name="obd_telemetry")
    op.drop_table("obd_telemetry")

    op.drop_index("ix_service_records_vehicle_date", table_name="service_records")
    op.drop_table("service_records")

    op.drop_index("ix_vehicles_registration_no", table_name="vehicles")
    op.drop_index("ix_vehicles_customer_id", table_name="vehicles")
    op.drop_table("vehicles")
