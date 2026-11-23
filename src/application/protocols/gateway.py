from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Protocol

from src.domain.value_objects.gateway import GatewayChargeResult


class Gateway(Protocol):
    async def charge(
        self,
        payment_id: uuid.UUID,
        amount: Decimal,
        currency: str,
    ) -> GatewayChargeResult: ...
