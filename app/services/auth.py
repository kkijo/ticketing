from datetime import UTC, datetime

import jwt
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh,
    verify_password,
)


class EmailTaken(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class InvalidRefresh(Exception):
    pass


async def register(
    session: AsyncSession, *, email: str, password: str
) -> User:
    user = User(email=email, password_hash=hash_password(password), role="customer")
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise EmailTaken from e
    await session.refresh(user)
    return user


async def login(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    user_agent: str | None,
    ip: str | None,
) -> tuple[str, str, int]:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # constant-ish time: verify against a dummy hash if user not found
    if user is None:
        verify_password("x", _DUMMY_HASH)
        raise InvalidCredentials

    ok, rehash = verify_password(password, user.password_hash)
    if not ok:
        raise InvalidCredentials
    if rehash:
        user.password_hash = rehash
        await session.commit()

    access = create_access_token(user.id, user.role)
    raw_refresh, _jti, expires_at = create_refresh_token(user.id)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh(raw_refresh),
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
    )
    await session.commit()
    return access, raw_refresh, 900  # matches ACCESS_TTL_SEC default


async def refresh(
    session: AsyncSession,
    *,
    raw_refresh: str,
    user_agent: str | None,
    ip: str | None,
) -> tuple[str, str]:
    try:
        decode_token(raw_refresh, expected_type="refresh")
    except jwt.PyJWTError as e:
        raise InvalidRefresh from e

    token_hash = hash_refresh(raw_refresh)
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()

    if row is None or row.revoked_at is not None:
        raise InvalidRefresh
    if row.expires_at <= datetime.now(UTC):
        raise InvalidRefresh

    user = (
        await session.execute(select(User).where(User.id == row.user_id))
    ).scalar_one()

    row.revoked_at = datetime.now(UTC)
    new_raw, _jti, new_exp = create_refresh_token(user.id)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh(new_raw),
            expires_at=new_exp,
            user_agent=user_agent,
            ip=ip,
        )
    )
    await session.commit()

    access = create_access_token(user.id, user.role)
    return access, new_raw


async def logout(session: AsyncSession, *, raw_refresh: str) -> None:
    token_hash = hash_refresh(raw_refresh)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()


_DUMMY_HASH = hash_password("dummy-password-for-timing")