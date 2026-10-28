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


class ScreenplaySceneUpdate(BaseModel):
    heading: str | None = Field(default=None, max_length=300)
    content: str | None = None
    notes: str | None = None


class ScreenplaySceneCreate(BaseModel):
    heading: str = Field(default="", max_length=300)
    content: str = ""
    notes: str = ""


class ScreenplaySceneResponse(BaseModel):
    id: str
    position: int
    heading: str
    content: str
    notes: str
    assets: list[AssetResponse]
    created_at: datetime
    updated_at: datetime


class ScreenplayProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    logline: str = Field(default="", max_length=2000)
    status: str = Field(default="draft", pattern="^(draft|in_progress|completed)$")


class ScreenplayProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    logline: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(draft|in_progress|completed)$")


class ScreenplayProjectSummary(BaseModel):
    id: str
    title: str
    logline: str
    status: str
    scene_count: int
    created_at: datetime
    updated_at: datetime


class ScreenplayProjectResponse(ScreenplayProjectSummary):
    scenes: list[ScreenplaySceneResponse]


class ScreenplaySceneOrder(BaseModel):
    scene_ids: list[str] = Field(min_length=1, max_length=500)
