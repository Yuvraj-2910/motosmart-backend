"""Phase 5: AI triage columns on service requests.

Adds the fields filled in when a customer raises a ticket: a category, a
priority, and a one-line summary of their description. All nullable — the
classification is best-effort and a ticket is never blocked on the model being
reachable.

Revision ID: 0005_ticket_ai
Revises: 0004_phase4
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ticket_ai"
down_revision: Union[str, None] = "0004_phase4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "service_requests", sa.Column("ai_category", sa.String(length=30), nullable=True)
    )
    op.add_column(
        "service_requests", sa.Column("ai_priority", sa.String(length=20), nullable=True)
    )
    op.add_column("service_requests", sa.Column("ai_summary", sa.Text(), nullable=True))

    # The dealer queue is sorted by urgency, so priority is filtered/ordered on.
    op.create_index(
        "ix_service_requests_priority",
        "service_requests",
        ["dealer_id", "ai_priority"],
    )


def downgrade() -> None:
    op.drop_index("ix_service_requests_priority", table_name="service_requests")
    op.drop_column("service_requests", "ai_summary")
    op.drop_column("service_requests", "ai_priority")
    op.drop_column("service_requests", "ai_category")
