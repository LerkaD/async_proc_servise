from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Generator
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.application.schemas.payment import PaymentCreateSchema
from src.application.services.payment import PaymentService
from src.application.services.processing import PaymentProcessingService
from src.domain.clock import utcnow
from src.domain.entities.payment import Payment
from src.domain.enums.payment import PaymentStatus
from src.domain.value_objects.webhook import WebhookNotification
from src.infrastructure.clients.gateway import PaymentGateway
from src.infrastructure.db.repositories.payment import SqlaPaymentRepository
from src.infrastructure.db.uow import SqlaUnitOfWorkFactory
from tests.support import FakeGateway, FakeWebhook

SessionFactory = async_sessionmaker[AsyncSession]


def pytest_configure(config: pytest.Config) -> None:
    del config
    base_url = os.environ.get(
        "PYTEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://payment:payment@localhost:5432/payment",
        ),
    )
    url = make_url(base_url)
    database = url.database or "payment"
    if not database.endswith("_test"):
        database = f"{database}_test"
    os.environ["DATABASE_URL"] = str(url.set(database=database))
    os.environ["OUTBOX_RELAY_ENABLED"] = "false"
    os.environ["API_KEY"] = "test-api-key"
    os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
    os.environ["GATEWAY_DELAY_MIN"] = "0"
    os.environ["GATEWAY_DELAY_MAX"] = "0"
    os.environ["GATEWAY_SUCCESS_RATE"] = "1"
    os.environ["GATEWAY_TIMEOUT_RATE"] = "0"
    os.environ["DB_POOL_SIZE"] = "20"
    os.environ["RABBITMQ_URL"] = "amqp://guest:guest@localhost:5672/"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        layer = item.path.parent.name
        if layer == "unit":
            item.add_marker(pytest.mark.unit)
            item.add_marker(pytest.mark.nodb)
        elif layer == "integration":
            item.add_marker(pytest.mark.integration)
        elif layer == "e2e":
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(autouse=True)
def public_webhook_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(
        host: str,
        port: int,
    ) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        del port
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(
        "src.application.validators.public_url.resolve_addresses",
        resolve,
    )


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
async def admin_connection(database_url: str) -> AsyncIterator[asyncpg.Connection]:
    url = make_url(database_url)
    connection = await asyncpg.connect(
        host=url.host or "localhost",
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database="postgres",
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture(scope="session")
async def postgres_database(
    database_url: str,
    admin_connection: asyncpg.Connection,
) -> AsyncIterator[str]:
    database = make_url(database_url).database
    if database is None or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database) is None:
        raise RuntimeError(f"refusing to create database {database!r}")

    exists = await admin_connection.fetchval(
        "SELECT 1 FROM pg_database WHERE datname = $1",
        database,
    )
    if exists is None:
        await admin_connection.execute(f'CREATE DATABASE "{database}"')
    try:
        yield database_url
    finally:
        await admin_connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            database,
        )
        await admin_connection.execute(f'DROP DATABASE IF EXISTS "{database}"')


@pytest.fixture(scope="session")
def alembic_upgrade(postgres_database: str) -> None:
    del postgres_database
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        output = f"{completed.stdout}\n{completed.stderr}".strip()
        pytest.fail(f"alembic upgrade head failed\n{output}")


@pytest.fixture(scope="session")
async def engine(
    database_url: str,
    alembic_upgrade: None,
) -> AsyncIterator[AsyncEngine]:
    del alembic_upgrade
    db_engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        yield db_engine
    finally:
        await db_engine.dispose()


@pytest.fixture
async def session_factory(
    engine: AsyncEngine,
    request: pytest.FixtureRequest,
) -> AsyncIterator[SessionFactory]:
    if request.node.get_closest_marker("commit") is not None:
        factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=True,
        )
        yield factory
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE outbox_events, idempotency_keys, payments "
                    "RESTART IDENTITY CASCADE",
                ),
            )
        return

    connection = await engine.connect()
    trans = await connection.begin()
    factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
        autoflush=True,
    )
    try:
        yield factory
    finally:
        await trans.rollback()
        await connection.close()


@pytest.fixture
async def session(session_factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session


@pytest.fixture
async def client(
    session_factory: SessionFactory,
    engine: AsyncEngine,
) -> AsyncIterator[AsyncClient]:
    from src.infrastructure.config import get_settings
    from src.infrastructure.container import AppContainer
    from src.presentation.main import create_app

    get_settings.cache_clear()
    container = AppContainer.create(
        get_settings(),
        engine=engine,
        session_factory=session_factory,
        dispose_engine=False,
    )
    # httpx ASGITransport does not run lifespan; inject the composition root directly.
    application = create_app(container=container)
    application.state.container = container
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest.fixture
def payload() -> Callable[..., dict[str, object]]:
    def build(**overrides: object) -> dict[str, object]:
        body: dict[str, object] = {
            "amount": "10.50",
            "currency": "RUB",
            "description": "Order 1001",
            "metadata": {"order_id": "1001"},
            "webhook_url": "https://example.com/webhooks/payments",
        }
        body.update(overrides)
        return body

    return build


@pytest.fixture
def headers() -> Callable[..., dict[str, str]]:
    missing = object()

    def build(
        key: str | None = None,
        *,
        api_key: str | None | object = missing,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        resolved = os.environ["API_KEY"] if api_key is missing else api_key
        if isinstance(resolved, str):
            result["X-API-Key"] = resolved
        if key is not None:
            result["Idempotency-Key"] = key
        return result

    return build


@pytest.fixture
def count() -> Callable[[AsyncSession, type[object]], Awaitable[int]]:
    async def query(db_session: AsyncSession, model: type[object]) -> int:
        result = await db_session.execute(select(func.count()).select_from(model))
        return int(result.scalar_one())

    return query


@pytest.fixture
def payment_service(session_factory: SessionFactory) -> PaymentService:
    return PaymentService(SqlaUnitOfWorkFactory(session_factory))


@pytest.fixture
def make_gateway() -> Callable[..., PaymentGateway]:
    def build(**overrides: float) -> PaymentGateway:
        return PaymentGateway(
            success_rate=overrides.get("gateway_success_rate", 1.0),
            timeout_rate=overrides.get("gateway_timeout_rate", 0.0),
            delay_min=0.0,
            delay_max=0.0,
        )

    return build


@pytest.fixture
def processing(
    session_factory: SessionFactory,
) -> Callable[..., PaymentProcessingService]:
    def build(
        gateway: FakeGateway,
        webhooks: FakeWebhook,
        *,
        lease_seconds: float = 60,
        webhook_retry_base_seconds: float = 2.0,
        webhook_retry_max_seconds: float = 60.0,
    ) -> PaymentProcessingService:
        return PaymentProcessingService(
            SqlaUnitOfWorkFactory(session_factory),
            gateway,
            webhooks,
            lease_seconds=lease_seconds,
            webhook_retry_base_seconds=webhook_retry_base_seconds,
            webhook_retry_max_seconds=webhook_retry_max_seconds,
        )

    return build


@pytest.fixture
def create_payment(
    payment_service: PaymentService,
    payload: Callable[..., dict[str, object]],
) -> Callable[[], Awaitable[uuid.UUID]]:
    async def create() -> uuid.UUID:
        body, status_code = await payment_service.create_payment(
            PaymentCreateSchema.model_validate(payload()),
            f"svc-{uuid.uuid4().hex}",
        )
        assert status_code == status.HTTP_202_ACCEPTED
        return uuid.UUID(str(body["payment_id"]))

    return create


@pytest.fixture
def insert_payment() -> Callable[[AsyncSession], Awaitable[uuid.UUID]]:
    async def insert(db_session: AsyncSession) -> uuid.UUID:
        now = utcnow()
        payment_id = uuid.uuid4()
        await SqlaPaymentRepository(db_session).add(
            Payment(
                id=payment_id,
                amount=Decimal("10.50"),
                currency="RUB",
                description="locked",
                payment_metadata={},
                status=PaymentStatus.PENDING,
                idempotency_key=f"lock-{payment_id.hex}",
                webhook_url="https://example.com/webhooks/payments",
                failure_reason=None,
                provider_reference=None,
                processing_token=None,
                processing_started_at=None,
                webhook_delivered_at=None,
                webhook_attempts=0,
                webhook_next_retry_at=None,
                processed_at=None,
                created_at=now,
                updated_at=now,
            ),
        )
        return payment_id

    return insert


@pytest.fixture
def payment_state(
    session_factory: SessionFactory,
) -> Callable[[uuid.UUID], Awaitable[tuple[str, object, str | None]]]:
    async def read(payment_id: uuid.UUID) -> tuple[str, object, str | None]:
        async with session_factory() as db_session, db_session.begin():
            payment = await SqlaPaymentRepository(db_session).get(payment_id)
            assert payment is not None
            value = (
                payment.status.value
                if isinstance(payment.status, PaymentStatus)
                else str(payment.status)
            )
            return value, payment.webhook_delivered_at, payment.failure_reason

    return read


@pytest.fixture
def checked_out(engine: AsyncEngine) -> Callable[[], int]:
    def read() -> int:
        return int(engine.pool.checkedout())

    return read


@pytest.fixture
def sample_notification() -> WebhookNotification:
    return WebhookNotification(
        payment_id=uuid.uuid4(),
        status="succeeded",
        amount=Decimal("10.50"),
        currency="RUB",
        processed_at=None,
        failure_reason=None,
    )
