"""Password and token hashing with the standard library only (no new dependency).

Passwords: PBKDF2-HMAC-SHA256, 16-byte random salt, configurable iterations, encoded as
``pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>``. Verification is constant-time.
Tokens: 32 random bytes, URL-safe; only their SHA-256 is stored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGO = "pbkdf2_sha256"
MIN_PASSWORD_LENGTH = 12


class WeakPassword(ValueError):
    pass


def check_strength(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if username and username.lower() in password.lower():
        raise WeakPassword("The password must not contain the username.")
    if len(set(password)) < 6:
        raise WeakPassword("The password is too repetitive.")


def hash_password(password: str, iterations: int) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGO}${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = encoded.split("$")
        if algo != ALGO:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def needs_rehash(encoded: str, iterations: int) -> bool:
    try:
        return int(encoded.split("$")[1]) < iterations
    except (IndexError, ValueError):
        return True


def new_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_for(session_token: str) -> str:
    """The CSRF token of a session, derived from its secret cookie value. Pages on other sites cannot read the httpOnly
    cookie, so they cannot compute it; every tab of the same session gets the same value."""
    return hashlib.sha256(f"aurum-csrf:{session_token}".encode("utf-8")).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

