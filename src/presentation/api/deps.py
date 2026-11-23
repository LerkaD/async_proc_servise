import hmac
from typing import Annotated

from fastapi import Depends, Header, Request

from src.application.services.payment import PaymentService
from src.infrastructure.container import AppContainer
from src.presentation.api.exceptions import InvalidApiKey


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, AppContainer):
        raise RuntimeError("application container is not initialized")
    return container


def get_payment_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> PaymentService:
    return container.payment_service


async def require_api_key(
    container: Annotated[AppContainer, Depends(get_container)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    provided = (x_api_key or "").encode()
    expected = container.settings.api_key.encode()
    if not x_api_key or not hmac.compare_digest(provided, expected):
        raise InvalidApiKey("invalid api key")


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
ApiKeyDep = Annotated[None, Depends(require_api_key)]
ContainerDep = Annotated[AppContainer, Depends(get_container)]
