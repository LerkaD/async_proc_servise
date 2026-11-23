from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

from src.domain.clock import utcnow
from src.domain.constants import ERROR_MESSAGE_MAX_LENGTH
from src.domain.enums.outbox import OutboxStatus
from src.application.protocols.outbox_publisher import OutboxPublisher
from src.application.protocols.uow import UnitOfWorkFactory
from src.domain.value_objects.outbox_message import OutboxMessage


class OutboxRelayService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def revive_failed(self, *, limit: int) -> int:
        async with self._uow_factory() as uow:
            events = await uow.outbox.claim_failed(limit=limit, now=utcnow())
            for event in events:
                event.status = OutboxStatus.PENDING
                event.retry_count = 0
                event.next_retry_at = None
            return len(events)

    async def publish_pending(
        self,
        publisher: OutboxPublisher,
        *,
        batch_size: int,
        base_delay: float,
        max_delay: float,
        max_retries: int,
        lease_seconds: float,
        failed_retry_seconds: float = 60.0,
    ) -> int:
        claimed = await self._claim(batch_size, lease_seconds)
        published = 0
        remaining = list(claimed)
        while remaining:
            await self._lease([item.id for item in remaining], lease_seconds)
            message = remaining.pop(0)
            try:
                await publisher.publish(message)
            except Exception as exc:
                await self._defer(
                    message.id,
                    exc,
                    base_delay=base_delay,
                    max_delay=max_delay,
                    max_retries=max_retries,
                    failed_retry_seconds=failed_retry_seconds,
                )
                continue
            if await self._mark_published(message.id):
                published += 1
        return published

    async def _claim(self, batch_size: int, lease_seconds: float) -> list[OutboxMessage]:
        async with self._uow_factory() as uow:
            events = await uow.outbox.claim_pending(limit=batch_size, now=utcnow())
            claimed = [
                OutboxMessage(
                    id=event.id,
                    event_type=event.event_type,
                    payload=dict(event.payload),
                )
                for event in events
            ]
            until = utcnow() + timedelta(seconds=lease_seconds)
            for event in events:
                event.next_retry_at = until
            return claimed

    async def _lease(self, event_ids: Sequence[uuid.UUID], lease_seconds: float) -> None:
        if not event_ids:
            return
        until = utcnow() + timedelta(seconds=lease_seconds)
        async with self._uow_factory() as uow:
            for event_id in event_ids:
                event = await uow.outbox.get_for_update(event_id)
                if event is not None and event.status == OutboxStatus.PENDING:
                    event.next_retry_at = until

    async def _mark_published(self, event_id: uuid.UUID) -> bool:
        async with self._uow_factory() as uow:
            event = await uow.outbox.get_for_update(event_id)
            if event is None or event.status != OutboxStatus.PENDING:
                return False
            event.status = OutboxStatus.PUBLISHED
            event.published_at = utcnow()
            event.last_error = None
            event.next_retry_at = None
        return True

    async def _defer(
        self,
        event_id: uuid.UUID,
        exc: Exception,
        *,
        base_delay: float,
        max_delay: float,
        max_retries: int,
        failed_retry_seconds: float,
    ) -> None:
        async with self._uow_factory() as uow:
            event = await uow.outbox.get_for_update(event_id)
            if event is None or event.status != OutboxStatus.PENDING:
                return
            event.retry_count += 1
            event.last_error = str(exc)[:ERROR_MESSAGE_MAX_LENGTH]
            if event.retry_count >= max_retries:
                event.status = OutboxStatus.FAILED
                event.next_retry_at = utcnow() + timedelta(seconds=failed_retry_seconds)
                return
            delay = min(base_delay * (2 ** (event.retry_count - 1)), max_delay)
            event.next_retry_at = utcnow() + timedelta(seconds=delay)
            event.status = OutboxStatus.PENDING
