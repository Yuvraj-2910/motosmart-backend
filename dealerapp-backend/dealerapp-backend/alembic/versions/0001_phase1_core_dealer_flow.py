"""Phase 1 - core dealer flow

Creates dealers, employees, customers, bike_models, leads, lead_followups.

`dealers.last_assigned_employee_id` and `employees.dealer_id` form a cycle, so
the dealers table is created with the column but without its constraint, and the
foreign key is added once `employees` exists.

Revision ID: 0001_phase1
Revises:
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_phase1"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dealers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("address", sa.String(length=400), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("pincode", sa.String(length=10), nullable=True),
        sa.Column("last_assigned_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dealers"),
        sa.UniqueConstraint("code", name="uq_dealers_code"),
    )
    op.create_index("ix_dealers_pincode", "dealers", ["pincode"])

    op.create_table(
        "employees",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dealer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cognito_sub", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dealer_id"],
            ["dealers.id"],
            name="fk_employees_dealer_id_dealers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employees"),
    )
    # `unique=True, index=True` on the ORM column renders as a single unique
    # index - not a unique constraint plus a second, redundant index.
    op.create_index(
        "ix_employees_cognito_sub", "employees", ["cognito_sub"], unique=True
    )

    # Close the dealers -> employees cycle now that both tables exist.
    op.create_foreign_key(
        "fk_dealers_last_assigned_employee_id_employees",
        "dealers",
        "employees",
        ["last_assigned_employee_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cognito_sub", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("onboarding_dealer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["onboarding_dealer_id"],
            ["dealers.id"],
            name="fk_customers_onboarding_dealer_id_dealers",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
    )
    op.create_index(
        "ix_customers_cognito_sub", "customers", ["cognito_sub"], unique=True
    )
    op.create_index("ix_customers_phone", "customers", ["phone"])

    op.create_table(
        "bike_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("variant", sa.String(length=80), nullable=True),
        sa.Column("category", sa.String(length=60), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("engine_cc", sa.Integer(), nullable=True),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("brochure_url", sa.String(length=500), nullable=True),
        sa.Column(
            "stock_status",
            sa.String(length=20),
            nullable=False,
            server_default="IN_STOCK",
        ),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bike_models"),
    )
    op.create_index("ix_bike_models_name", "bike_models", ["name"])
    op.create_index("ix_bike_models_category", "bike_models", ["category"])

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dealer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_name", sa.String(length=160), nullable=False),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="WALK_IN"),
        sa.Column("interested_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_bike", sa.String(length=160), nullable=True),
        sa.Column("tentative_purchase_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="NEW"),
        sa.Column("ai_intent", sa.String(length=10), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("converted_customer_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["dealer_id"], ["dealers.id"], name="fk_leads_dealer_id_dealers", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_employee_id"],
            ["employees.id"],
            name="fk_leads_assigned_employee_id_employees",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["interested_model_id"],
            ["bike_models.id"],
            name="fk_leads_interested_model_id_bike_models",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["converted_customer_id"],
            ["customers.id"],
            name="fk_leads_converted_customer_id_customers",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_leads"),
    )
    # The pipeline query: "my dealer's leads, optionally mine, filtered by status".
    op.create_index(
        "ix_leads_dealer_assignee_status",
        "leads",
        ["dealer_id", "assigned_employee_id", "status"],
    )
    op.create_index("ix_leads_dealer_mobile", "leads", ["dealer_id", "mobile"])

    op.create_table(
        "lead_followups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("next_action", sa.String(length=400), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_lead_followups_lead_id_leads", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_lead_followups_employee_id_employees",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_lead_followups"),
    )
    op.create_index(
        "ix_lead_followups_scheduled", "lead_followups", ["scheduled_date", "completed"]
    )


def downgrade() -> None:
    op.drop_index("ix_lead_followups_scheduled", table_name="lead_followups")
    op.drop_table("lead_followups")

    op.drop_index("ix_leads_dealer_mobile", table_name="leads")
    op.drop_index("ix_leads_dealer_assignee_status", table_name="leads")
    op.drop_table("leads")

    op.drop_index("ix_bike_models_category", table_name="bike_models")
    op.drop_index("ix_bike_models_name", table_name="bike_models")
    op.drop_table("bike_models")

    op.drop_index("ix_customers_phone", table_name="customers")
    op.drop_index("ix_customers_cognito_sub", table_name="customers")
    op.drop_table("customers")

    # Break the cycle before dropping employees.
    op.drop_constraint(
        "fk_dealers_last_assigned_employee_id_employees", "dealers", type_="foreignkey"
    )
    op.drop_index("ix_employees_cognito_sub", table_name="employees")
    op.drop_table("employees")

    op.drop_index("ix_dealers_pincode", table_name="dealers")
    op.drop_table("dealers")
