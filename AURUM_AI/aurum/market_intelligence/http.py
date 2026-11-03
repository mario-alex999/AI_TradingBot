"""HTTP client for Module 04 providers: timeouts, exponential backoff, ``Retry-After`` on 429, no retry on other 4xx.

API keys travel as query parameters for some providers, so URLs are NEVER logged with their query string and every
error message is scrubbed of configured secrets. ``sleep`` and the ``httpx.Client`` are injectable for tests.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)
USER_AGENT = "AURUM-AI/0.4 (market-intelligence research module; read-only)"


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, kind: str = "error"):
        super().__init__(message)
        self.status = status
        self.kind = kind            # network | timeout | rate_limited | auth | http | parse


class HttpClient:
    def __init__(self, timeout: float = 20.0, max_retries: int = 3, backoff: float = 1.5,
                 client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 secrets: list[str] | None = None):
        self.timeout, self.max_retries, self.backoff = timeout, max_retries, backoff
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self.sleep = sleep
        self.secrets = [s for s in (secrets or []) if s]
        self.requests = 0

    def scrub(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
        safe_url = url.split("?", 1)[0]
        attempt = 0
        while True:
            attempt += 1
            self.requests += 1
            try:
                resp = self.client.get(url, params=params, headers={"User-Agent": USER_AGENT, **(headers or {})},
                                       timeout=self.timeout)
            except httpx.TimeoutException as exc:
                if attempt > self.max_retries:
                    raise ProviderError(f"Timeout contacting {safe_url}", kind="timeout") from exc
                self._wait(attempt, safe_url, "timeout")
                continue
            except httpx.TransportError as exc:
                if attempt > self.max_retries:
                    raise ProviderError(self.scrub(f"{type(exc).__name__} contacting {safe_url}"), kind="network") from exc
                self._wait(attempt, safe_url, type(exc).__name__)
                continue
            if resp.status_code == 429:
                if attempt > self.max_retries:
                    raise ProviderError(f"Rate limited by {safe_url} (HTTP 429)", 429, "rate_limited")
                self._wait(attempt, safe_url, "rate_limited", _retry_after(resp))
                continue
            if resp.status_code >= 500:
                if attempt > self.max_retries:
                    raise ProviderError(f"HTTP {resp.status_code} from {safe_url}", resp.status_code, "http")
                self._wait(attempt, safe_url, f"http_{resp.status_code}")
                continue
            if resp.status_code in (401, 403):
                raise ProviderError(f"HTTP {resp.status_code} from {safe_url}: access denied (API key missing, invalid "
                                    f"or the plan does not include this endpoint).", resp.status_code, "auth")
            if resp.status_code >= 400:
                raise ProviderError(f"HTTP {resp.status_code} from {safe_url}", resp.status_code, "http")
            return resp

    def json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.get(url, params)
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"Response from {url.split('?', 1)[0]} is not valid JSON", resp.status_code,
                                "parse") from exc

    def _wait(self, attempt: int, url: str, why: str, retry_after: float | None = None) -> None:
        delay = retry_after if retry_after is not None else self.backoff * 2 ** (attempt - 1)
        log.warning("Provider request failed - retrying", extra={"url": url, "reason": why, "attempt": attempt,
                                                                 "delay_s": delay})
        self.sleep(delay)


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("Retry-After")
    try:
        return max(0.0, float(v)) if v is not None else None
    except ValueError:
        return None
