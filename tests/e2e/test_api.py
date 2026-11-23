from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

import httpx
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.enums.outbox import OutboxStatus
from src.infrastructure.db.models.idempotency import IdempotencyKeyModel
from src.infrastructure.db.models.outbox import OutboxEventModel
from src.infrastructure.db.models.payment import PaymentModel

SessionFactory = async_sessionmaker[AsyncSession]
Payload = Callable[..., dict[str, object]]
Headers = Callable[..., dict[str, str]]
Count = Callable[[AsyncSession, type[object]], Awaitable[int]]


async def test_create_payment_persists_outbox_and_idempotency(
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    payload: Payload,
    headers: Headers,
    count: Count,
) -> None:
    response = await client.post(
        "/api/v1/payments",
        json=payload(),
        headers=headers(f"create-{uuid.uuid4().hex}"),
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    body = response.json()
    assert body["status"] == "pending"
    assert body["payment_id"]

    async with session_factory() as session, session.begin():
        assert await count(session, PaymentModel) == 1
        assert await count(session, IdempotencyKeyModel) == 1
        events = (await session.execute(select(OutboxEventModel))).scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "payment.created"
        assert events[0].status == OutboxStatus.PENDING
        assert events[0].payload["payment_id"] == body["payment_id"]


async def test_get_returns_created_payment(
    client: httpx.AsyncClient,
    payload: Payload,
    headers: Headers,
) -> None:
    created = await client.post(
        "/api/v1/payments",
        json=payload(),
        headers=headers(f"get-{uuid.uuid4().hex}"),
    )
    payment_id = created.json()["payment_id"]

    response = await client.get(f"/api/v1/payments/{payment_id}", headers=headers())

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["payment_id"] == payment_id
    assert body["status"] == "pending"
    assert body["currency"] == "RUB"
    assert Decimal(str(body["amount"])) == Decimal("10.50")
    assert body["metadata"] == {"order_id": "1001"}
    assert body["processed_at"] is None


async def test_rejects_bad_credentials_body_and_unknown_payment(
    client: httpx.AsyncClient,
    payload: Payload,
    headers: Headers,
) -> None:
    missing_key = await client.post(
        "/api/v1/payments",
        json=payload(),
        headers=headers(api_key=None),
    )
    wrong_key = await client.post(
        "/api/v1/payments",
        json=payload(),
        headers=headers(key="missing-auth", api_key="nope"),
    )
    missing_idempotency = await client.post(
        "/api/v1/payments",
        json=payload(),
        headers=headers(),
    )
    invalid_currency = await client.post(
        "/api/v1/payments",
        json=payload(currency="GBP"),
        headers=headers("bad-currency"),
    )
    missing = await client.get(f"/api/v1/payments/{uuid.uuid4()}", headers=headers())

    assert missing_key.status_code == status.HTTP_401_UNAUTHORIZED
    assert wrong_key.status_code == status.HTTP_401_UNAUTHORIZED
    assert missing_idempotency.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert invalid_currency.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert missing.status_code == status.HTTP_404_NOT_FOUND


async def test_float_amount_is_rejected(
    client: httpx.AsyncClient,
    payload: Payload,
    headers: Headers,
) -> None:
    response = await client.post(
        "/api/v1/payments",
        json=payload(amount=10.5),
        headers=headers(f"float-{uuid.uuid4().hex}"),
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
