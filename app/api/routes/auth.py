from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.schemas.auth import LoginIn, RefreshIn, RegisterIn, TokenPair, UserOut
from app.services import auth as auth_svc

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserOut:
    try:
        user = await auth_svc.register(session, email=body.email, password=body.password)
    except auth_svc.EmailTaken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from None
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    try:
        access, refresh, ttl = await auth_svc.login(
            session,
            email=body.email,
            password=body.password,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    except auth_svc.InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        ) from None
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=ttl)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    try:
        access, refresh = await auth_svc.refresh(
            session,
            raw_refresh=body.refresh_token,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    except auth_svc.InvalidRefresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from None
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=900)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshIn,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await auth_svc.logout(session, raw_refresh=body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)