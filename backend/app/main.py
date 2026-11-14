"""FastAPI entry point: ``uvicorn app.main:app --reload`` from backend/."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.errors import catch_unhandled, install_error_handlers
from app.providers import DataProvider, make_provider
from app.routers import health, science


def create_app(
    settings: Settings | None = None, provider: DataProvider | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
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
    )

    app.include_router(health.router)
    app.include_router(science.router)
    return app


app = create_app()
