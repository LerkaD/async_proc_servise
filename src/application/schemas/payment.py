import json
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from src.application.validators.public_url import ensure_public_https_url
from src.domain.constants import DESCRIPTION_MAX_LENGTH, METADATA_MAX_BYTES
from src.domain.enums.currency import Currency


class PaymentCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, examples=["1500.50"])
    currency: Currency = Field(examples=["RUB"])
    description: str = Field(
        min_length=1,
        max_length=DESCRIPTION_MAX_LENGTH,
        examples=["Order 1001"],
    )
    metadata: dict[str, Any] = Field(default_factory=dict, examples=[{"order_id": "1001"}])
    webhook_url: HttpUrl = Field(examples=["https://example.com/webhooks/payments"])

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_not_float(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("amount must be a decimal string with at most 2 digits")
        return value

    @field_validator("amount")
    @classmethod
    def _amount_scale(cls, value: Decimal) -> Decimal:
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -2:
            raise ValueError("amount supports at most 2 decimal places")
        quantized = value.quantize(Decimal("0.01"))
        if quantized <= 0:
            raise ValueError("amount must be greater than 0")
        return quantized

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > METADATA_MAX_BYTES:
            raise ValueError("metadata exceeds 16 KiB")
        return value

    @field_validator("webhook_url")
    @classmethod
    def _webhook_target(cls, value: HttpUrl) -> HttpUrl:
        ensure_public_https_url(str(value))
        return value
