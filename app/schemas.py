from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool = True
    avatar_url: str | None = None
    last_login_at: datetime | None = None

    @classmethod
    def from_user(cls, user) -> UserInfo:
        return cls(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            avatar_url=user.avatar_url,
            last_login_at=getattr(user, "last_login_at", None),
        )


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UpdateMeRequest(BaseModel):
    full_name: str | None = None
    avatar_url: str | None = None


class CreateUserRequest(BaseModel):
    email: str
    full_name: str
    password: str | None = None
    role: str = "member"
    project_ids: list[str] = []


class UpdateUserRequest(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class CreateProjectRequest(BaseModel):
    name: str
    slug: str
    description: str = ""


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class AddMemberRequest(BaseModel):
    user_id: str
    role: str = "member"


class PlaygroundQueryRequest(BaseModel):
    project_id: str
    doc_ids: list[str] = []
    question: str
    mode: str = "answer"


class QueryRequest(BaseModel):
    doc_id: str | None = None
    doc_ids: list[str] = []
    question: str
    stream: bool = False


class CreateKeyRequest(BaseModel):
    name: str
    key_type: str = "live"
    scopes: list[str] = ["read", "write"]
    expires_at: datetime | None = None


class CreateWebhookRequest(BaseModel):
    url: str
    secret: str | None = None
    events: list[str] = ["document.completed", "document.failed"]


class BulkDocsRequest(BaseModel):
    doc_ids: list[str]
