"""Phase 7: dealer-side chatbot.

Creates dealer_chatbot_conversations, dealer_chatbot_messages - the staff-facing
counterpart to the customer chatbot tables from phase 3, keyed on `employee_id`
instead of `customer_id` so the two never share a transcript.

Revision ID: 0007_dealer_chatbot
Revises: 0006_incentive_attr
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_dealer_chatbot"
down_revision: Union[str, None] = "0006_incentive_attr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dealer_chatbot_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_dealer_chatbot_conversations_employee_id_employees",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dealer_chatbot_conversations"),
    )
    op.create_index(
        "ix_dealer_chatbot_conversations_employee_id",
        "dealer_chatbot_conversations",
        ["employee_id"],
    )

    op.create_table(
        "dealer_chatbot_messages",
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
            ["dealer_chatbot_conversations.id"],
            name="fk_dealer_chatbot_messages_conversation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dealer_chatbot_messages"),
    )
    op.create_index(
        "ix_dealer_chatbot_messages_conv",
        "dealer_chatbot_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dealer_chatbot_messages_conv", table_name="dealer_chatbot_messages")
    op.drop_table("dealer_chatbot_messages")

    op.drop_index(
        "ix_dealer_chatbot_conversations_employee_id",
        table_name="dealer_chatbot_conversations",
    )
    op.drop_table("dealer_chatbot_conversations")
