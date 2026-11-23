import asyncio
import logging
from typing import Any

from aio_pika.abc import HeadersType
from faststream import FastStream
from faststream.rabbit import RabbitMessage

from src.application.dto.events import DeadLetterEvent, PaymentCreatedEvent
from src.application.exceptions import PaymentNotFound, ProcessingBusy, TransientProcessingError
from src.application.services.processing import PaymentProcessingService
from src.domain.constants import ERROR_MESSAGE_MAX_LENGTH
from src.infrastructure.clients.constants import WEBHOOK_TIMEOUT
from src.infrastructure.config import get_settings
from src.infrastructure.constants import LOG_LEVEL
from src.infrastructure.container import WorkerContainer, create_worker_broker
from src.infrastructure.messaging.constants import (
    CONSUMER_MAX_ATTEMPTS,
    DLQ_ROUTING_KEY,
    HEADER_ATTEMPT,
    HEADER_PROCESSING_TOKEN,
)
from src.infrastructure.messaging.topology import (
    DLQ_QUEUE,
    DLX_EXCHANGE,
    PAYMENTS_EXCHANGE,
    PAYMENTS_QUEUE,
    RETRY_QUEUES,
)
from src.infrastructure.workers.constants import WEBHOOK_POLL_INTERVAL

logger = logging.getLogger(__name__)
settings = get_settings()

broker = create_worker_broker(settings)
app = FastStream(broker)


class PaymentConsumer:
    """Rabbit consumer: process payment events, retry, and dead-letter."""

    def __init__(
        self,
        processing: PaymentProcessingService,
        broker_ref: Any,
    ) -> None:
        self._processing = processing
        self._broker = broker_ref

    async def handle_delivery(self, event: PaymentCreatedEvent, message: RabbitMessage) -> None:
        headers = message.headers or {}
        raw_attempt = headers.get(HEADER_ATTEMPT, 1)
        try:
            attempt = max(1, int(raw_attempt))
        except (TypeError, ValueError):
            attempt = 1
        raw_token = headers.get(HEADER_PROCESSING_TOKEN)
        token = None if raw_token is None or raw_token == "" else str(raw_token)

        try:
            await self._processing.handle(event.payment_id, processing_token=token)
        except ProcessingBusy as exc:
            logger.info(
                "payment %s is busy, retrying without consuming an attempt",
                event.payment_id,
            )
            await self.schedule_retry(
                event,
                attempt=attempt,
                token=exc.processing_token,
                queue_index=0,
            )
        except TransientProcessingError as exc:
            logger.warning(
                "payment %s attempt %s failed: %s",
                event.payment_id,
                attempt,
                exc.message,
            )
            await self.retry_or_dead_letter(
                event,
                attempt=attempt,
                token=exc.processing_token,
                reason=exc.message,
            )
        except PaymentNotFound:
            await self.dead_letter(event, attempt, "payment not found")
        except Exception as exc:
            logger.exception("payment %s attempt %s failed", event.payment_id, attempt)
            await self.retry_or_dead_letter(
                event,
                attempt=attempt,
                token=token,
                reason=str(exc),
            )
        else:
            logger.info("payment %s processed", event.payment_id)

    async def handle_dead(self, event: DeadLetterEvent) -> None:
        logger.error(
            "finalizing dead letter for payment %s: %s",
            event.payment_id,
            event.error,
        )
        await self._processing.mark_exhausted(event.payment_id, event.error)

    async def retry_webhooks(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                delivered = await self._processing.deliver_due_webhooks()
            except Exception:
                logger.exception("webhook recovery failed")
                delivered = 0
            if stop.is_set():
                return
            if delivered:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=WEBHOOK_POLL_INTERVAL)
            except TimeoutError:
                continue

    async def retry_or_dead_letter(
        self,
        event: PaymentCreatedEvent,
        *,
        attempt: int,
        token: str | None,
        reason: str,
    ) -> None:
        if attempt >= CONSUMER_MAX_ATTEMPTS or not RETRY_QUEUES:
            await self.dead_letter(event, attempt, reason)
            return
        await self.schedule_retry(
            event,
            attempt=attempt + 1,
            token=token,
            queue_index=attempt - 1,
        )

    async def schedule_retry(
        self,
        event: PaymentCreatedEvent,
        *,
        attempt: int,
        token: str | None,
        queue_index: int,
    ) -> None:
        if not RETRY_QUEUES:
            await self.dead_letter(event, attempt, "retry budget is exhausted")
            return
        index = min(max(queue_index, 0), len(RETRY_QUEUES) - 1)
        headers: HeadersType = {HEADER_ATTEMPT: attempt}
        if token:
            headers[HEADER_PROCESSING_TOKEN] = token
        await self._broker.publish(
            {"payment_id": str(event.payment_id)},
            queue=RETRY_QUEUES[index],
            persist=True,
            mandatory=True,
            headers=headers,
        )

    async def dead_letter(
        self,
        event: PaymentCreatedEvent,
        attempt: int,
        reason: str,
    ) -> None:
        try:
            await self._processing.mark_exhausted(event.payment_id, reason)
        except Exception:
            logger.exception("failed to finalize payment %s", event.payment_id)
        await self._broker.publish(
            {
                "payment_id": str(event.payment_id),
                "attempts": attempt,
                "error": reason[:ERROR_MESSAGE_MAX_LENGTH],
            },
            exchange=DLX_EXCHANGE,
            routing_key=DLQ_ROUTING_KEY,
            persist=True,
            mandatory=True,
        )
        logger.error(
            "payment %s moved to %s after %s attempt(s): %s",
            event.payment_id,
            DLQ_ROUTING_KEY,
            attempt,
            reason,
        )


container: WorkerContainer | None = None
consumer: PaymentConsumer | None = None
_stop: asyncio.Event | None = None
_webhook_task: asyncio.Task[None] | None = None


@app.on_startup
async def open_clients() -> None:
    global container, consumer, _stop, _webhook_task
    container = WorkerContainer.from_settings(settings, broker)
    consumer = PaymentConsumer(container.processing, broker)
    await container.startup()
    _stop = asyncio.Event()
    _webhook_task = asyncio.create_task(
        consumer.retry_webhooks(_stop),
        name="webhook-retry",
    )


@app.on_shutdown
async def close_clients() -> None:
    global container, consumer, _stop, _webhook_task
    if _stop is not None:
        _stop.set()
    if _webhook_task is not None:
        try:
            await asyncio.wait_for(_webhook_task, timeout=WEBHOOK_TIMEOUT + 1)
        except TimeoutError:
            _webhook_task.cancel()
            await asyncio.gather(_webhook_task, return_exceptions=True)
        _webhook_task = None
    _stop = None
    if container is not None:
        await container.shutdown()
        container = None
    consumer = None


@broker.subscriber(PAYMENTS_QUEUE, PAYMENTS_EXCHANGE)
async def on_payment_created(event: PaymentCreatedEvent, message: RabbitMessage) -> None:
    if consumer is None:
        raise RuntimeError("consumer is not initialized")
    await consumer.handle_delivery(event, message)


@broker.subscriber(DLQ_QUEUE, DLX_EXCHANGE)
async def on_payment_dead(event: DeadLetterEvent) -> None:
    if consumer is None:
        raise RuntimeError("consumer is not initialized")
    await consumer.handle_dead(event)


def main() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
