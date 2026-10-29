# -*- coding: utf-8 -*-
"""投票闭环的回归测试。

覆盖 PRD 中那些「不可妥协」的规则:选题上限、归因枚举、幂等、离线乱序补传、
大屏不下发姓名、未发布作业不可开窗、关窗后拒收、能量不重复发放。

运行: cd apps/api && python -m pytest -q
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# 必须在导入 app 之前指向独立的临时库,否则会污染开发用的 mathmate.db
_TMP = tempfile.mkdtemp(prefix="mathmate-test-")
os.environ["MATHMATE_DB_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"

from conftest import authenticate                  # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402
from sqlalchemy import select                      # noqa: E402

from app.db import SessionLocal, engine            # noqa: E402
from app.main import app                           # noqa: E402
from app.models import (                           # noqa: E402
    Assignment, Base, Pet, PetEnergyEvent, Problem, Student,
)
from app.seed import seed                          # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(engine)
    seed(reset=True)
    with TestClient(app) as c:
        yield authenticate(c)


@pytest.fixture()
def ctx(client):
    """每个用例一个全新的投票会话,互不干扰。"""
    cls = client.get("/api/v1/classes").json()[0]
    asg = cls["assignments"][0]
    # 关掉可能残留的开放窗口
    active = client.get("/api/v1/vote-sessions/active", params={"class_id": cls["id"]}).json()
    if active:
        client.post(f"/api/v1/vote-sessions/{active['id']}/close")
    s = client.post("/api/v1/vote-sessions",
                    json={"class_id": cls["id"], "assignment_id": asg["id"]}).json()
    bs = client.get(f"/api/v1/vote-sessions/{s['id']}/bootstrap").json()
    yield {"client": client, "cls": cls, "session": s, "bs": bs}
    client.post(f"/api/v1/vote-sessions/{s['id']}/close")


def _submit(ctx, student_id, choices, ts=None, key=None):
    return ctx["client"].post(
        f"/api/v1/vote-sessions/{ctx['session']['id']}/submissions",
        json={
            "idempotency_key": key or uuid.uuid4().hex,
            "student_id": student_id,
            "client_ts": (ts or datetime.now(timezone.utc)).isoformat(),
            "source": "bigscreen",
            "choices": choices,
        },
    )


def test_bootstrap_never_leaks_student_names(ctx):
    """FR-VOTE-10:大屏是公开可见的,姓名会把「我不会」变成公开信号。"""
    payload = ctx["bs"]
    assert len(payload["pets"]) == 40
    blob = str(payload)
    real_names = {s.name for s in SessionLocal().scalars(select(Student)).all()}
    for pet in payload["pets"]:
        assert "name" not in pet
    assert not (real_names & set(blob.split())), "bootstrap 载荷中出现了学生姓名"


def test_reopen_returns_same_session(ctx):
    again = ctx["client"].post("/api/v1/vote-sessions", json={
        "class_id": ctx["cls"]["id"],
        "assignment_id": ctx["cls"]["assignments"][0]["id"],
    }).json()
    assert again["id"] == ctx["session"]["id"]


def test_choice_limit_enforced_server_side(ctx):
    """上限不能只靠前端 —— 手机端/脚本可以绕过 UI。"""
    pets, probs = ctx["bs"]["pets"], ctx["bs"]["problems"]
    r = _submit(ctx, pets[0]["student_id"],
                [{"problem_id": p["id"], "reason_code": "NO_IDEA"} for p in probs[:4]])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "TOO_MANY_CHOICES"


def test_rejects_unknown_reason_and_foreign_problem(ctx):
    pets, probs = ctx["bs"]["pets"], ctx["bs"]["problems"]
    assert _submit(ctx, pets[0]["student_id"],
                   [{"problem_id": probs[0]["id"], "reason_code": "BOGUS"}]).status_code == 422
    assert _submit(ctx, pets[0]["student_id"],
                   [{"problem_id": "not-a-problem", "reason_code": "NO_IDEA"}]).status_code == 422


def test_rejects_student_from_other_class(ctx):
    probs = ctx["bs"]["problems"]
    assert _submit(ctx, "ghost", [{"problem_id": probs[0]["id"], "reason_code": "NO_IDEA"}]).status_code == 404


def test_duplicate_key_is_idempotent(ctx):
    """离线补传会重复上报同一条,不能变成两次提交。"""
    pets, probs = ctx["bs"]["pets"], ctx["bs"]["problems"]
    sid, key = pets[1]["student_id"], uuid.uuid4().hex
    body = [{"problem_id": probs[0]["id"], "reason_code": "NO_IDEA"}]
    first = _submit(ctx, sid, body, key=key).json()
    second = _submit(ctx, sid, body, key=key).json()
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert second["submitted_count"] == first["submitted_count"]


def test_out_of_order_replay_does_not_clobber_newer(ctx):
    """离线队列可能乱序补传:较旧的 client_ts 必须被丢弃而不是覆盖。"""
    pets, probs = ctx["bs"]["pets"], ctx["bs"]["problems"]
    sid = pets[2]["student_id"]
    now = datetime.now(timezone.utc)
    _submit(ctx, sid, [{"problem_id": probs[1]["id"], "reason_code": "SLOW"}], ts=now)
    stale = _submit(ctx, sid, [{"problem_id": probs[2]["id"], "reason_code": "NO_IDEA"}],
                    ts=now - timedelta(minutes=10)).json()
    assert stale["status"] == "stale"

    tally = ctx["client"].get(f"/api/v1/vote-sessions/{ctx['session']['id']}/tally").json()
    picked = {p["problem_id"]: p["vote_count"] for p in tally["problems"] if p["vote_count"]}
    assert picked.get(probs[1]["id"]) == 1
    assert probs[2]["id"] not in picked


def test_resubmit_replaces_choices_and_does_not_regrant_energy(ctx):
    """重投允许(FR-VOTE-06),但能量只在首次提交发放,避免把参与变成刷分。"""
    pets, probs = ctx["bs"]["pets"], ctx["bs"]["problems"]
    sid = pets[3]["student_id"]
    now = datetime.now(timezone.utc)
    first = _submit(ctx, sid, [{"problem_id": probs[0]["id"], "reason_code": "NO_IDEA"},
                               {"problem_id": probs[1]["id"], "reason_code": "SLOW"}], ts=now).json()
    again = _submit(ctx, sid, [{"problem_id": probs[3]["id"], "reason_code": "WANT_BETTER"}],
                    ts=now + timedelta(seconds=30)).json()
    assert again["status"] == "accepted"
    assert again["choice_count"] == 1
    assert again["pet_energy"] == first["pet_energy"], "重投不应再次发放能量"

    db = SessionLocal()
    pet = db.scalar(select(Pet).where(Pet.student_id == sid))
    events = db.scalars(select(PetEnergyEvent).where(PetEnergyEvent.pet_id == pet.id)).all()
    db.close()
    assert len(events) == 1, "同一会话内重复发放了能量"

    dd = ctx["client"].get(
        f"/api/v1/vote-sessions/{ctx['session']['id']}/problems/{probs[0]['id']}/students").json()
    assert sid not in [d["student_id"] for d in dd], "旧选择没有被整体替换"


def test_zero_choice_submission_counts_as_participation(ctx):
    """FR-VOTE-12:要能区分「不想选」与「没来得及选」。"""
    pets = ctx["bs"]["pets"]
    r = _submit(ctx, pets[4]["student_id"], []).json()
    assert r["status"] == "accepted"
    assert r["choice_count"] == 0
    assert r["submitted_count"] >= 1


def test_dominant_reason_weights_urgency_over_headcount(ctx):
    """3 个「完全没思路」应压过 5 个「粗心」—— 归因权重的意义所在。"""
    pets, probs = ctx["bs"]["pets"], ctx["bs"]["problems"]
    target = probs[5]["id"]
    for p in pets[10:15]:
        _submit(ctx, p["student_id"], [{"problem_id": target, "reason_code": "CARELESS"}])
    for p in pets[15:18]:
        _submit(ctx, p["student_id"], [{"problem_id": target, "reason_code": "NO_IDEA"}])
    tally = ctx["client"].get(f"/api/v1/vote-sessions/{ctx['session']['id']}/tally").json()
    row = next(p for p in tally["problems"] if p["problem_id"] == target)
    assert row["reasons"] == {"CARELESS": 5, "NO_IDEA": 3}
    assert row["dominant_reason"] == "NO_IDEA"


def test_closed_session_rejects_submission(ctx):
    pets, probs = ctx["bs"]["pets"], ctx["bs"]["problems"]
    ctx["client"].post(f"/api/v1/vote-sessions/{ctx['session']['id']}/close")
    r = _submit(ctx, pets[20]["student_id"], [{"problem_id": probs[0]["id"], "reason_code": "NO_IDEA"}])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SESSION_CLOSED"


def test_unpublished_assignment_cannot_open_window(client):
    """识别结果未经教师校对发布,不得推给学生 —— 这条约束不可妥协。"""
    db = SessionLocal()
    cls_id = db.scalars(select(Assignment.class_id)).first()
    draft = Assignment(class_id=cls_id, title="草稿作业", assign_date="2026-08-16",
                       review_status="draft")
    db.add(draft)
    db.flush()
    db.add(Problem(assignment_id=draft.id, seq_no=1, stem_text="x", difficulty=1))
    db.commit()
    draft_id = draft.id
    db.close()

    active = client.get("/api/v1/vote-sessions/active", params={"class_id": cls_id}).json()
    if active:
        client.post(f"/api/v1/vote-sessions/{active['id']}/close")

    r = client.post("/api/v1/vote-sessions", json={"class_id": cls_id, "assignment_id": draft_id})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ASSIGNMENT_NOT_PUBLISHED"
