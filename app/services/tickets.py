import hashlib
import hmac
import secrets

from app.core.config import settings


def _secret() -> bytes:
    return settings.JWT_SECRET.get_secret_value().encode()


def generate_code() -> str:
    return secrets.token_hex(16)


def sign_code(code: str) -> str:
    return hmac.new(_secret(), code.encode(), hashlib.sha256).hexdigest()


def qr_payload(code: str) -> str:
    return f"{code}.{sign_code(code)}"


def verify_qr_payload(payload: str) -> str | None:
    """Return ticket.code if signature valid, else None."""
    try:
        code, sig = payload.rsplit(".", 1)
    except ValueError:
        return None
    if not code or not sig:
        return None
    expected = sign_code(code)
    if not hmac.compare_digest(sig, expected):
        return None
    return code