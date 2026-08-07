from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo import security
from nekotodo.config import get_settings
from nekotodo.db import get_session_factory
from nekotodo.models import Account, User

_bearer = HTTPBearer(auto_error=False)


async def get_session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def get_current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Account:
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing token")
    settings = get_settings()
    payload = security.decode_access_token(
        credentials.credentials, settings.auth.jwt_secret, settings.auth.algorithm
    )
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid token")
    try:
        account_id = int(payload.get("sub", -1))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token")
    account = await session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=401, detail="unknown account")
    if account.auth_version != payload.get("av"):
        raise HTTPException(status_code=401, detail="token revoked")
    return account


async def get_current_user(
    account: Account = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await session.get(User, account.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user missing")
    return user
