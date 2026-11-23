import asyncio
import logging

from faststream.rabbit import RabbitBroker

from src.application.services.outbox import OutboxRelayService
from src.application.protocols.uow import UnitOfWorkFactory
from src.domain.value_objects.outbox_message import OutboxMessage
from src.infrastructure.messaging.constants import PAYMENTS_ROUTING_KEY
from src.infrastructure.messaging.topology import PAYMENTS_EXCHANGE
from src.infrastructure.workers.constants import (
    OUTBOX_BATCH_SIZE,
    OUTBOX_FAILED_RETRY_SECONDS,
    OUTBOX_MAX_RETRIES,
    OUTBOX_POLL_INTERVAL,
    OUTBOX_PUBLISH_TIMEOUT,
    OUTBOX_RETRY_BASE_SECONDS,
    OUTBOX_RETRY_MAX_SECONDS,
)

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        broker: RabbitBroker,
    ) -> None:
        self._uow_factory = uow_factory
        self._broker = broker

    async def publish(self, event: OutboxMessage) -> None:
        await self._broker.publish(
            event.payload,
            exchange=PAYMENTS_EXCHANGE,
            routing_key=PAYMENTS_ROUTING_KEY,
            persist=True,
            mandatory=True,
            timeout=OUTBOX_PUBLISH_TIMEOUT,
            message_id=str(event.id),
            message_type=event.event_type,
        )

    async def publish_batch(self) -> int:
        service = OutboxRelayService(self._uow_factory)
        revived = await service.revive_failed(limit=OUTBOX_BATCH_SIZE)
        if revived:
            logger.info("outbox relay queued %s failed event(s) again", revived)
        published = await service.publish_pending(
            self,
            batch_size=OUTBOX_BATCH_SIZE,
            base_delay=OUTBOX_RETRY_BASE_SECONDS,
            max_delay=OUTBOX_RETRY_MAX_SECONDS,
            max_retries=OUTBOX_MAX_RETRIES,
            lease_seconds=OUTBOX_PUBLISH_TIMEOUT + OUTBOX_RETRY_BASE_SECONDS,
            failed_retry_seconds=OUTBOX_FAILED_RETRY_SECONDS,
        )
        if published:
            logger.info("outbox relay published %s event(s)", published)
        return published

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                published = await self.publish_batch()
            except Exception:
                logger.exception("outbox relay iteration failed")
                published = 0
            if stop.is_set():
                return
            if published:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=OUTBOX_POLL_INTERVAL)
            except TimeoutError:
                continue
