from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.db.models.outbox import OutboxEventModel
from src.infrastructure.db.models.payment import PaymentModel

SessionFactory = async_sessionmaker[AsyncSession]
Payload = Callable[..., dict[str, object]]
Headers = Callable[..., dict[str, str]]
Count = Callable[[AsyncSession, type[object]], Awaitable[int]]


async def test_replay_returns_the_stored_response(
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    payload: Payload,
    headers: Headers,
    count: Count,
) -> None:
    key = f"replay-{uuid.uuid4().hex}"
    first = await client.post("/api/v1/payments", json=payload(), headers=headers(key))
    second = await client.post("/api/v1/payments", json=payload(), headers=headers(key))

    assert first.status_code == status.HTTP_202_ACCEPTED
    assert second.status_code == status.HTTP_202_ACCEPTED
    assert second.json() == first.json()

    async with session_factory() as session, session.begin():
        assert await count(session, PaymentModel) == 1
        assert await count(session, OutboxEventModel) == 1


async def test_same_key_with_different_payload_is_conflict(
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    payload: Payload,
    headers: Headers,
    count: Count,
) -> None:
    key = f"conflict-{uuid.uuid4().hex}"
    first = await client.post("/api/v1/payments", json=payload(), headers=headers(key))
    second = await client.post(
        "/api/v1/payments",
        json=payload(amount="11.00"),
        headers=headers(key),
    )

    assert first.status_code == status.HTTP_202_ACCEPTED
    assert second.status_code == status.HTTP_409_CONFLICT

    async with session_factory() as session, session.begin():
        assert await count(session, PaymentModel) == 1


@pytest.mark.commit
async def test_parallel_creates_with_one_key_insert_a_single_payment(
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    payload: Payload,
    headers: Headers,
    count: Count,
) -> None:
    key = f"race-{uuid.uuid4().hex}"

    async def send() -> httpx.Response:
        return await client.post("/api/v1/payments", json=payload(), headers=headers(key))

    responses = await asyncio.gather(*[send() for _ in range(8)])

    assert {response.status_code for response in responses} == {status.HTTP_202_ACCEPTED}
    assert len({response.json()["payment_id"] for response in responses}) == 1

    async with session_factory() as session, session.begin():
        assert await count(session, PaymentModel) == 1
        assert await count(session, OutboxEventModel) == 1
