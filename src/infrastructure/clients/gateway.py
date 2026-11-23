import asyncio
import hashlib
import random
import uuid
from decimal import Decimal

from src.domain.constants import DECLINED
from src.domain.value_objects.gateway import GatewayChargeResult
from src.infrastructure.clients.constants import (
    GATEWAY_DELAY_MAX,
    GATEWAY_DELAY_MIN,
    GATEWAY_SUCCESS_RATE,
    GATEWAY_TIMEOUT_RATE,
)


class PaymentGateway:
    def __init__(
        self,
        *,
        success_rate: float = GATEWAY_SUCCESS_RATE,
        timeout_rate: float = GATEWAY_TIMEOUT_RATE,
        delay_min: float = GATEWAY_DELAY_MIN,
        delay_max: float = GATEWAY_DELAY_MAX,
        rng: random.Random | None = None,
    ) -> None:
        self._success_rate = success_rate
        self._timeout_rate = timeout_rate
        self._delay_min = delay_min
        self._delay_max = delay_max
        self._rng = rng or random.Random()
        self._decisions: dict[uuid.UUID, GatewayChargeResult] = {}

    async def charge(
        self,
        payment_id: uuid.UUID,
        amount: Decimal,
        currency: str,
    ) -> GatewayChargeResult:
        _ = (amount, currency)
        decided = self._decisions.get(payment_id)
        if decided is not None:
            return decided

        high = max(self._delay_min, self._delay_max)
        if high > 0:
            await asyncio.sleep(self._rng.uniform(self._delay_min, high))

        decided = self._decisions.get(payment_id)
        if decided is not None:
            return decided

        if self._timeout_rate > 0 and self._rng.random() < self._timeout_rate:
            raise RuntimeError("payment gateway timed out")

        result = self._stable_decision(payment_id, self._success_rate)
        self._decisions[payment_id] = result
        return result

    @staticmethod
    def _stable_decision(payment_id: uuid.UUID, success_rate: float) -> GatewayChargeResult:
        digest = hashlib.sha256(payment_id.bytes).digest()
        roll = int.from_bytes(digest[:8], "big") / float(2**64)
        if roll >= success_rate:
            return GatewayChargeResult(ok=False, error_code=DECLINED, provider_reference=None)
        return GatewayChargeResult(
            ok=True,
            error_code=None,
            provider_reference=f"gw_{payment_id.hex[:16]}",
        )
