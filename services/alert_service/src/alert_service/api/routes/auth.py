from __future__ import annotations

import jwt
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    nickname: str = Field(min_length=1, max_length=64)

    @field_validator("nickname")
    @classmethod
    def nickname_must_not_be_blank(cls, value: str) -> str:
        nickname = value.strip()
        if not nickname:
            raise ValueError("nickname must not be empty")
        return nickname


class LoginOut(BaseModel):
    token: str
    user_id: str


@router.post("/login", response_model=LoginOut)
async def login(payload: LoginIn, request: Request) -> LoginOut:
    user_repo = request.app.state.user_repo
    config = request.app.state.config
    user = await user_repo.get_or_create_by_nickname(payload.nickname.strip())
    token = jwt.encode(
        {config.jwt_user_id_claim: str(user.user_id)},
        config.jwt_secret,
        algorithm=config.jwt_algorithm,
    )
    return LoginOut(token=token, user_id=str(user.user_id))
