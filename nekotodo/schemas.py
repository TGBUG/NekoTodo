from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8)
    turnstile_token: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class PreferencesUpdate(BaseModel):
    custom_prompt_template: str | None = None
    timezone: str | None = None


class TaskCreate(BaseModel):
    description: str = Field(min_length=1)
    deadline: str | None = None
    priority: int | None = None
    category: str | None = None


class TaskUpdate(BaseModel):
    description: str | None = None
    status: str | None = None
    details: str | None = None
    deadline: str | None = None
    category: str | None = None


class TaskMove(BaseModel):
    to_position: int


class SourceInfoUpdate(BaseModel):
    content: str | None = None
