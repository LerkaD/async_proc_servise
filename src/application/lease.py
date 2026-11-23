from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from src.application.enums.processing import Lease

logger = logging.getLogger(__name__)


@asynccontextmanager
async def keep_lease(
    payment_id: uuid.UUID,
    refresh: Callable[[], Awaitable[Lease]],
    *,
    lease_seconds: float,
) -> AsyncIterator[None]:
    stop = asyncio.Event()
    task = asyncio.create_task(_beat(payment_id, refresh, lease_seconds, stop))
    try:
        yield
    finally:
        stop.set()
        await task


async def _beat(
    payment_id: uuid.UUID,
    refresh: Callable[[], Awaitable[Lease]],
    lease_seconds: float,
    stop: asyncio.Event,
) -> None:
    interval = max(lease_seconds / 3, 0.05)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        if stop.is_set():
            return
        try:
            state = await refresh()
        except Exception:
            logger.exception("lease refresh failed for payment %s", payment_id)
            continue
        if state is not Lease.HELD:
            return
