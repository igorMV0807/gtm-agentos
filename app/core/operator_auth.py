import hashlib
import hmac
import secrets
import time

from app.core.config import Settings


OPERATOR_SESSION_COOKIE = "gtm_operator_session"
_SESSION_CONTEXT = "gtm-agentos:operator-session:v1"


def valid_operator_key(provided: str | None, settings: Settings) -> bool:
    if not provided:
        return False
    expected = settings.require_operator_key()
    return secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    )


def create_operator_session(settings: Settings, *, now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    signature = _session_signature(str(issued_at), settings.require_operator_key())
    return f"{issued_at}.{signature}"


def valid_operator_session(
    token: str | None,
    settings: Settings,
    *,
    now: int | None = None,
) -> bool:
    if not token or token.count(".") != 1:
        return False
    issued_text, provided_signature = token.split(".", maxsplit=1)
    try:
        issued_at = int(issued_text)
    except ValueError:
        return False
    current = int(time.time() if now is None else now)
    if issued_at > current + 30 or current - issued_at > settings.operator_session_max_age_seconds:
        return False
    expected = _session_signature(issued_text, settings.require_operator_key())
    return secrets.compare_digest(provided_signature, expected)


def _session_signature(issued_at: str, key: str) -> str:
    message = f"{_SESSION_CONTEXT}:{issued_at}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()
