from enum import StrEnum

from src.domain.enums.currency import Currency
from src.domain.enums.outbox import OutboxStatus
from src.domain.enums.payment import PaymentStatus


def sql_in_clause(enum_cls: type[StrEnum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


__all__ = ["Currency", "OutboxStatus", "PaymentStatus", "sql_in_clause"]
