"""FastAPI entry point: ``uvicorn app.main:app --reload`` from backend/."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.errors import catch_unhandled, install_error_handlers
from app.providers import DataProvider, make_provider
from app.routers import health, science

_LOG_HANDLER_NAME = "app-irsa-info"


def configure_logging(settings: Settings) -> None:
    """In irsa mode, show ``app.*`` INFO lines (build timings, cache hits) next to
    uvicorn's own output. uvicorn configures only its own loggers, so without a
    handler these lines would be dropped. Safe to call more than once."""
    if settings.data_mode != "irsa":
        return
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    if not any(h.get_name() == _LOG_HANDLER_NAME for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.set_name(_LOG_HANDLER_NAME)
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
        logger.addHandler(handler)


def create_app(
    settings: Settings | None = None, provider: DataProvider | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings)
    app = FastAPI(
        title="Sarawak Astronauts science API",
        version="0.1.0",
        description="SPHEREx time-lapse API (contract: docs/API.md v0.1).",
    )
    app.state.settings = settings
    app.state.provider = provider or make_provider(settings)

    install_error_handlers(app)
    # Middleware added last runs first: CORS wraps the 500 catcher, so error
    # responses still carry CORS headers.
    app.middleware("http")(catch_unhandled)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=[health.DATA_MODE_HEADER],
    )

    app.include_router(health.router)
    app.include_router(science.router)
    return app


app = create_app()
