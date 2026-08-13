"""Phase 6: record who earned an incentive, and when.

Incentives are paid for two acts: converting a lead into a customer, and closing
a service ticket. Neither was attributable before this migration —

* `service_requests` had no notion of who resolved it (there is no assignee on a
  ticket at all; the branch's whole desk is notified and whoever picks it up
  works it), so "the employee who closed it" was simply not recorded;
* a lead's conversion was credited to `assigned_employee_id`, which is who the
  lead was *given* to, not who actually converted it, and the month it landed in
  came from `updated_at`, which any later edit would move.

Both now carry an explicit actor and timestamp, so a recompute months later
produces the same figures.

Revision ID: 0006_incentive_attr
Revises: 0005_ticket_ai
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_incentive_attr"
down_revision: Union[str, None] = "0005_ticket_ai"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- who closed the ticket -------------------------------------------
    op.add_column(
        "service_requests",
        sa.Column("resolved_by_employee_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "service_requests",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_service_requests_resolved_by",
        "service_requests",
        "employees",
        ["resolved_by_employee_id"],
        ["id"],
        # The incentive was already earned; losing the staff row must not delete
        # the ticket, only orphan the credit.
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_service_requests_resolved_by",
        "service_requests",
        ["resolved_by_employee_id", "resolved_at"],
    )

    # --- who converted the lead ------------------------------------------
    op.add_column(
        "leads",
        sa.Column("converted_by_employee_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_leads_converted_by",
        "leads",
        "employees",
        ["converted_by_employee_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_leads_converted_by",
        "leads",
        ["converted_by_employee_id", "converted_at"],
    )

    # --- the new counter on the computed rollup ---------------------------
    op.add_column(
        "employee_incentives",
        sa.Column(
            "tickets_resolved_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # Backfill leads that were already converted, so existing figures do not
    # drop to zero: credit the assignee, dated by the row's last update. This is
    # the best available answer for history, and every conversion from here on
    # records the real actor.
    op.execute(
        """
        UPDATE leads
           SET converted_by_employee_id = assigned_employee_id,
               converted_at = updated_at
         WHERE converted_customer_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("employee_incentives", "tickets_resolved_count")

    op.drop_index("ix_leads_converted_by", table_name="leads")
    op.drop_constraint("fk_leads_converted_by", "leads", type_="foreignkey")
    op.drop_column("leads", "converted_at")
    op.drop_column("leads", "converted_by_employee_id")

    op.drop_index("ix_service_requests_resolved_by", table_name="service_requests")
    op.drop_constraint(
        "fk_service_requests_resolved_by", "service_requests", type_="foreignkey"
    )
    op.drop_column("service_requests", "resolved_at")
    op.drop_column("service_requests", "resolved_by_employee_id")
