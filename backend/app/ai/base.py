from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.models import ExplainContext


@dataclass(frozen=True)
class ExplainResult:
    answer: str
    glossary_terms: list[str] = field(default_factory=list)
    # The model that answered, when it differs from Explainer.model (a fallback).
    model: str | None = None


class Explainer(Protocol):
    """An LLM that answers a question about the current view (POST /explain)."""

    name: str
    model: str

    async def explain(self, question: str, context: ExplainContext | None) -> ExplainResult: ...
