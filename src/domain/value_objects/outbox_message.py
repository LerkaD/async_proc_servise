from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
