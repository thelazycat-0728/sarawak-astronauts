"""AI explainer for POST /explain (Track C): Google Gemini, free tier.

``make_explainer`` returns None when no ``GEMINI_API_KEY`` is set; the route then
answers 503 and the frontend falls back to its offline answers.
"""

from __future__ import annotations

from app.ai.base import Explainer, ExplainResult
from app.config import Settings

__all__ = ["ExplainResult", "Explainer", "make_explainer"]


def make_explainer(settings: Settings) -> Explainer | None:
    if not settings.gemini_api_key:
        return None
    from app.ai.gemini import GeminiExplainer

    return GeminiExplainer(settings)
