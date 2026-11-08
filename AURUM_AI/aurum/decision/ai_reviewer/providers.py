"""AI providers other than Claude, behind one small interface. Claude keeps its own path in ``client.py`` because it alone has a price
table and so a hard budget. The rule-based explanation in ``recommendation.explain`` is the deterministic fallback: it always exists
and needs no provider. It explains; it never counts as a review, so it can never promote a candidate.

``openai`` is an optional install. Every failure becomes a ProviderError with a reason the audit can store; its text is never kept.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from aurum.config import Settings


@dataclass
class ProviderReply:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ReviewProvider(Protocol):
    def complete(self, system: str, prompt: str, schema: dict[str, Any]) -> ProviderReply: ...


REASONS = {"APITimeoutError": "timeout", "APIConnectionError": "connection_error", "AuthenticationError": "auth_error", "RateLimitError": "rate_limited"}


class OpenAiCompatible:
    """OpenAI, or any local server with the same API when AI_PROVIDER_BASE_URL is set."""

    def __init__(self, settings: Settings, retries: int = 0, client: Any = None):
        self.settings, self.retries, self._client = settings, retries, client

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import openai
        except ImportError as exc:
            raise ProviderError("sdk_missing") from exc
        s = self.settings
        key = s.openai_api_key.get_secret_value() if s.openai_api_key else "local"
        return openai.OpenAI(api_key=key, base_url=s.ai_provider_base_url, timeout=s.ai_review_timeout_seconds, max_retries=self.retries)

    def complete(self, system: str, prompt: str, schema: dict[str, Any]) -> ProviderReply:
        sdk = self._sdk()
        try:
            r = sdk.chat.completions.create(model=self.settings.ai_provider_model,
                                            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                                            response_format={"type": "json_schema", "json_schema": {"name": "review", "strict": True, "schema": schema}})
        except Exception as exc:  # noqa: BLE001 - mapped by name: the SDK is optional, and its text may hold secrets
            raise ProviderError(REASONS.get(type(exc).__name__, "provider_error")) from None
        choice = r.choices[0]
        if choice.finish_reason == "length":
            raise ProviderError("truncated")
        if not choice.message.content:
            raise ProviderError("refused")
        usage = getattr(r, "usage", None)
        return ProviderReply(choice.message.content, getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))


def providers(settings: Settings) -> list[dict[str, Any]]:
    s = settings
    return [{"id": "anthropic", "selected": s.ai_provider == "anthropic", "model": s.ai_review_model, "configured": bool(s.anthropic_api_key),
             "budget_enforced": True},
            {"id": "openai", "selected": s.ai_provider == "openai", "model": s.ai_provider_model, "configured": bool(s.openai_api_key and s.ai_provider_model),
             "budget_enforced": False},
            {"id": "local", "selected": s.ai_provider == "local", "model": s.ai_provider_model, "configured": bool(s.ai_provider_base_url and s.ai_provider_model),
             "budget_enforced": False},
            {"id": "deterministic", "selected": False, "model": None, "configured": True, "budget_enforced": False,
             "note": "Rule-based explanation. Always on; it is not a review."}]
