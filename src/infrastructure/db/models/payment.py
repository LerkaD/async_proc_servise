import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.clock import utcnow
from src.domain.constants import (
    DESCRIPTION_MAX_LENGTH,
    ERROR_MESSAGE_MAX_LENGTH,
    IDEMPOTENCY_KEY_MAX_LENGTH,
    PROCESSING_TOKEN_MAX_LENGTH,
    PROVIDER_REFERENCE_MAX_LENGTH,
    WEBHOOK_URL_MAX_LENGTH,
)
from src.domain.enums import sql_in_clause
from src.domain.enums.currency import Currency
from src.domain.enums.payment import PaymentStatus
from src.infrastructure.db.base import Base


class PaymentModel(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint(
            f"currency IN ({sql_in_clause(Currency)})",
            name="ck_payments_currency",
        ),
        CheckConstraint(
            f"status IN ({sql_in_clause(PaymentStatus)})",
            name="ck_payments_status",
        ),
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        Index("ix_payments_created_at", "created_at"),
        Index(
            "ix_payments_webhook_due",
            "webhook_next_retry_at",
            postgresql_where=text(
                "webhook_delivered_at IS NULL AND webhook_next_retry_at IS NOT NULL",
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str] = mapped_column(String(DESCRIPTION_MAX_LENGTH), nullable=False)
    payment_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
    )
    status: Mapped[PaymentStatus] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(IDEMPOTENCY_KEY_MAX_LENGTH),
        nullable=False,
    )
    webhook_url: Mapped[str] = mapped_column(String(WEBHOOK_URL_MAX_LENGTH), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(
        String(ERROR_MESSAGE_MAX_LENGTH),
        nullable=True,
    )
    provider_reference: Mapped[str | None] = mapped_column(
        String(PROVIDER_REFERENCE_MAX_LENGTH),
        nullable=True,
    )
    processing_token: Mapped[str | None] = mapped_column(
        String(PROCESSING_TOKEN_MAX_LENGTH),
        nullable=True,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    webhook_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    webhook_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    webhook_next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
