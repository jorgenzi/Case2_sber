"""Начальная схема: obligations и payments

Revision ID: 0001_initial
Revises:
Create Date: 2025-07-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "obligations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "subscription",
                "warranty",
                "bill",
                "insurance",
                name="category",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "recurrence",
            sa.Enum(
                "monthly",
                "quarterly",
                "yearly",
                name="recurrence",
                native_enum=False,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("next_payment_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "cancelled",
                "expired",
                name="status",
                native_enum=False,
                length=20,
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_obligations_title", "obligations", ["title"])
    op.create_index(
        "ix_obligations_status_next_payment_date",
        "obligations",
        ["status", "next_payment_date"],
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "obligation_id",
            sa.Uuid(),
            sa.ForeignKey("obligations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payments_obligation_id", "payments", ["obligation_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_obligation_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index(
        "ix_obligations_status_next_payment_date", table_name="obligations"
    )
    op.drop_index("ix_obligations_title", table_name="obligations")
    op.drop_table("obligations")
