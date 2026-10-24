from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: str
    username: str


class SessionResponse(BaseModel):
    authenticated: bool
    csrf_token: str | None = None
    user: UserResponse | None = None


class AssetUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = None
    status: str | None = Field(default=None, pattern="^(draft|published|archived)$")
    visibility: str | None = Field(default=None, pattern="^(private|public)$")


class AssetResponse(BaseModel):
    id: str
    title: str
    asset_type: str
    description: str
    category: str
    tags: list[str]
    status: str
    visibility: str
    cover_url: str | None = None
    original_name: str | None = None
    mime_type: str | None = None
    size_bytes: int = 0
    created_at: datetime
    updated_at: datetime
