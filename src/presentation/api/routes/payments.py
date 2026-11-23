import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import JSONResponse

from src.application.dto.payment import PaymentAccepted, PaymentDetails
from src.application.schemas.payment import PaymentCreateSchema
from src.application.validators.constants import IDEMPOTENCY_KEY_PATTERN
from src.presentation.api.deps import ApiKeyDep, PaymentServiceDep

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PaymentAccepted,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid API key"},
        status.HTTP_409_CONFLICT: {
            "description": "Idempotency key reused with a different payload",
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Validation error"},
    },
)
async def create_payment(
    payload: PaymentCreateSchema,
    _: ApiKeyDep,
    service: PaymentServiceDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JSONResponse:
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid Idempotency-Key",
        )
    body, status_code = await service.create_payment(payload, idempotency_key)
    return JSONResponse(status_code=status_code, content=body)


@router.get(
    "/{payment_id}",
    response_model=PaymentDetails,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid API key"},
        status.HTTP_404_NOT_FOUND: {"description": "Payment not found"},
    },
)
async def get_payment(
    payment_id: uuid.UUID,
    _: ApiKeyDep,
    service: PaymentServiceDep,
) -> PaymentDetails:
    return await service.get_payment(payment_id)
