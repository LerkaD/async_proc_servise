from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.payment import Payment
from src.domain.enums.payment import PaymentStatus
from src.infrastructure.db.mappers.payment import (
    apply_payment,
    payment_to_entity,
    payment_to_model,
)
from src.infrastructure.db.models.payment import PaymentModel


class SqlaPaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tracked: dict[uuid.UUID, tuple[PaymentModel, Payment]] = {}

    async def add(self, payment: Payment) -> Payment:
        model = payment_to_model(payment)
        self._session.add(model)
        await self._session.flush()
        entity = payment_to_entity(model)
        self._tracked[entity.id] = (model, entity)
        return entity

    async def get(self, payment_id: uuid.UUID) -> Payment | None:
        model = await self._session.get(PaymentModel, payment_id)
        if model is None:
            return None
        return self._track(model)

    async def get_for_update(self, payment_id: uuid.UUID) -> Payment | None:
        statement = (
            select(PaymentModel)
            .where(PaymentModel.id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._track(model)

    async def claim_due_webhooks(self, *, limit: int, now: datetime) -> list[Payment]:
        statement = (
            select(PaymentModel)
            .where(
                PaymentModel.status.in_((PaymentStatus.SUCCEEDED, PaymentStatus.FAILED)),
                PaymentModel.webhook_delivered_at.is_(None),
                PaymentModel.webhook_next_retry_at.is_not(None),
                PaymentModel.webhook_next_retry_at <= now,
            )
            .order_by(PaymentModel.webhook_next_retry_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        return [self._track(model) for model in result.scalars().all()]

    def sync_all(self) -> None:
        for model, entity in self._tracked.values():
            apply_payment(entity, model)

    def _track(self, model: PaymentModel) -> Payment:
        entity = payment_to_entity(model)
        self._tracked[entity.id] = (model, entity)
        return entity
