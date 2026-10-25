import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from app.core.config import settings

_ph = PasswordHasher()


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> tuple[bool, str | None]:
    """Return (ok, new_hash_if_rehash_needed)."""
    try:
        _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError):
        return False, None
    if _ph.check_needs_rehash(hashed):
        return True, _ph.hash(plain)
    return True, None


def hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_access_token(user_id: UUID, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.ACCESS_TTL_SEC)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALG)


def create_refresh_token(user_id: UUID) -> tuple[str, UUID, datetime]:
    """Return (raw_token, jti, expires_at). Raw token never stored — only sha256(raw)."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.REFRESH_TTL_SEC)
    jti = uuid4()
    payload = {
        "sub": str(user_id),
        "jti": str(jti),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    raw = jwt.encode(payload, settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALG)
    return raw, jti, expires_at


def decode_token(token: str, expected_type: str) -> dict:
    payload = jwt.decode(
        token,
        settings.JWT_SECRET.get_secret_value(),
        algorithms=[settings.JWT_ALG],
        options={"require": ["exp", "sub", "type"]},
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected type={expected_type}")
    return payload