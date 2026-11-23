from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

from src.domain.retry import retry_delay_seconds
from src.infrastructure.messaging.constants import (
    CONSUMER_MAX_ATTEMPTS,
    DLQ_QUEUE_NAME,
    DLQ_ROUTING_KEY,
    DLX_EXCHANGE_NAME,
    PAYMENTS_EXCHANGE_NAME,
    PAYMENTS_ROUTING_KEY,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    RETRY_QUEUE_PREFIX,
)

PAYMENTS_EXCHANGE = RabbitExchange(
    PAYMENTS_EXCHANGE_NAME,
    type=ExchangeType.DIRECT,
    durable=True,
)
DLX_EXCHANGE = RabbitExchange(
    DLX_EXCHANGE_NAME,
    type=ExchangeType.DIRECT,
    durable=True,
)

PAYMENTS_QUEUE = RabbitQueue(
    PAYMENTS_ROUTING_KEY,
    durable=True,
    routing_key=PAYMENTS_ROUTING_KEY,
    arguments={
        "x-dead-letter-exchange": DLX_EXCHANGE_NAME,
        "x-dead-letter-routing-key": DLQ_ROUTING_KEY,
    },
)

DLQ_QUEUE = RabbitQueue(
    DLQ_QUEUE_NAME,
    durable=True,
    routing_key=DLQ_ROUTING_KEY,
)


def build_retry_queues(max_attempts: int, *, base: float, cap: float) -> tuple[RabbitQueue, ...]:
    queues: list[RabbitQueue] = []
    for attempt in range(1, max_attempts):
        delay_ms = int(retry_delay_seconds(attempt, base=base, cap=cap) * 1000)
        queues.append(
            RabbitQueue(
                f"{RETRY_QUEUE_PREFIX}.{attempt}",
                durable=True,
                arguments={
                    "x-message-ttl": delay_ms,
                    "x-dead-letter-exchange": PAYMENTS_EXCHANGE_NAME,
                    "x-dead-letter-routing-key": PAYMENTS_ROUTING_KEY,
                },
            ),
        )
    return tuple(queues)


RETRY_QUEUES = build_retry_queues(
    CONSUMER_MAX_ATTEMPTS,
    base=RETRY_BASE_SECONDS,
    cap=RETRY_MAX_SECONDS,
)
