# -*- coding: utf-8 -*-
"""登录与设备配对 —— DESIGN.md §10.1。

两条入口:
* 教师用账号口令换 JWT(access 2h / refresh 30d);
* 大屏用教师生成的一次性配对码换长效 device_token。

配对码而不是「把 token 抄到大屏上」:大屏往往由不懂技术的人开机,
六位数字念得出来、在大屏自带的数字键盘上输得进去,而 43 位的 token 不行。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import (
    ACCESS_TTL, PAIRING_TTL, REFRESH_TTL, AuthError, current_device, current_teacher,
    device_token_hash, make_token, new_device_token, new_pairing_code, read_token,
    require_class, teacher_class_ids, verify_password,
)
from ..db import get_db
from ..models import Class, ScreenDevice, Teacher

router = APIRouter(prefix="/api/v1", tags=["auth"])


# ---------------------------------------------------------------- 登录限流
# 进程内计数,多实例部署时必须换成 Redis —— 但「完全不限流」和「单实例限流」
# 之间的差距,比「单实例限流」和「分布式限流」之间的差距大得多,所以先有再好。

_FAILS: dict[str, list[float]] = {}
_MAX_FAILS, _WINDOW, _LOCKOUT = 5, 300.0, 300.0


def _check_throttle(key: str) -> None:
    now = time.monotonic()
    hits = [t for t in _FAILS.get(key, []) if now - t < _WINDOW]
    _FAILS[key] = hits
    if len(hits) >= _MAX_FAILS:
        wait = int(_LOCKOUT - (now - hits[-1]))
        raise HTTPException(429, {
            "code": "TOO_MANY_ATTEMPTS",
            "message": f"登录尝试过于频繁，请 {max(wait, 1)} 秒后再试",
        })


def _record_fail(key: str) -> None:
    _FAILS.setdefault(key, []).append(time.monotonic())


# ---------------------------------------------------------------- schemas

class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1)


class PairingIn(BaseModel):
    class_id: str
    name: str | None = Field(default=None, max_length=64)


class PairIn(BaseModel):
    code: str = Field(min_length=4, max_length=16)


def _teacher_out(db: Session, t: Teacher) -> dict:
    allowed = teacher_class_ids(db, t)
    q = select(Class).where(Class.tenant_id == t.tenant_id)
    if allowed is not None:
        # 空集合时 SQLAlchemy 2.0 会生成恒假条件,正是想要的语义
        q = q.where(Class.id.in_(allowed))
    return {
        "id": t.id, "name": t.name, "username": t.username, "role": t.role,
        "classes": [{"id": c.id, "name": c.name, "grade": c.grade}
                    for c in db.scalars(q.order_by(Class.name)).all()],
    }


def _issue(t: Teacher) -> dict:
    return {
        "access_token": make_token(t.id, "access", ACCESS_TTL, ver=t.token_version, role=t.role),
        "refresh_token": make_token(t.id, "refresh", REFRESH_TTL, ver=t.token_version),
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
    }


# ---------------------------------------------------------------- 教师

@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    key = body.username.strip().lower()
    _check_throttle(key)
    t = db.scalar(select(Teacher).where(Teacher.username == body.username.strip()))
    # 用户名不存在与口令错误返回同一条消息:区分开就等于送了一个账号枚举接口
    if not t or t.status != "active" or not verify_password(body.password, t.password_hash):
        _record_fail(key)
        raise AuthError("BAD_CREDENTIALS", "用户名或密码不正确")
    _FAILS.pop(key, None)
    return {**_issue(t), "teacher": _teacher_out(db, t)}


@router.post("/auth/refresh")
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    payload = read_token(body.refresh_token, "refresh")
    t = db.get(Teacher, payload.get("sub"))
    if not t or t.status != "active":
        raise AuthError("UNAUTHENTICATED", "账号不存在或已停用")
    if int(payload.get("ver", -1)) != t.token_version:
        raise AuthError("TOKEN_EXPIRED", "账号信息已变更，请重新登录")
    return _issue(t)


@router.get("/auth/me")
def me(teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    return _teacher_out(db, teacher)


# ---------------------------------------------------------------- 大屏配对

@router.post("/screens/pairings", status_code=201)
def create_pairing(body: PairingIn, teacher: Teacher = Depends(current_teacher),
                   db: Session = Depends(get_db)):
    """教师为某个班级生成一次性配对码。"""
    require_class(db, teacher, body.class_id)
    klass = db.get(Class, body.class_id)
    if not klass:
        raise HTTPException(404, {"code": "CLASS_NOT_FOUND", "message": "班级不存在"})
    device = ScreenDevice(
        tenant_id=klass.tenant_id, class_id=klass.id,
        name=(body.name or "").strip() or f"{klass.name} 大屏",
        pairing_code=new_pairing_code(),
        pairing_expires_at=datetime.now(timezone.utc) + PAIRING_TTL,
        status="pending",
    )
    db.add(device)
    db.commit()
    return {
        "device_id": device.id, "class_id": klass.id, "class_name": klass.name,
        "code": device.pairing_code, "expires_in": int(PAIRING_TTL.total_seconds()),
    }


@router.post("/screens/pair")
def pair(body: PairIn, db: Session = Depends(get_db)):
    """大屏用配对码换长效令牌。**无需鉴权** —— 配对码本身就是凭证。

    配对码一次性且 15 分钟过期:六位数字的强度不足以长期暴露,靠短窗口
    + 用完即焚把爆破窗口压到可接受。
    """
    code = body.code.strip()
    device = db.scalar(select(ScreenDevice).where(
        ScreenDevice.pairing_code == code, ScreenDevice.status == "pending"))
    expires = device.pairing_expires_at if device else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)     # SQLite 取回不带时区
    if not device or expires is None or expires <= datetime.now(timezone.utc):
        raise AuthError("BAD_PAIRING_CODE", "配对码无效或已过期")

    token = new_device_token()
    device.token_hash = device_token_hash(token)
    device.pairing_code = None
    device.pairing_expires_at = None
    device.status = "active"
    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    klass = db.get(Class, device.class_id)
    return {
        "device_token": token, "device_id": device.id,
        "class_id": device.class_id, "class_name": klass.name if klass else "",
    }


@router.get("/screens/me")
def screen_me(device: ScreenDevice = Depends(current_device), db: Session = Depends(get_db)):
    """大屏自报家门 —— 班级从令牌推出来,大屏不再需要读班级列表。"""
    klass = db.get(Class, device.class_id)
    return {"device_id": device.id, "class_id": device.class_id,
            "class_name": klass.name if klass else "", "name": device.name}


@router.post("/screens/me/unpair", status_code=204)
def unpair_screen(device: ScreenDevice = Depends(current_device), db: Session = Depends(get_db)):
    """大屏自助解绑当前设备；仅能吊销自己持有的设备令牌。"""
    db.info["actor"] = (device.id, "大屏自助解绑", device.tenant_id)
    device.status = "revoked"
    device.token_hash = None
    device.pairing_code = None
    db.commit()


@router.get("/screens")
def list_screens(class_id: str = Query(...), teacher: Teacher = Depends(current_teacher),
                 db: Session = Depends(get_db)):
    require_class(db, teacher, class_id)
    rows = db.scalars(select(ScreenDevice).where(
        ScreenDevice.class_id == class_id, ScreenDevice.status != "revoked"
    ).order_by(ScreenDevice.created_at)).all()
    return [{
        "id": d.id, "name": d.name, "status": d.status,
        "pairing_code": d.pairing_code if d.status == "pending" else None,
        "last_seen_at": d.last_seen_at, "app_version": d.app_version,
    } for d in rows]


@router.delete("/screens/{device_id}", status_code=204)
def revoke_screen(device_id: str, teacher: Teacher = Depends(current_teacher),
                  db: Session = Depends(get_db)):
    """吊销 —— 设备丢失/教室调整时,必须能立刻让那块屏失效。"""
    device = db.get(ScreenDevice, device_id)
    if not device:
        raise HTTPException(404, {"code": "DEVICE_NOT_FOUND", "message": "设备不存在"})
    require_class(db, teacher, device.class_id)
    device.status = "revoked"
    device.token_hash = None
    device.pairing_code = None
    db.commit()
