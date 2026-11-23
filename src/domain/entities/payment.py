from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.domain.enums.payment import PaymentStatus


@dataclass(slots=True)
class Payment:
    id: uuid.UUID
    amount: Decimal
    currency: str
    description: str
    payment_metadata: dict[str, Any]
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    failure_reason: str | None
    provider_reference: str | None
    processing_token: str | None
    processing_started_at: datetime | None
    webhook_delivered_at: datetime | None
    webhook_attempts: int
    webhook_next_retry_at: datetime | None
    processed_at: datetime | None
    created_at: datetime
    updated_at: datetime = field(compare=False)
