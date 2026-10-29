# -*- coding: utf-8 -*-
"""鉴权:教师 JWT + 大屏 device_token —— 对应 DESIGN.md §10.1。

两类主体的威胁模型完全不同,凭证也就不同:

* **教师**:短期 access JWT(2h)+ 长期 refresh(30d)。人在用,过期了重新登录即可。
* **大屏**:长效 device_token,但权限收窄到「只能读绑定班级当日已发布的作业、
  只能往这个班写投票」。它是物理上最不可控的终端 —— 放在教室里,任何人都能
  接触。所以设计取舍不是「让它更难被偷」,而是「被偷了也拿不到有价值的东西」:
  即使令牌泄露,攻击者也读不到学生名单、读不到归因聚合,只能投垃圾票。

JWT 用标准库 HMAC-SHA256 自签,不引 PyJWT —— 签发与校验都在本进程内,
算法**硬编码**为 HS256(从不读 header 里的 alg,从根上排除 alg=none 与算法混淆),
比较一律走 hmac.compare_digest。这与本仓库自己解析 .env 的取舍一致(见 config.py)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import REPO_ROOT
from .db import get_db
from .models import Class, ScreenDevice, Teacher, TeacherClass

log = logging.getLogger(__name__)

ACCESS_TTL = timedelta(hours=2)      # DESIGN §10.1
REFRESH_TTL = timedelta(days=30)
PAIRING_TTL = timedelta(minutes=15)  # 配对码只在教师站在大屏前的这段时间内有效

ROLE_ALL_CLASSES = {"group_leader", "school_admin"}


# ---------------------------------------------------------------- 密钥

def _load_secret() -> bytes:
    """签名密钥:优先环境变量,否则首次启动时生成并落盘。

    刻意**不设代码内默认值** —— 一个提交进仓库的默认密钥意味着任何人都能给
    自己签一张教师令牌。落盘的那份仅供本地零配置开发;生产必须显式设置
    MATHMATE_JWT_SECRET(容器重建、多实例部署时文件方案都不成立)。
    """
    env = os.environ.get("MATHMATE_JWT_SECRET", "").strip()
    if env:
        return env.encode("utf-8")

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".jwt_secret")
    try:
        with open(path, encoding="utf-8") as f:
            saved = f.read().strip()
        if saved:
            return saved.encode("utf-8")
    except OSError:
        pass

    generated = secrets.token_urlsafe(48)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(generated)
        os.chmod(path, 0o600)
    except OSError:
        log.warning("无法写入 %s,本次进程使用临时密钥,重启后所有令牌失效", path)
    log.warning(
        "未设置 MATHMATE_JWT_SECRET,已生成本地开发密钥(%s)。生产部署必须显式配置。",
        os.path.relpath(path, REPO_ROOT),
    )
    return generated.encode("utf-8")


_SECRET = _load_secret()


# ---------------------------------------------------------------- JWT(HS256)

def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: bytes) -> str:
    return _b64u(hmac.new(_SECRET, signing_input, hashlib.sha256).digest())


def make_token(subject: str, typ: str, ttl: timedelta, **claims) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject, "typ": typ,
        "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp()),
        **claims,
    }
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"},
                              separators=(",", ":")).encode("utf-8"))
    body = _b64u(json.dumps(payload, separators=(",", ":"),
                            ensure_ascii=False).encode("utf-8"))
    return f"{header}.{body}.{_sign(f'{header}.{body}'.encode('ascii'))}"


class AuthError(HTTPException):
    def __init__(self, code: str, message: str, status: int = 401):
        super().__init__(status, {"code": code, "message": message},
                         headers={"WWW-Authenticate": "Bearer"} if status == 401 else None)


def read_token(token: str, expect_typ: str) -> dict:
    """校验并解出 payload。算法不从 header 读,固定按 HS256 验。"""
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise AuthError("BAD_TOKEN", "令牌格式不正确")
    header_b64, body_b64, sig = parts
    expected = _sign(f"{header_b64}.{body_b64}".encode("ascii"))
    if not hmac.compare_digest(sig, expected):
        raise AuthError("BAD_TOKEN", "令牌签名不正确")
    try:
        payload = json.loads(_b64u_decode(body_b64))
    except (ValueError, TypeError) as exc:
        raise AuthError("BAD_TOKEN", "令牌无法解析") from exc
    if payload.get("typ") != expect_typ:
        # access 与 refresh 不可互换:refresh 当 access 用就绕过了 2h 有效期
        raise AuthError("BAD_TOKEN", "令牌类型不正确")
    if int(payload.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
        raise AuthError("TOKEN_EXPIRED", "登录已过期，请重新登录")
    return payload


# ---------------------------------------------------------------- 口令

def hash_password(password: str) -> str:
    """scrypt(标准库)。参数取 RFC 7914 的交互式登录档位。"""
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${_b64u(salt)}${_b64u(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, dk_b64 = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        want = _b64u_decode(dk_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=_b64u_decode(salt_b64),
                            n=int(n), r=int(r), p=int(p), dklen=len(want))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, want)


# ---------------------------------------------------------------- 大屏令牌

def new_device_token() -> str:
    return secrets.token_urlsafe(32)


def device_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_pairing_code() -> str:
    """6 位数字 —— 大屏上只有数字键盘,字母码在那台设备上根本没法输入。"""
    return f"{secrets.randbelow(10 ** 6):06d}"


# ---------------------------------------------------------------- 依赖

def _bearer(request: Request) -> str | None:
    raw = request.headers.get("Authorization", "")
    scheme, _, token = raw.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def current_teacher(request: Request, db: Session = Depends(get_db)) -> Teacher:
    token = _bearer(request)
    if not token:
        raise AuthError("UNAUTHENTICATED", "需要教师登录")
    payload = read_token(token, "access")
    teacher = db.get(Teacher, payload.get("sub"))
    if not teacher or teacher.status != "active":
        raise AuthError("UNAUTHENTICATED", "账号不存在或已停用")
    if int(payload.get("ver", -1)) != teacher.token_version:
        raise AuthError("TOKEN_EXPIRED", "账号信息已变更，请重新登录")
    db.info["actor"] = (teacher.id, teacher.name, teacher.tenant_id)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and teacher.role == "group_leader":
        if request.url.path not in {"/api/v1/auth/password"}:
            raise AuthError("FORBIDDEN", "教研组长仅有查看权限", status=403)
    return teacher


def teacher_class_ids(db: Session, teacher: Teacher) -> set[str] | None:
    """教师能碰的班级集合。None = 本校全部(教研组长 / 校管理员)。"""
    if teacher.role in ROLE_ALL_CLASSES:
        return None
    return set(db.scalars(
        select(TeacherClass.class_id).where(TeacherClass.teacher_id == teacher.id)
    ).all())


def require_class(db: Session, teacher: Teacher, class_id: str) -> None:
    allowed = teacher_class_ids(db, teacher)
    if allowed is None:
        # 组长/管理员限本校 —— 跨租户永远不放行
        klass = db.get(Class, class_id)
        if klass is not None and klass.tenant_id == teacher.tenant_id:
            return
    elif class_id in allowed:
        return
    # 刻意用 403 而不是 404:走到这一步身份已经验过,藏班级的存在性对攻击者
    # 没有意义,把话说清楚更利于老师和运维排查「为什么我看不到这个班」。
    raise AuthError("FORBIDDEN", "无权访问该班级", status=403)


def require_reason_detail(teacher: Teacher) -> None:
    """校管理员不可见学生归因明细(DESIGN §10.1)。"""
    if teacher.role == "school_admin":
        raise AuthError("FORBIDDEN", "校管理员不可查看学生归因明细", status=403)


def device_from_token(db: Session, token: str | None) -> ScreenDevice:
    if not token:
        raise AuthError("UNAUTHENTICATED", "大屏未配对")
    device = db.scalar(
        select(ScreenDevice).where(ScreenDevice.token_hash == device_token_hash(token))
    )
    if not device or device.status != "active":
        raise AuthError("DEVICE_UNPAIRED", "大屏令牌无效，请重新配对")
    return device


def current_device(request: Request, db: Session = Depends(get_db)) -> ScreenDevice:
    device = device_from_token(db, request.headers.get("X-Device-Token"))
    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return device


def require_device_class(device: ScreenDevice, class_id: str) -> None:
    if device.class_id != class_id:
        raise AuthError("FORBIDDEN", "大屏只能访问绑定班级", status=403)
