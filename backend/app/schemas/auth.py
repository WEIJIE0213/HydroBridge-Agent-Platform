from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import TimestampedModel


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(TimestampedModel):
    email: EmailStr
    display_name: str
    organization_id: str | None
    status: str
    is_superuser: bool
    roles: list[str] = Field(default_factory=list)
