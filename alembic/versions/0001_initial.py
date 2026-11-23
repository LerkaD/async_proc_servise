"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("webhook_url", sa.String(length=2048), nullable=False),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("provider_reference", sa.String(length=128), nullable=True),
        sa.Column("processing_token", sa.String(length=64), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "webhook_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("webhook_next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        sa.CheckConstraint("currency IN ('RUB', 'USD', 'EUR')", name="ck_payments_currency"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', 'failed')",
            name="ck_payments_status",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
    )
    op.create_index("ix_payments_created_at", "payments", ["created_at"])
    op.create_index(
        "ix_payments_webhook_due",
        "payments",
        ["webhook_next_retry_at"],
        postgresql_where=sa.text(
            "webhook_delivered_at IS NULL AND webhook_next_retry_at IS NOT NULL",
        ),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=200), primary_key=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_body", postgresql.JSONB, nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name="fk_idempotency_keys_payment_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_idempotency_keys_payment_id",
        "idempotency_keys",
        ["payment_id"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'published', 'failed')",
            name="ck_outbox_status",
        ),
        sa.ForeignKeyConstraint(
            ["aggregate_id"],
            ["payments.id"],
            name="fk_outbox_events_aggregate_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_outbox_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index(
        "ix_outbox_pending_claim",
        "outbox_events",
        ["next_retry_at", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_outbox_failed_retry",
        "outbox_events",
        ["next_retry_at", "created_at"],
        postgresql_where=sa.text("status = 'failed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_failed_retry", table_name="outbox_events")
    op.drop_index("ix_outbox_pending_claim", table_name="outbox_events")
    op.drop_index("ix_outbox_aggregate_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_idempotency_keys_payment_id", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_payments_webhook_due", table_name="payments")
    op.drop_index("ix_payments_created_at", table_name="payments")
    op.drop_table("payments")
