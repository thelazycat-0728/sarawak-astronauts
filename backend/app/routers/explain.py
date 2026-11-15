"""POST /explain (docs/API.md): plain-language answers from the AI explainer."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.errors import InvalidParameter, RateLimited, Unavailable
from app.models import ErrorResponse, ExplainRequest, ExplainResponse

router = APIRouter(tags=["ai"])


@router.post(
    "/explain",
    response_model=ExplainResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid question"},
        429: {"model": ErrorResponse, "description": "Too many questions, or LLM quota used up"},
        502: {"model": ErrorResponse, "description": "The LLM failed"},
        503: {"model": ErrorResponse, "description": "No LLM configured"},
    },
)
async def explain(body: ExplainRequest, request: Request) -> ExplainResponse:
    explainer = request.app.state.explainer
    if explainer is None:
        raise Unavailable("The AI assistant is not configured on this server.")
    question = body.question.strip()
    if not question:
        raise InvalidParameter("question: must not be blank")
    # Behind Render's proxy, uvicorn --forwarded-allow-ips="*" puts the real
    # visitor address in request.client.
    ip = request.client.host if request.client else "unknown"
    if not request.app.state.explain_limiter.allow(ip):
        raise RateLimited("Too many questions. Wait a minute and try again.")
    result = await explainer.explain(question, body.context)
    return ExplainResponse(
        answer=result.answer,
        ai_generated=True,
        model=result.model or explainer.model,
        glossary_terms=result.glossary_terms,
    )
