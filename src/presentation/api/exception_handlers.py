from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from src.application.exceptions import (
    IdempotencyInProgress,
    IdempotencyPayloadMismatch,
    PaymentNotFound,
)
from src.presentation.api.exceptions import InvalidApiKey


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidApiKey)
    async def _invalid_api_key(_request: Request, exc: InvalidApiKey) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": exc.message},
        )

    @app.exception_handler(PaymentNotFound)
    async def _payment_not_found(_request: Request, exc: PaymentNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": exc.message},
        )

    @app.exception_handler(IdempotencyPayloadMismatch)
    async def _payload_mismatch(
        _request: Request,
        exc: IdempotencyPayloadMismatch,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": exc.message},
        )

    @app.exception_handler(IdempotencyInProgress)
    async def _in_progress(_request: Request, exc: IdempotencyInProgress) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": exc.message},
        )
