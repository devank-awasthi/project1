from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.enums import Action


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=200)
    reg_no: str = Field(min_length=3, max_length=50)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TicketIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=20, max_length=5000)
    category: str
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    anonymous: bool = False
    upload_ids: list[str] = Field(default_factory=list, max_length=5)


class ActionIn(BaseModel):
    action: Literal[Action.ASSIGN, Action.START_REVIEW, Action.RESOLVE, Action.CONFIRM, Action.DISPUTE]
    note: str | None = Field(default=None, max_length=2000)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class PresignIn(BaseModel):
    filename: str = Field(max_length=200)
    content_type: str
    size: int


class RuleIn(BaseModel):
    category: str = Field(min_length=2, max_length=80)
    level: int = Field(ge=1, le=5)
    handler_designation: str = Field(min_length=2, max_length=100)
    base_sla_hours: int = Field(ge=1, le=24 * 60)


class OfficialIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str
    designation: str
    level: int = Field(ge=1, le=5)
