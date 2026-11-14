from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.models import HealthResponse

router = APIRouter(tags=["health"])

# The body stays exactly {"status": "ok"} (API.md); the data mode is a header.
DATA_MODE_HEADER = "X-Data-Mode"


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={200: {"headers": {DATA_MODE_HEADER: {
        "description": "Data provider in use: mock or irsa", "schema": {"type": "string"},
    }}}},
)
def health(request: Request, response: Response) -> HealthResponse:
    response.headers[DATA_MODE_HEADER] = request.app.state.provider.name
    return HealthResponse(status="ok")
