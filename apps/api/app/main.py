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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .db import engine
from .models import Base
from .realtime import hub
from .routers import authoring, insight, vote

APPS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREEN_DIR = os.path.join(APPS_DIR, "screen")
TEACHER_DIR = os.path.join(APPS_DIR, "teacher")

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="MathMate API", version="0.1.0", lifespan=lifespan)

# 本切片无鉴权,CORS 全开仅适用于本地开发;
# 上线前须收紧到学校域名,并按 DESIGN §10.1 补齐 JWT / device_token。
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(vote.router)
app.include_router(authoring.router)
app.include_router(insight.router)


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok"}


@app.websocket("/ws/vote/{session_id}")
async def ws_vote(ws: WebSocket, session_id: str) -> None:
    await hub.join(session_id, ws)
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

for path, name in ((SCREEN_DIR, "screen"), (TEACHER_DIR, "teacher")):
    if os.path.isdir(path):
        app.mount(f"/{name}", StaticFiles(directory=path, html=True), name=name)
