from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
from faststream.rabbit import RabbitBroker
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.application.services.payment import PaymentService
from src.application.services.processing import PaymentProcessingService
from src.infrastructure.clients.constants import (
    WEBHOOK_CONNECT_TIMEOUT,
    WEBHOOK_MAX_CONNECTIONS,
    WEBHOOK_TIMEOUT,
)
from src.infrastructure.clients.gateway import PaymentGateway
from src.infrastructure.clients.webhook import WebhookService, build_webhook_client
from src.infrastructure.config import Settings
from src.infrastructure.db.session import create_engine, create_session_factory
from src.infrastructure.db.uow import SqlaUnitOfWorkFactory
from src.infrastructure.messaging.rabbit import (
    create_consumer_broker,
    create_publisher_broker,
    declare_topology,
)
from src.infrastructure.workers.constants import (
    OUTBOX_PUBLISH_TIMEOUT,
    PROCESSING_LEASE_SECONDS,
    WEBHOOK_RETRY_BASE_SECONDS,
    WEBHOOK_RETRY_MAX_SECONDS,
)
from src.infrastructure.workers.outbox_relay import OutboxRelay


@dataclass
class AppContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    uow_factory: SqlaUnitOfWorkFactory
    payment_service: PaymentService
    dispose_engine: bool = True
    publisher_broker: RabbitBroker | None = None
    outbox_relay: OutboxRelay | None = None
    _stop: asyncio.Event | None = field(default=None, init=False, repr=False)
    _relay_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    @classmethod
    def create(
        cls,
        settings: Settings,
        *,
        engine: AsyncEngine | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        dispose_engine: bool | None = None,
    ) -> AppContainer:
        owns_engine = engine is None
        resolved_engine = engine or create_engine(settings.database_url)
        resolved_factory = session_factory or create_session_factory(resolved_engine)
        uow_factory = SqlaUnitOfWorkFactory(resolved_factory)
        return cls(
            settings=settings,
            engine=resolved_engine,
            session_factory=resolved_factory,
            uow_factory=uow_factory,
            payment_service=PaymentService(uow_factory),
            dispose_engine=owns_engine if dispose_engine is None else dispose_engine,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> AppContainer:
        return cls.create(settings)

    async def startup(self) -> None:
        if not self.settings.outbox_relay_enabled:
            return
        self.publisher_broker = create_publisher_broker(self.settings.rabbitmq_url)
        await self.publisher_broker.connect()
        await declare_topology(self.publisher_broker)
        self.outbox_relay = OutboxRelay(self.uow_factory, self.publisher_broker)
        self._stop = asyncio.Event()
        self._relay_task = asyncio.create_task(
            self.outbox_relay.run(self._stop),
            name="outbox-relay",
        )

    async def shutdown(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._relay_task is not None:
            try:
                await asyncio.wait_for(self._relay_task, timeout=OUTBOX_PUBLISH_TIMEOUT)
            except TimeoutError:
                self._relay_task.cancel()
                await asyncio.gather(self._relay_task, return_exceptions=True)
            self._relay_task = None
        self._stop = None
        if self.publisher_broker is not None:
            await self.publisher_broker.stop()
            self.publisher_broker = None
        self.outbox_relay = None
        if self.dispose_engine:
            await self.engine.dispose()


@dataclass
class WorkerContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    uow_factory: SqlaUnitOfWorkFactory
    broker: RabbitBroker
    http_client: httpx.AsyncClient
    processing: PaymentProcessingService
    dispose_engine: bool = True

    @classmethod
    def create(
        cls,
        settings: Settings,
        broker: RabbitBroker,
        *,
        engine: AsyncEngine | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        dispose_engine: bool | None = None,
    ) -> WorkerContainer:
        owns_engine = engine is None
        resolved_engine = engine or create_engine(settings.database_url)
        resolved_factory = session_factory or create_session_factory(resolved_engine)
        uow_factory = SqlaUnitOfWorkFactory(resolved_factory)
        http_client = build_webhook_client(
            connect_timeout=WEBHOOK_CONNECT_TIMEOUT,
            read_timeout=WEBHOOK_TIMEOUT,
            max_connections=WEBHOOK_MAX_CONNECTIONS,
        )
        webhooks = WebhookService(http_client, settings.webhook_secret)
        processing = PaymentProcessingService(
            uow_factory,
            PaymentGateway(),
            webhooks,
            lease_seconds=PROCESSING_LEASE_SECONDS,
            webhook_retry_base_seconds=WEBHOOK_RETRY_BASE_SECONDS,
            webhook_retry_max_seconds=WEBHOOK_RETRY_MAX_SECONDS,
        )
        return cls(
            settings=settings,
            engine=resolved_engine,
            session_factory=resolved_factory,
            uow_factory=uow_factory,
            broker=broker,
            http_client=http_client,
            processing=processing,
            dispose_engine=owns_engine if dispose_engine is None else dispose_engine,
        )

    @classmethod
    def from_settings(cls, settings: Settings, broker: RabbitBroker) -> WorkerContainer:
        return cls.create(settings, broker)

    async def startup(self) -> None:
        await self.broker.connect()
        await declare_topology(self.broker)

    async def shutdown(self) -> None:
        await self.http_client.aclose()
        if self.dispose_engine:
            await self.engine.dispose()


def create_worker_broker(settings: Settings) -> RabbitBroker:
    return create_consumer_broker(settings.rabbitmq_url)
