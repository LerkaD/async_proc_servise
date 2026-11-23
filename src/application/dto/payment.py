from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.domain.enums.currency import Currency
from src.domain.enums.payment import PaymentStatus


@dataclass(frozen=True, slots=True)
class PaymentAccepted:
    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentDetails:
    payment_id: uuid.UUID
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    failure_reason: str | None
    provider_reference: str | None
    created_at: datetime
    processed_at: datetime | None
    webhook_delivered_at: datetime | None
