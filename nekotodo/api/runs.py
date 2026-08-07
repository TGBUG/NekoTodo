from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo.api.deps import get_current_user, get_session
from nekotodo.models import DecompositionRun, User

router = APIRouter(prefix="/runs", tags=["runs"])


def _run_to_dict(run: DecompositionRun) -> dict:
    return {
        "id": run.id,
        "source_info_id": run.source_info_id,
        "status": run.status,
        "created_task_ids": run.created_task_ids or [],
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }


@router.get("")
async def list_runs(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(DecompositionRun)
        .where(DecompositionRun.user_id == user.id)
        .order_by(DecompositionRun.created_at.desc())
    )
    return [_run_to_dict(run) for run in result.scalars().all()]


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(DecompositionRun).where(
            DecompositionRun.id == run_id, DecompositionRun.user_id == user.id
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_to_dict(run)
