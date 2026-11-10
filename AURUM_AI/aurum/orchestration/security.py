"""Webhook signing and verification, and credential metadata.

Signature scheme (both directions)::

    X-Aurum-Timestamp: <unix seconds>
    X-Aurum-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>

- The timestamp must be within ``max_skew`` seconds of the receiver's clock (replay window).
- Inside the window, replays are refused by the receiver's unique message id (callback id / event id).
- Several secrets may be accepted at once while a secret is rotated (current + previous).

Secrets themselves are never stored in the database or returned by the API: ``secret_status`` reports only whether a
secret is configured and a short SHA-256 fingerprint, so operators can tell which secret is in use after a rotation.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

SIGNATURE_HEADER = "X-Aurum-Signature"
TIMESTAMP_HEADER = "X-Aurum-Timestamp"
WEBHOOK_TOKEN_HEADER = "X-AURUM-Webhook-Token"


class SignatureError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"v1={mac}"


def signed_headers(secret: str | None, body: bytes, now: datetime) -> dict[str, str]:
    if not secret:
        return {}
    ts = int(now.timestamp())
    return {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sign(secret, ts, body)}


def verify(body: bytes, timestamp: str | None, signature: str | None, secrets: Iterable[str], now: datetime,
           max_skew: int = 300) -> None:
    """Raises SignatureError unless the signature matches one of ``secrets`` and the timestamp is fresh."""
    keys = [s for s in secrets if s]
    if not keys:
        raise SignatureError("SIGNATURE_NOT_CONFIGURED", "No webhook secret is configured on the server.")
    if not signature or not timestamp:
        raise SignatureError("SIGNATURE_MISSING", "The request is not signed.")
    try:
        ts = int(timestamp)
    except ValueError:
        raise SignatureError("TIMESTAMP_INVALID", "The signature timestamp is not a number.") from None
    if abs(int(now.timestamp()) - ts) > max_skew:
        raise SignatureError("TIMESTAMP_EXPIRED", "The signature timestamp is outside the allowed window.")
    sent = signature.strip()
    if not any(hmac.compare_digest(sent, sign(k, ts, body)) for k in keys):
        raise SignatureError("SIGNATURE_INVALID", "The signature does not match.")


def fingerprint(secret: str | None) -> str | None:
    return hashlib.sha256(secret.encode()).hexdigest()[:12] if secret else None


@dataclass(frozen=True)
class CredentialSpec:
    name: str
    env: str
    attr: str
    purpose: str


CREDENTIALS = (
    CredentialSpec("n8n_api_key", "N8N_API_KEY", "n8n_api_key",
                   "n8n public API (workflow metadata, activation, execution status, stop). Created in n8n: Settings > n8n API."),
    CredentialSpec("n8n_webhook_shared_secret", "N8N_WEBHOOK_SHARED_SECRET", "n8n_webhook_shared_secret",
                   "HMAC key: AURUM signs requests to n8n and verifies signed callbacks from n8n."),
    CredentialSpec("n8n_webhook_shared_secret_previous", "N8N_WEBHOOK_SHARED_SECRET_PREVIOUS",
                   "n8n_webhook_shared_secret_previous", "Previous HMAC key, still accepted on callbacks during a rotation."),
    CredentialSpec("n8n_webhook_auth_token", "N8N_WEBHOOK_AUTH_TOKEN", "n8n_webhook_auth_token",
                   "Header value n8n webhook nodes check (Header Auth credential in n8n) before running a workflow."),
    CredentialSpec("orchestration_service_token", "ORCHESTRATION_SERVICE_TOKEN", "orchestration_service_token",
                   "AURUM service token the module gateway uses for protected module endpoints (for example Module 07 health)."),
)


def secret_value(settings: Any, attr: str) -> str | None:
    v = getattr(settings, attr, None)
    if v is None:
        return None
    raw = v.get_secret_value() if hasattr(v, "get_secret_value") else str(v)
    return raw or None


def secret_status(settings: Any) -> list[dict[str, Any]]:
    out = []
    for c in CREDENTIALS:
        v = secret_value(settings, c.attr)
        out.append({"name": c.name, "source": f"env:{c.env}", "purpose": c.purpose, "configured": v is not None,
                    "fingerprint": fingerprint(v)})
    return out
