from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import delete, select

from app.api.deps import CurrentUser, DatabaseSession, get_user_roles
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    token_fingerprint,
    verify_password,
)
from app.db.models.identity import RefreshToken, User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.common import StatusResponse

router = APIRouter(prefix="/auth", tags=["authentication"])


async def issue_tokens(
    session: DatabaseSession,
    user: User,
    request: Request,
) -> TokenResponse:
    roles = await get_user_roles(session, user.id)
    access = create_access_token(user.id, roles)
    refresh = create_refresh_token(user.id)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_fingerprint(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
            user_agent=request.headers.get("user-agent", "")[:500],
            ip_address=request.client.host if request.client else None,
        )
    )
    await session.commit()
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: DatabaseSession,
) -> TokenResponse:
    result = await session.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账户已停用")
    user.last_login_at = datetime.now(UTC)
    return await issue_tokens(session, user, request)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    payload: RefreshRequest,
    request: Request,
    session: DatabaseSession,
) -> TokenResponse:
    try:
        claims = decode_token(payload.refresh_token, "refresh")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="刷新凭证无效或已过期") from exc

    fingerprint = token_fingerprint(payload.refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == fingerprint)
    )
    stored = result.scalar_one_or_none()
    expires_at = stored.expires_at if stored is not None else None
    # MySQL DATETIME values are returned without tzinfo even though the
    # application consistently writes UTC values. Normalize before comparing.
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        stored is None
        or stored.revoked_at is not None
        or expires_at is None
        or expires_at < datetime.now(UTC)
    ):
        raise HTTPException(status_code=401, detail="刷新凭证已失效")
    user = await session.get(User, claims["sub"])
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="用户不可用")

    stored.revoked_at = datetime.now(UTC)
    new_tokens = await issue_tokens(session, user, request)
    stored.replaced_by_hash = token_fingerprint(new_tokens.refresh_token)
    await session.commit()
    return new_tokens


@router.post("/logout", response_model=StatusResponse)
async def logout(payload: LogoutRequest, session: DatabaseSession) -> StatusResponse:
    fingerprint = token_fingerprint(payload.refresh_token)
    await session.execute(delete(RefreshToken).where(RefreshToken.token_hash == fingerprint))
    await session.commit()
    return StatusResponse(status="ok", message="已退出登录")


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser, session: DatabaseSession) -> UserResponse:
    roles = await get_user_roles(session, user.id)
    data = {
        "id": user.id,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "email": user.email,
        "display_name": user.display_name,
        "organization_id": user.organization_id,
        "status": user.status,
        "is_superuser": user.is_superuser,
        "roles": roles,
    }
    return UserResponse.model_validate(data)
