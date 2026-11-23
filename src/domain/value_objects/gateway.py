from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GatewayChargeResult:
    ok: bool
    error_code: str | None
    provider_reference: str | None
