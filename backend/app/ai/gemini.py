"""Google Gemini (free tier) behind the Explainer interface.

``google-genai`` is imported lazily, so the API starts (and the tests run)
without loading it when no key is configured.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace

from app.ai.base import ExplainResult
from app.ai.prompt import RESPONSE_SCHEMA, load_glossary, system_prompt, user_prompt
from app.config import Settings
from app.errors import RateLimited, UpstreamError
from app.models import ExplainContext

log = logging.getLogger("app.ai")


class GeminiExplainer:
    name = "gemini"

    def __init__(self, settings: Settings):
        from google import genai
        from google.genai import types

        self._types = types
        self._errors = genai.errors
        self.model = settings.gemini_model
        self.fallback_model = settings.gemini_fallback_model
        self._timeout_s = settings.explain_timeout_s
        self._max_output_tokens = settings.explain_max_output_tokens
        self._client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=int(settings.explain_timeout_s * 1000)),
        )

    async def explain(self, question: str, context: ExplainContext | None) -> ExplainResult:
        """Asks the main model; if it is busy, out of quota or missing, asks the
        fallback model once. The result names the model that answered."""
        config = self._types.GenerateContentConfig(
            system_instruction=system_prompt(),
            max_output_tokens=self._max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA,
        )
        contents = user_prompt(question, context)
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)
        for i, model in enumerate(models):
            try:
                response = await self._generate(model, contents, config)
            except _TryNext as exc:
                if i + 1 < len(models):
                    log.warning("Gemini %s failed (%s); trying %s", model, exc, models[i + 1])
                    continue
                raise exc.error from exc
            usage = response.usage_metadata
            if usage is not None:
                log.info(
                    "explain model=%s prompt_tokens=%s output_tokens=%s thinking_tokens=%s",
                    model, usage.prompt_token_count, usage.candidates_token_count,
                    usage.thoughts_token_count,
                )
            return replace(parse_reply(response.text), model=model)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _generate(self, model: str, contents: str, config):
        """One Gemini call. Raises _TryNext when another model may succeed."""
        errors = self._errors
        try:
            return await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                ),
                timeout=self._timeout_s + 2,
            )
        except TimeoutError as exc:
            raise _TryNext("timeout", UpstreamError(
                "The AI assistant took too long to answer."
            )) from exc
        except errors.ClientError as exc:
            if exc.code == 429:
                # Free-tier quota (per minute or per day, per model) used up.
                raise _TryNext(f"429 {exc.message}", RateLimited(
                    "The AI assistant has reached its usage limit. Try again later."
                )) from exc
            if exc.code == 404:
                log.error("Gemini model %s not found: %s", model, exc.message)
                raise _TryNext("404 unknown model", UpstreamError(
                    "The AI assistant is misconfigured."
                )) from exc
            # 400/401/403: bad key or rejected request. Another model won't help.
            log.error("Gemini rejected the request (%s): %s", exc.code, exc.message)
            raise UpstreamError("The AI assistant is misconfigured.") from exc
        except errors.APIError as exc:
            # 5xx, usually "model is experiencing high demand" (503).
            raise _TryNext(f"{exc.code} {exc.message}", UpstreamError(
                "The AI assistant is busy right now. Try again shortly."
            )) from exc


class _TryNext(Exception):
    """A failure another model may not have; ``error`` is what the client gets if none is left."""

    def __init__(self, reason: str, error: UpstreamError | RateLimited):
        super().__init__(reason)
        self.error = error


def parse_reply(text: str | None) -> ExplainResult:
    """Validates Gemini's JSON reply; unknown glossary ids are dropped."""
    try:
        data = json.loads(text or "")
        answer = str(data["answer"]).strip()
        terms = data.get("glossary_terms") or []
    except (ValueError, KeyError, TypeError) as exc:
        # Blocked by a safety filter, cut off at max_output_tokens, or malformed.
        log.warning("Unusable Gemini reply: %.200r", text)
        raise UpstreamError("The AI assistant could not answer that question.") from exc
    if not answer:
        raise UpstreamError("The AI assistant could not answer that question.")
    known = load_glossary()
    seen: list[str] = []
    for t in terms if isinstance(terms, list) else []:
        if isinstance(t, str) and t in known and t not in seen:
            seen.append(t)
    return ExplainResult(answer=answer, glossary_terms=seen)
