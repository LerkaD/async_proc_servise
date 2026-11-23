from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest

from src.domain.constants import DECLINED
from src.infrastructure.clients.gateway import PaymentGateway

pytestmark = pytest.mark.nodb


class ScriptedRandom(random.Random):
    def __init__(self, values: list[float]) -> None:
        super().__init__()
        self._values = list(values)

    def random(self) -> float:
        if not self._values:
            raise AssertionError("rng exhausted")
        return self._values.pop(0)


async def test_decision_is_stable_for_payment_id(
    make_gateway: Callable[..., PaymentGateway],
) -> None:
    payment_id = uuid.uuid4()
    gateway = make_gateway(gateway_success_rate=1)

    first = await gateway.charge(payment_id, Decimal("10.50"), "RUB")
    second = await gateway.charge(payment_id, Decimal("10.50"), "RUB")

    assert first.ok is True
    assert second == first
    assert first.provider_reference == f"gw_{payment_id.hex[:16]}"

    declined = await make_gateway(gateway_success_rate=0).charge(
        payment_id,
        Decimal("10.50"),
        "RUB",
    )
    assert declined.ok is False
    assert declined.error_code == DECLINED


async def test_timeout_is_not_cached() -> None:
    gateway = PaymentGateway(
        success_rate=1.0,
        timeout_rate=0.5,
        delay_min=0.0,
        delay_max=0.0,
        rng=ScriptedRandom([0.0, 0.9]),
    )
    payment_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="timed out"):
        await gateway.charge(payment_id, Decimal("10.50"), "RUB")

    decided = await gateway.charge(payment_id, Decimal("10.50"), "RUB")
    repeated = await gateway.charge(payment_id, Decimal("10.50"), "RUB")

    assert decided.ok is True
    assert repeated == decided
