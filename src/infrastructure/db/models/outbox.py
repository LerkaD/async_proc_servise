import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.constants import ERROR_MESSAGE_MAX_LENGTH
from src.domain.enums import sql_in_clause
from src.domain.enums.outbox import OutboxStatus
from src.infrastructure.db.base import Base


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({sql_in_clause(OutboxStatus)})",
            name="ck_outbox_status",
        ),
        Index(
            "ix_outbox_pending_claim",
            "next_retry_at",
            "created_at",
            postgresql_where=text(f"status = '{OutboxStatus.PENDING.value}'"),
        ),
        Index(
            "ix_outbox_failed_retry",
            "next_retry_at",
            "created_at",
            postgresql_where=text(f"status = '{OutboxStatus.FAILED.value}'"),
        ),
        Index("ix_outbox_aggregate_id", "aggregate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(String(32), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(
        String(ERROR_MESSAGE_MAX_LENGTH),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
