# -*- coding: utf-8 -*-
"""鉴权边界的回归测试 —— 对应 DESIGN.md §10.1。

这里测的不是「登录能不能成功」,而是**该拒的有没有真的拒**:

* 匿名能不能读到花名册和归因聚合(全系统最敏感的两类数据);
* 大屏令牌能不能越权读教师端接口、能不能读别的班;
* 教师能不能碰自己不任教的班;
* 令牌被改过一个字节还认不认、access 与 refresh 能不能互换。

前四条任何一条失守,产品的隐私承诺(PRIV-04 / FR-VOTE-10)就不成立了。
"""
from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="mathmate-auth-")
os.environ.setdefault("MATHMATE_DB_URL", f"sqlite:///{os.path.join(_TMP, 'test.db')}")

from conftest import DEMO_LOGIN, login_teacher, pair_screen   # noqa: E402
from fastapi.testclient import TestClient                     # noqa: E402

from app.auth import hash_password                            # noqa: E402
from app.db import SessionLocal, engine                       # noqa: E402
from app.main import app                                      # noqa: E402
from app.models import (                                   # noqa: E402
    Assignment, Base, Class, Teacher, TeacherClass, VoteSession,
)
from app.seed import seed                                     # noqa: E402


@pytest.fixture(scope="module")
def raw():
    """完全不带凭证的客户端。"""
    Base.metadata.drop_all(engine)
    seed(reset=True)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def class_id(raw):
    db = SessionLocal()
    try:
        return db.scalars(Class.__table__.select().with_only_columns(Class.id)).first()
    finally:
        db.close()


@pytest.fixture(scope="module")
def teacher_token(raw):
    return login_teacher(raw)


@pytest.fixture(scope="module")
def th(teacher_token):
    return {"Authorization": f"Bearer {teacher_token}"}


@pytest.fixture(scope="module")
def device_token(raw, th, class_id):
    r = raw.post("/api/v1/screens/pairings", json={"class_id": class_id}, headers=th)
    assert r.status_code == 201, r.text
    r2 = raw.post("/api/v1/screens/pair", json={"code": r.json()["code"]})
    return r2.json()["device_token"]


@pytest.fixture(scope="module")
def dh(device_token):
    return {"X-Device-Token": device_token}


@pytest.fixture(scope="module")
def session_id(raw, th, class_id):
    aid = raw.get(f"/api/v1/assignments?class_id={class_id}", headers=th).json()[0]["id"]
    r = raw.post("/api/v1/vote-sessions",
                 json={"class_id": class_id, "assignment_id": aid}, headers=th)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture(scope="module")
def other(raw, th):
    """另一个班 + 一个不任教该班的教师。"""
    db = SessionLocal()
    try:
        klass = Class(name="初二(9)班", grade=8, seat_layout={"rows": 2, "cols": 2})
        db.add(klass)
        outsider = Teacher(name="王老师", username="outsider",
                           password_hash=hash_password("outsider-pw"), role="teacher")
        admin = Teacher(name="校管理员", username="principal",
                        password_hash=hash_password("admin-pw"), role="school_admin")
        db.add_all([outsider, admin])
        db.flush()
        db.add(TeacherClass(teacher_id=outsider.id, class_id=klass.id))
        # 直接建会话:这个班没有作业,而这里只需要一个「不属于本大屏」的 session_id
        asg = Assignment(class_id=klass.id, title="别班作业", assign_date="2026-08-17",
                         review_status="published")
        db.add(asg)
        db.flush()
        vs = VoteSession(class_id=klass.id, assignment_id=asg.id, status="open")
        db.add(vs)
        db.commit()
        return {"class_id": klass.id, "session_id": vs.id}
    finally:
        db.close()


# ---------------------------------------------------------------- 匿名一律拒绝

@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/classes"),
    ("get", "/api/v1/auth/me"),
    ("get", "/api/v1/pets/species"),
    ("get", "/api/v1/kg/topics"),
    ("post", "/api/v1/vote-sessions"),
])
def test_anonymous_rejected_on_teacher_endpoints(raw, method, path):
    r = getattr(raw, method)(path) if method == "get" else raw.post(path, json={})
    assert r.status_code == 401, f"{path} 竟然放行了匿名请求"


def test_anonymous_cannot_read_roster(raw, class_id):
    """花名册含学生真实姓名(PRIV-03),匿名读到就是数据泄露。"""
    assert raw.get(f"/api/v1/classes/{class_id}/students").status_code == 401
    assert raw.get(f"/api/v1/classes/{class_id}/pets").status_code == 401


def test_anonymous_cannot_read_tally(raw, session_id):
    assert raw.get(f"/api/v1/vote-sessions/{session_id}/tally").status_code == 401
    assert raw.get(f"/api/v1/vote-sessions/{session_id}/students").status_code == 401


def test_anonymous_cannot_use_screen_endpoints(raw, session_id):
    assert raw.get(f"/api/v1/vote-sessions/{session_id}/bootstrap").status_code == 401
    assert raw.post(f"/api/v1/vote-sessions/{session_id}/submissions", json={}).status_code == 401


# ---------------------------------------------------------------- 登录

def test_login_returns_tokens_and_classes(raw):
    d = raw.post("/api/v1/auth/login", json=DEMO_LOGIN).json()
    assert d["access_token"] and d["refresh_token"]
    assert d["teacher"]["role"] == "teacher"
    assert len(d["teacher"]["classes"]) == 1


def test_wrong_password_and_unknown_user_are_indistinguishable(raw):
    """区分开就等于送了一个账号枚举接口。"""
    a = raw.post("/api/v1/auth/login", json={"username": "teacher", "password": "nope"})
    b = raw.post("/api/v1/auth/login", json={"username": "nobody-here", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_login_throttled_after_repeated_failures(raw):
    body = {"username": "brute-target", "password": "x"}
    codes = [raw.post("/api/v1/auth/login", json=body).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5
    assert codes[-1] == 429, "连续失败没有触发限流"


def test_refresh_issues_new_access(raw):
    d = raw.post("/api/v1/auth/login", json=DEMO_LOGIN).json()
    r = raw.post("/api/v1/auth/refresh", json={"refresh_token": d["refresh_token"]})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert raw.get("/api/v1/auth/me",
                   headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_access_and_refresh_are_not_interchangeable(raw):
    """refresh 当 access 用就绕过了 2h 有效期约束。"""
    d = raw.post("/api/v1/auth/login", json=DEMO_LOGIN).json()
    assert raw.get("/api/v1/auth/me",
                   headers={"Authorization": f"Bearer {d['refresh_token']}"}).status_code == 401
    assert raw.post("/api/v1/auth/refresh",
                    json={"refresh_token": d["access_token"]}).status_code == 401


def test_tampered_token_rejected(raw, teacher_token):
    head, body, sig = teacher_token.split(".")
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    r = raw.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {head}.{body}.{flipped}"})
    assert r.status_code == 401
    # 换个 payload 但保留原签名 —— 典型的伪造尝试
    r2 = raw.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {head}.{body}x.{sig}"})
    assert r2.status_code == 401


def test_garbage_token_rejected(raw):
    for bad in ["", "not-a-token", "a.b", "a.b.c.d"]:
        r = raw.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, bad


# ---------------------------------------------------------------- 大屏权限收窄

def test_device_can_use_its_own_screen_endpoints(raw, dh, session_id, class_id):
    assert raw.get(f"/api/v1/vote-sessions/active?class_id={class_id}",
                   headers=dh).status_code == 200
    r = raw.get(f"/api/v1/vote-sessions/{session_id}/bootstrap", headers=dh)
    assert r.status_code == 200
    # 大屏拿到的宠物列表永远不含姓名(FR-VOTE-10)
    assert all("name" not in p for p in r.json()["pets"])


def test_device_token_cannot_reach_teacher_endpoints(raw, dh, session_id, class_id):
    """令牌泄露的止损线:大屏最多只能投垃圾票,读不到名单与聚合。"""
    for path in [
        "/api/v1/classes",
        f"/api/v1/classes/{class_id}/students",
        f"/api/v1/classes/{class_id}/pets",
        f"/api/v1/vote-sessions/{session_id}/tally",
        f"/api/v1/vote-sessions/{session_id}/students",
    ]:
        assert raw.get(path, headers=dh).status_code == 401, f"{path} 对大屏令牌放行了"


def test_device_cannot_touch_another_class(raw, dh, other):
    r = raw.get(f"/api/v1/vote-sessions/active?class_id={other['class_id']}", headers=dh)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "FORBIDDEN"


def test_device_cannot_bootstrap_or_submit_foreign_session(raw, dh, other):
    """只验令牌有效是不够的 —— 会话必须属于令牌绑定的那个班。

    少了这一条,任何一块已配对的大屏都能读到别的班的题目与提交进度。
    """
    foreign = other["session_id"]
    assert raw.get(f"/api/v1/vote-sessions/{foreign}/bootstrap", headers=dh).status_code == 403
    r = raw.post(f"/api/v1/vote-sessions/{foreign}/submissions", headers=dh, json={
        "student_id": "whoever", "choices": [],
        "idempotency_key": "k-cross-class",
        "client_ts": "2026-08-17T08:00:00Z",
    })
    assert r.status_code == 403


def test_revoked_device_is_rejected(raw, th, class_id, session_id):
    """设备丢失时必须能立刻让那块屏失效。"""
    r = raw.post("/api/v1/screens/pairings", json={"class_id": class_id}, headers=th)
    device_id = r.json()["device_id"]
    token = raw.post("/api/v1/screens/pair",
                     json={"code": r.json()["code"]}).json()["device_token"]
    dh2 = {"X-Device-Token": token}
    assert raw.get(f"/api/v1/vote-sessions/{session_id}/bootstrap", headers=dh2).status_code == 200

    assert raw.delete(f"/api/v1/screens/{device_id}", headers=th).status_code == 204
    assert raw.get(f"/api/v1/vote-sessions/{session_id}/bootstrap", headers=dh2).status_code == 401


def test_pairing_code_is_single_use(raw, th, class_id):
    r = raw.post("/api/v1/screens/pairings", json={"class_id": class_id}, headers=th)
    code = r.json()["code"]
    assert raw.post("/api/v1/screens/pair", json={"code": code}).status_code == 200
    again = raw.post("/api/v1/screens/pair", json={"code": code})
    assert again.status_code == 401
    assert again.json()["detail"]["code"] == "BAD_PAIRING_CODE"


def test_unknown_pairing_code_rejected(raw):
    assert raw.post("/api/v1/screens/pair", json={"code": "000000"}).status_code == 401


# ---------------------------------------------------------------- 教师班级边界

def test_teacher_cannot_reach_class_they_do_not_teach(raw, th, other):
    cid = other["class_id"]
    for path in [f"/api/v1/classes/{cid}/students", f"/api/v1/classes/{cid}/pets",
                 f"/api/v1/assignments?class_id={cid}"]:
        r = raw.get(path, headers=th)
        assert r.status_code == 403, f"{path} 越权放行了"


def test_class_list_only_shows_own_classes(raw, th, other):
    ids = [c["id"] for c in raw.get("/api/v1/classes", headers=th).json()]
    assert other["class_id"] not in ids


def test_outsider_cannot_open_session_for_foreign_class(raw, class_id):
    tok = raw.post("/api/v1/auth/login",
                   json={"username": "outsider", "password": "outsider-pw"}).json()["access_token"]
    r = raw.post("/api/v1/vote-sessions", json={"class_id": class_id, "assignment_id": "x"},
                 headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_school_admin_cannot_read_reason_detail(raw, session_id):
    """DESIGN §10.1:校管理员不可见学生归因明细。"""
    tok = raw.post("/api/v1/auth/login",
                   json={"username": "principal", "password": "admin-pw"}).json()["access_token"]
    ah = {"Authorization": f"Bearer {tok}"}
    # 聚合数字可以看
    assert raw.get(f"/api/v1/vote-sessions/{session_id}/tally", headers=ah).status_code == 200
    # 但落到具体学生的归因不行
    assert raw.get(f"/api/v1/vote-sessions/{session_id}/students", headers=ah).status_code == 403


# ---------------------------------------------------------------- WebSocket

def test_websocket_requires_credentials(raw, session_id):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with raw.websocket_connect(f"/ws/vote/{session_id}") as ws:
            ws.receive_text()


def test_websocket_accepts_device_and_teacher(raw, session_id, device_token, teacher_token):
    with raw.websocket_connect(f"/ws/vote/{session_id}?token={device_token}") as ws:
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat_ack"
    with raw.websocket_connect(f"/ws/vote/{session_id}?access={teacher_token}") as ws:
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat_ack"
