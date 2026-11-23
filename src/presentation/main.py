import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from src.infrastructure.config import get_settings
from src.infrastructure.constants import API_PREFIX, APP_NAME, LOG_LEVEL
from src.infrastructure.container import AppContainer
from src.presentation.api.exception_handlers import register_exception_handlers
from src.presentation.api.routes.payments import router as payments_router


def create_lifespan(
    container: AppContainer | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=LOG_LEVEL,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        owned = container or AppContainer.from_settings(get_settings())
        await owned.startup()
        app.state.container = owned
        try:
            yield
        finally:
            await owned.shutdown()

    return lifespan


def create_app(container: AppContainer | None = None) -> FastAPI:
    application = FastAPI(
        title=APP_NAME,
        version="1.0.0",
        lifespan=create_lifespan(container),
        redirect_slashes=False,
    )
    if container is not None:
        # Allows ASGI test clients that skip lifespan to resolve Depends(get_container).
        application.state.container = container
    register_exception_handlers(application)
    application.include_router(payments_router, prefix=API_PREFIX)
    return application


app = create_app()
