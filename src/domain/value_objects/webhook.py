from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class WebhookNotification:
    payment_id: uuid.UUID
    status: str
    amount: Decimal
    currency: str
    processed_at: datetime | None
    failure_reason: str | None
