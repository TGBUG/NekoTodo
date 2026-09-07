from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from nekotodo import db, storage as storage_mod
from nekotodo.agent import runner
from nekotodo.api import auth, files, preferences, runs, source_infos, tasks
from nekotodo.config import get_settings
from nekotodo.models import DecompositionRun

# 让 nekotodo.* 的运行日志(拆解/VLM/LLM 等)统一输出到控制台,
# 否则后台拆解任务的失败会被写进数据库而不在终端留下任何痕迹。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)


async def _reconcile_stale_runs() -> None:
    factory = db.get_session_factory()
    async with factory() as session:
        await session.execute(
            update(DecompositionRun)
            .where(DecompositionRun.status.in_(["pending", "running"]))
            .values(status="failed", error="interrupted by restart")
        )
        await session.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if not settings.auth.jwt_secret:
        raise RuntimeError("auth.jwt_secret is not set; refusing to start")
    db.init_db(settings.database.path)
    await db.init_schema()
    storage_mod.init_storage(settings.files.dir)
    runner.init_agent(settings, db.get_session_factory())
    await _reconcile_stale_runs()
    yield


app = FastAPI(title="NekoTodo", lifespan=lifespan)

if get_settings().server.allow_cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(files.router)
app.include_router(preferences.router)
app.include_router(tasks.router)
app.include_router(source_infos.router)
app.include_router(runs.router)


@app.get("/")
async def root():
    return {"message": "NekoTodo API"}
