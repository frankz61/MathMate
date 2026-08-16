# -*- coding: utf-8 -*-
"""数据库引擎与会话。

首个切片用 SQLite,零配置即可运行;schema 刻意保持 PostgreSQL 兼容
(字符串 UUID、JSON 代替 ARRAY),迁移到 DESIGN.md §3 的 PG 方案时只需换 URL。
未实现的部分:行级安全(RLS)租户隔离、pgvector。
"""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, "mathmate.db")
DATABASE_URL = os.environ.get("MATHMATE_DB_URL", f"sqlite:///{DEFAULT_DB}")

engine = create_engine(
    DATABASE_URL,
    future=True,
    # SQLite 默认禁止跨线程共享连接,而 uvicorn 的同步端点跑在线程池里
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 连接期钩子
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")   # 默认是关的,不开外键约束形同虚设
        cur.execute("PRAGMA journal_mode=WAL")  # 读写并发:大屏轮询与教师端写入会同时发生
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
