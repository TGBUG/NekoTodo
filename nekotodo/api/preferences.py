from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo.api.deps import get_current_user, get_session
from nekotodo.models import User
from nekotodo.schemas import PreferencesUpdate

router = APIRouter(prefix="/me/preferences", tags=["preferences"])


@router.get("")
async def get_preferences(user: User = Depends(get_current_user)):
    return {"custom_prompt_template": user.custom_prompt_template, "timezone": user.timezone}


@router.patch("")
async def update_preferences(
    payload: PreferencesUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = payload.model_dump(exclude_unset=True)
    if "custom_prompt_template" in data:
        user.custom_prompt_template = data["custom_prompt_template"]
    if "timezone" in data:
        user.timezone = data["timezone"]
    await session.commit()
    return {"custom_prompt_template": user.custom_prompt_template, "timezone": user.timezone}
