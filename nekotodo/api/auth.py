from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo import security, tools
from nekotodo.api.deps import get_current_account, get_current_user, get_session
from nekotodo.config import get_settings
from nekotodo.models import Account, User
from nekotodo.schemas import ChangePasswordRequest, DeleteAccountRequest, LoginRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])


async def _verify_turnstile(secret: str, token: str) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret, "response": token},
        )
        data = response.json()
    if not data.get("success"):
        raise HTTPException(status_code=400, detail="turnstile verification failed")


async def _issue_token(account: Account) -> str:
    settings = get_settings()
    return security.create_access_token(
        account.id, account.auth_version, settings.auth.jwt_secret, settings.auth.algorithm
    )


@router.post("/register")
async def register(
    payload: RegisterRequest, session: AsyncSession = Depends(get_session)
):
    settings = get_settings()
    if not settings.registration.allow_public:
        raise HTTPException(status_code=403, detail="public registration is disabled")
    if settings.registration.require_turnstile:
        if not payload.turnstile_token:
            raise HTTPException(status_code=400, detail="turnstile token required")
        await _verify_turnstile(settings.registration.turnstile_secret_key, payload.turnstile_token)
    existing = await session.execute(select(Account).where(Account.username == payload.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="username already taken")
    user = User()
    session.add(user)
    await session.flush()
    account = Account(
        username=payload.username,
        password_hash=security.hash_password(payload.password),
        user_id=user.id,
    )
    session.add(account)
    await session.commit()
    return {"token": await _issue_token(account)}


@router.get("/registration")
async def registration_config():
    """公开的注册能力说明,供登录窗口动态显示(无需鉴权)。

    只暴露 site key(它本就是给前端渲染用的公开值);secret 永不下发。
    """
    registration = get_settings().registration
    return {
        "allow_public": registration.allow_public,
        "require_turnstile": registration.require_turnstile,
        "turnstile_site_key": registration.turnstile_site_key if registration.require_turnstile else "",
    }


@router.post("/login")
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Account).where(Account.username == payload.username))
    account = result.scalar_one_or_none()
    if account is None or not security.verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return {"token": await _issue_token(account)}


@router.post("/revoke-all")
async def revoke_all(
    account: Account = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    account.auth_version += 1
    await session.commit()
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    account: Account = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    if not security.verify_password(payload.old_password, account.password_hash):
        raise HTTPException(status_code=400, detail="old password incorrect")
    account.password_hash = security.hash_password(payload.new_password)
    account.auth_version += 1
    await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Account).where(Account.user_id == user.id))
    account = result.scalar_one_or_none()
    return {
        "id": user.id,
        "username": account.username if account else None,
        "custom_prompt_template": user.custom_prompt_template,
        "timezone": user.timezone,
    }


@router.post("/delete-account")
async def delete_account(
    payload: DeleteAccountRequest,
    account: Account = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    if not security.verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=400, detail="password incorrect")
    await tools.delete_user(session, account.user_id)
    await session.commit()
    return {"ok": True}
