# -*- coding: utf-8 -*-
"""MathMate API —— 大屏宠物选题闭环。

启动:
    cd apps/api
    python -m app.seed            # 首次:生成演示数据
    uvicorn app.main:app --reload --port 8000

大屏     http://localhost:8000/screen/
教师控制台 http://localhost:8000/teacher/
接口文档  http://localhost:8000/docs
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .auth import device_from_token, read_token
from .db import SessionLocal, engine
from .logging_conf import setup as setup_logging
from .models import Base, Teacher, VoteSession
from .realtime import SCREEN, TEACHER, hub
from .routers import auth as auth_router
from .routers import authoring, insight, students, vote, teachers

setup_logging()

APPS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHARED_DIR = os.path.join(APPS_DIR, "shared")
SCREEN_DIR = os.path.join(APPS_DIR, "screen")
TEACHER_DIR = os.path.join(APPS_DIR, "teacher")
PREVIEW_DIR = os.path.join(os.path.dirname(APPS_DIR), "preview")

def _migrate_schema() -> None:
    """轻量迁移:给已有 SQLite 库补上后加的 student_no 列。

    正式 PG 环境走 Alembic;这里只为零配置的 SQLite 演示库兜底,
    避免模型加了列之后老库报 no such column。
    """
    if engine.url.drivername != "sqlite":
        return
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(students)")}
        if "student_no" not in cols:
            conn.exec_driver_sql("ALTER TABLE students ADD COLUMN student_no VARCHAR(32)")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    _migrate_schema()
    yield


app = FastAPI(title="MathMate API", version="0.1.0", lifespan=lifespan)

# 允许的前端来源。默认只放行本机 —— 大屏与教师端都由本服务同源托管,
# 跨域只在「前端单独部署」时才需要,那时用 MATHMATE_CORS_ORIGINS 显式列出。
# 通配符 * 与 allow_credentials 不能共存,而且鉴权上线后全开就是把令牌
# 暴露给任意站点,所以这里不再用 *。
_origins = [o.strip() for o in os.environ.get("MATHMATE_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["http://localhost:55101", "http://127.0.0.1:55101"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(teachers.router)
app.include_router(vote.router)
app.include_router(authoring.router)
app.include_router(insight.router)
app.include_router(students.router)


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok"}


def _ws_identity(session_id: str, token: str | None, access: str | None) -> str:
    """WebSocket 鉴权。浏览器的 WebSocket 构造器不能自定义请求头,凭证只能走 query。

    两种角色都要验到「这个会话属于你有权限的班级」为止 —— 只验令牌有效
    等于任何一块已配对的大屏都能监听别的班。
    """
    db = SessionLocal()
    try:
        session = db.get(VoteSession, session_id)
        if not session:
            raise HTTPException(404, "session not found")
        if token:
            device = device_from_token(db, token)
            if device.class_id != session.class_id:
                raise HTTPException(403, "device not bound to this class")
            return SCREEN
        if access:
            payload = read_token(access, "access")
            teacher = db.get(Teacher, payload.get("sub"))
            if not teacher or teacher.status != "active":
                raise HTTPException(401, "unknown teacher")
            if int(payload.get("ver", -1)) != teacher.token_version:
                raise HTTPException(401, "stale token")
            from .auth import require_class
            require_class(db, teacher, session.class_id)
            return TEACHER
        raise HTTPException(401, "missing credentials")
    finally:
        db.close()


@app.websocket("/ws/vote/{session_id}")
async def ws_vote(ws: WebSocket, session_id: str) -> None:
    try:
        role = _ws_identity(session_id, ws.query_params.get("token"),
                            ws.query_params.get("access"))
    except HTTPException:
        # 握手前拒绝:客户端收到的是 HTTP 403,而不是「连上了又被踢」
        await ws.close(code=1008)
        return
    await hub.join(session_id, ws, role)
    try:
        while True:
            # 目前上行只用于心跳保活;提交仍走 REST(便于离线 outbox 重试与幂等)
            msg = await ws.receive_json()
            if msg.get("type") == "heartbeat":
                await ws.send_json({"type": "heartbeat_ack"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.leave(session_id, ws)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/teacher/")


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

app.mount("/graph", StaticFiles(directory=PREVIEW_DIR, html=True), name="graph")

for path, name in ((SHARED_DIR, "shared"), (SCREEN_DIR, "screen"), (TEACHER_DIR, "teacher")):
    if os.path.isdir(path):
        app.mount(f"/{name}", StaticFiles(directory=path, html=True), name=name)
