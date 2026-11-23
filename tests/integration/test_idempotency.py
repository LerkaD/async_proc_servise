from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.schemas.payment import PaymentCreateSchema
from src.application.services.payment import PaymentService
from src.domain.entities.outbox import OutboxEvent
from src.infrastructure.db.models.idempotency import IdempotencyKeyModel
from src.infrastructure.db.models.outbox import OutboxEventModel
from src.infrastructure.db.models.payment import PaymentModel
from src.infrastructure.db.repositories.outbox import SqlaOutboxRepository

SessionFactory = async_sessionmaker[AsyncSession]


async def test_outbox_failure_rolls_back_payment_and_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: SessionFactory,
    payment_service: PaymentService,
    payload: Callable[..., dict[str, object]],
    count: Callable[[AsyncSession, type[object]], Awaitable[int]],
) -> None:
    async def boom(self: SqlaOutboxRepository, event: OutboxEvent) -> OutboxEvent:
        del self, event
        raise RuntimeError("outbox down")

    monkeypatch.setattr(SqlaOutboxRepository, "add", boom)

    with pytest.raises(RuntimeError, match="outbox down"):
        await payment_service.create_payment(
            PaymentCreateSchema.model_validate(payload()),
            f"rollback-{uuid.uuid4().hex}",
        )

    async with session_factory() as session, session.begin():
        assert await count(session, PaymentModel) == 0
        assert await count(session, IdempotencyKeyModel) == 0
        assert await count(session, OutboxEventModel) == 0
