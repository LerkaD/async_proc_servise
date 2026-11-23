from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.outbox import OutboxEvent
from src.domain.enums.outbox import OutboxStatus
from src.infrastructure.db.mappers.outbox import apply_outbox, outbox_to_entity, outbox_to_model
from src.infrastructure.db.models.outbox import OutboxEventModel


class SqlaOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tracked: dict[uuid.UUID, tuple[OutboxEventModel, OutboxEvent]] = {}

    async def add(self, event: OutboxEvent) -> OutboxEvent:
        model = outbox_to_model(event)
        self._session.add(model)
        await self._session.flush()
        entity = outbox_to_entity(model)
        self._tracked[entity.id] = (model, entity)
        return entity

    async def get_for_update(self, event_id: uuid.UUID) -> OutboxEvent | None:
        statement = (
            select(OutboxEventModel)
            .where(OutboxEventModel.id == event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._track(model)

    async def claim_pending(self, *, limit: int, now: datetime) -> list[OutboxEvent]:
        statement = (
            select(OutboxEventModel)
            .where(
                OutboxEventModel.status == OutboxStatus.PENDING,
                or_(
                    OutboxEventModel.next_retry_at.is_(None),
                    OutboxEventModel.next_retry_at <= now,
                ),
            )
            .order_by(OutboxEventModel.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        return [self._track(model) for model in result.scalars().all()]

    async def claim_failed(self, *, limit: int, now: datetime) -> list[OutboxEvent]:
        statement = (
            select(OutboxEventModel)
            .where(
                OutboxEventModel.status == OutboxStatus.FAILED,
                OutboxEventModel.next_retry_at.is_not(None),
                OutboxEventModel.next_retry_at <= now,
            )
            .order_by(OutboxEventModel.next_retry_at, OutboxEventModel.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        return [self._track(model) for model in result.scalars().all()]

    def sync_all(self) -> None:
        for model, entity in self._tracked.values():
            apply_outbox(entity, model)

    def _track(self, model: OutboxEventModel) -> OutboxEvent:
        entity = outbox_to_entity(model)
        self._tracked[entity.id] = (model, entity)
        return entity
