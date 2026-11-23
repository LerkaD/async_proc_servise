from faststream.rabbit import Channel, RabbitBroker

from src.infrastructure.messaging.constants import (
    CONSUMER_PREFETCH,
    DLQ_ROUTING_KEY,
    PAYMENTS_ROUTING_KEY,
)
from src.infrastructure.messaging.topology import (
    DLQ_QUEUE,
    DLX_EXCHANGE,
    PAYMENTS_EXCHANGE,
    PAYMENTS_QUEUE,
    RETRY_QUEUES,
)


def create_publisher_broker(rabbitmq_url: str) -> RabbitBroker:
    return RabbitBroker(
        rabbitmq_url,
        app_id="payment-outbox",
        default_channel=Channel(publisher_confirms=True, on_return_raises=True),
    )


def create_consumer_broker(rabbitmq_url: str) -> RabbitBroker:
    return RabbitBroker(
        rabbitmq_url,
        app_id="payment-consumer",
        default_channel=Channel(
            prefetch_count=CONSUMER_PREFETCH,
            publisher_confirms=True,
            on_return_raises=True,
        ),
    )


async def declare_topology(broker: RabbitBroker) -> None:
    payments_exchange = await broker.declare_exchange(PAYMENTS_EXCHANGE)
    dead_letter_exchange = await broker.declare_exchange(DLX_EXCHANGE)

    payments_queue = await broker.declare_queue(PAYMENTS_QUEUE)
    await payments_queue.bind(payments_exchange, routing_key=PAYMENTS_ROUTING_KEY)

    dead_letter_queue = await broker.declare_queue(DLQ_QUEUE)
    await dead_letter_queue.bind(dead_letter_exchange, routing_key=DLQ_ROUTING_KEY)

    for queue in RETRY_QUEUES:
        await broker.declare_queue(queue)
