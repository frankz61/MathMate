# -*- coding: utf-8 -*-
"""投票会话:开窗/关窗、大屏 bootstrap、提交、聚合。

对应 docs/DESIGN.md §9.1 与 PRD FR-VOTE-01~13。
本切片**无鉴权** —— DESIGN §10.1 要求教师 JWT、大屏受限 device_token,
上线前必须补齐;当前所有端点均可匿名调用。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Assignment, Class, Pet, PetEnergyEvent, Problem, Student,
    StudentChoice, StudentSubmission, VoteSession,
)
from ..realtime import hub
from ..schemas import (
    BootstrapOut, OpenSessionIn, PetOut, ProblemOut, ProblemTally,
    SessionOut, StudentBrief, SubmissionIn, SubmissionOut, TallyOut,
)

router = APIRouter(prefix="/api/v1", tags=["vote"])

ENERGY_ON_SUBMIT = 10          # FR-PET-04;注意能量与答对率无关(FR-PET-05)
# 归因权重 —— PRD §3.3.3 / DESIGN §7.1,用于挑出主导归因
REASON_WEIGHT = {
    "NO_IDEA": 1.00, "WRONG_UNKNOWN": 0.90, "STUCK_MIDWAY": 0.80,
    "SLOW": 0.50, "WANT_BETTER": 0.40, "WANT_EXTEND": 0.30, "CARELESS": 0.15,
}


def _aware(dt: datetime) -> datetime:
    """SQLite 取回的 datetime 不带时区,统一按 UTC 处理再比较,避免 naive/aware 相减报错。"""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get_session(db: Session, session_id: str) -> VoteSession:
    s = db.get(VoteSession, session_id)
    if not s:
        raise HTTPException(404, {"code": "SESSION_NOT_FOUND", "message": "投票会话不存在"})
    return s


def _class_size(db: Session, class_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(Student)
        .where(Student.class_id == class_id, Student.status == "active")
    ) or 0


def _submitted_count(db: Session, session_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(StudentSubmission)
        .where(StudentSubmission.session_id == session_id)
    ) or 0


def _build_tally(db: Session, session: VoteSession) -> TallyOut:
    problems = db.scalars(
        select(Problem).where(Problem.assignment_id == session.assignment_id).order_by(Problem.seq_no)
    ).all()
    rows = db.execute(
        select(StudentChoice.problem_id, StudentChoice.reason_code, func.count())
        .where(StudentChoice.session_id == session.id)
        .group_by(StudentChoice.problem_id, StudentChoice.reason_code)
    ).all()

    by_problem: dict[str, Counter] = defaultdict(Counter)
    for pid, reason, n in rows:
        by_problem[pid][reason] = n

    size = _class_size(db, session.class_id)
    submitted = _submitted_count(db, session.id)
    out = []
    for p in problems:
        reasons = by_problem.get(p.id, Counter())
        total = sum(reasons.values())
        dominant = None
        if total:
            # 主导归因按 人数 × 归因权重 取胜者,而不是单纯人数 ——
            # 3 个「完全没思路」比 5 个「粗心」更值得占用讲评时间
            dominant = max(reasons, key=lambda r: reasons[r] * REASON_WEIGHT.get(r, 0.5))
        out.append(ProblemTally(
            problem_id=p.id, seq_no=p.seq_no, kp_name=p.kp_name,
            vote_count=total, reasons=dict(reasons), dominant_reason=dominant,
        ))
    out.sort(key=lambda t: (-t.vote_count, t.seq_no))
    return TallyOut(
        session_id=session.id, status=session.status, class_size=size,
        submitted_count=submitted,
        participation=round(submitted / size, 4) if size else 0.0,
        problems=out,
    )


# ---------------------------------------------------------------- 教师端

@router.get("/classes")
def list_classes(db: Session = Depends(get_db)):
    out = []
    for c in db.scalars(select(Class).order_by(Class.name)).all():
        asgs = db.scalars(
            select(Assignment).where(
                Assignment.class_id == c.id, Assignment.review_status == "published"
            ).order_by(Assignment.assign_date.desc())
        ).all()
        active = db.scalar(
            select(VoteSession).where(VoteSession.class_id == c.id, VoteSession.status == "open")
        )
        out.append({
            "id": c.id, "name": c.name, "grade": c.grade,
            "size": _class_size(db, c.id),
            "assignments": [
                {"id": a.id, "title": a.title, "assign_date": a.assign_date,
                 "problem_count": len(a.problems)} for a in asgs
            ],
            "active_session_id": active.id if active else None,
        })
    return out


@router.post("/vote-sessions", response_model=SessionOut)
def open_session(body: OpenSessionIn, db: Session = Depends(get_db)):
    """开启投票窗口(FR-VOTE-01)。同一班级已有开放窗口时直接复用,不重复开窗。"""
    klass = db.get(Class, body.class_id)
    if not klass:
        raise HTTPException(404, {"code": "CLASS_NOT_FOUND", "message": "班级不存在"})
    asg = db.get(Assignment, body.assignment_id)
    if not asg or asg.class_id != klass.id:
        raise HTTPException(404, {"code": "ASSIGNMENT_NOT_FOUND", "message": "作业不存在或不属于该班级"})
    if asg.review_status != "published":
        # 未经教师校对发布的识别结果不得推给学生 —— 这条约束不可妥协
        raise HTTPException(409, {
            "code": "ASSIGNMENT_NOT_PUBLISHED",
            "message": "该作业尚未发布，学生不可见",
            "details": {"assignment_id": asg.id},
        })

    existing = db.scalar(
        select(VoteSession).where(VoteSession.class_id == klass.id, VoteSession.status == "open")
    )
    if existing:
        return SessionOut.model_validate(existing, from_attributes=True)

    s = VoteSession(class_id=klass.id, assignment_id=asg.id, max_choices=body.max_choices)
    db.add(s)
    db.commit()
    return SessionOut.model_validate(s, from_attributes=True)


@router.post("/vote-sessions/{session_id}/close", response_model=SessionOut)
async def close_session(session_id: str, db: Session = Depends(get_db)):
    s = _get_session(db, session_id)
    if s.status == "open":
        s.status = "closed"
        s.closed_at = datetime.now(timezone.utc)
        db.commit()
    await hub.broadcast(session_id, {"type": "session_closed", "session_id": session_id})
    return SessionOut.model_validate(s, from_attributes=True)


@router.get("/vote-sessions/{session_id}/problems/{problem_id}/students",
            response_model=list[StudentBrief])
def drill_down(session_id: str, problem_id: str, db: Session = Depends(get_db)):
    """下钻名单(FR-DEC-04)——**仅教师端**。含真实姓名,大屏不得调用。"""
    _get_session(db, session_id)
    rows = db.execute(
        select(StudentChoice, Student)
        .join(Student, Student.id == StudentChoice.student_id)
        .where(StudentChoice.session_id == session_id, StudentChoice.problem_id == problem_id)
        .order_by(Student.seat_no)
    ).all()
    return [
        StudentBrief(student_id=st.id, name=st.name, seat_no=st.seat_no,
                     reason_code=ch.reason_code, note=ch.note)
        for ch, st in rows
    ]


# ---------------------------------------------------------------- 大屏

@router.get("/vote-sessions/active", response_model=SessionOut | None)
def active_session(class_id: str = Query(...), db: Session = Depends(get_db)):
    s = db.scalar(select(VoteSession).where(VoteSession.class_id == class_id, VoteSession.status == "open"))
    return SessionOut.model_validate(s, from_attributes=True) if s else None


@router.get("/vote-sessions/{session_id}/bootstrap", response_model=BootstrapOut)
def bootstrap(session_id: str, db: Session = Depends(get_db)):
    """大屏一次性预拉取(NFR-AV-01)。

    返回的宠物列表**不含学生姓名** —— 大屏是公开可见的,姓名会让「我不会」
    变成公开信号,产品也就拿不到真实数据了(FR-VOTE-10)。
    """
    s = _get_session(db, session_id)
    klass = db.get(Class, s.class_id)
    asg = db.get(Assignment, s.assignment_id)

    rows = db.execute(
        select(Student, Pet).join(Pet, Pet.student_id == Student.id)
        .where(Student.class_id == s.class_id, Student.status == "active")
        .order_by(Student.seat_no)
    ).all()
    pets = [
        PetOut(student_id=st.id, seat_no=st.seat_no, species=p.species,
               nickname=p.nickname if p.nickname_status == "approved" else "同学",
               hue=p.hue, level=p.level, energy=p.energy)
        for st, p in rows
    ]
    problems = [
        ProblemOut(id=p.id, seq_no=p.seq_no, problem_type=p.problem_type,
                   stem_text=p.stem_text, options=p.options,
                   difficulty=p.difficulty, kp_name=p.kp_name)
        for p in db.scalars(
            select(Problem).where(Problem.assignment_id == s.assignment_id).order_by(Problem.seq_no)
        ).all()
    ]
    submitted = list(db.scalars(
        select(StudentSubmission.student_id).where(StudentSubmission.session_id == session_id)
    ).all())

    return BootstrapOut(
        session=SessionOut.model_validate(s, from_attributes=True),
        class_name=klass.name, seat_layout=klass.seat_layout,
        assignment_title=asg.title, pets=pets, problems=problems,
        submitted_student_ids=submitted,
    )


@router.post("/vote-sessions/{session_id}/submissions", response_model=SubmissionOut)
async def submit(session_id: str, body: SubmissionIn, db: Session = Depends(get_db)):
    """提交选择。幂等 + 可重投(FR-VOTE-06)。

    冲突以 client_ts 大者为准:离线期间的真实先后只有客户端知道。
    """
    s = _get_session(db, session_id)
    if s.status != "open":
        raise HTTPException(409, {"code": "SESSION_CLOSED", "message": "投票已结束"})

    student = db.get(Student, body.student_id)
    if not student or student.class_id != s.class_id:
        raise HTTPException(404, {"code": "STUDENT_NOT_IN_CLASS", "message": "学生不属于该班级"})

    if len(body.choices) > s.max_choices:
        raise HTTPException(422, {
            "code": "TOO_MANY_CHOICES",
            "message": f"最多只能选 {s.max_choices} 道题",
            "details": {"max_choices": s.max_choices, "got": len(body.choices)},
        })

    valid_pids = set(db.scalars(
        select(Problem.id).where(Problem.assignment_id == s.assignment_id)
    ).all())
    for c in body.choices:
        if c.problem_id not in valid_pids:
            raise HTTPException(422, {
                "code": "PROBLEM_NOT_IN_ASSIGNMENT",
                "message": "题目不属于本次作业",
                "details": {"problem_id": c.problem_id},
            })

    # 幂等:同一 key 重复上报(离线补传常见)直接返回既有结果
    dup = db.scalar(select(StudentSubmission).where(
        StudentSubmission.idempotency_key == body.idempotency_key))
    if dup:
        return SubmissionOut(
            status="duplicate", session_id=session_id, student_id=body.student_id,
            choice_count=dup.choice_count, submitted_count=_submitted_count(db, session_id),
            class_size=_class_size(db, s.class_id),
        )

    prev = db.scalar(select(StudentSubmission).where(
        StudentSubmission.session_id == session_id,
        StudentSubmission.student_id == body.student_id))
    client_ts = _aware(body.client_ts)
    if prev and _aware(prev.client_ts) >= client_ts:
        # 迟到的旧数据(离线补传乱序),丢弃而不是覆盖更新的选择
        return SubmissionOut(
            status="stale", session_id=session_id, student_id=body.student_id,
            choice_count=prev.choice_count, submitted_count=_submitted_count(db, session_id),
            class_size=_class_size(db, s.class_id),
        )

    first_time = prev is None
    # 重投:整体替换该生在本会话的全部选择(先删后插,单事务)
    db.query(StudentChoice).filter(
        StudentChoice.session_id == session_id,
        StudentChoice.student_id == body.student_id,
    ).delete(synchronize_session=False)

    for c in body.choices:
        db.add(StudentChoice(
            session_id=session_id, student_id=body.student_id, problem_id=c.problem_id,
            reason_code=c.reason_code, note=c.note, source=body.source, client_ts=client_ts,
        ))

    if prev:
        prev.choice_count = len(body.choices)
        prev.client_ts = client_ts
        prev.idempotency_key = body.idempotency_key
        prev.source = body.source
        prev.submitted_at = datetime.now(timezone.utc)
    else:
        db.add(StudentSubmission(
            session_id=session_id, student_id=body.student_id,
            choice_count=len(body.choices), source=body.source,
            idempotency_key=body.idempotency_key, client_ts=client_ts,
        ))

    pet = db.scalar(select(Pet).where(Pet.student_id == body.student_id))
    if pet and first_time:
        # 只在首次提交时发能量:重投不刷能量,避免把参与变成刷分
        pet.energy += ENERGY_ON_SUBMIT
        pet.level = 1 + pet.energy // 100
        db.add(PetEnergyEvent(pet_id=pet.id, event_type="vote_submit",
                              delta=ENERGY_ON_SUBMIT, ref_id=session_id))

    db.commit()

    tally = _build_tally(db, s)
    await hub.broadcast(session_id, {"type": "tally_update", "tally": tally.model_dump(mode="json")})

    return SubmissionOut(
        status="accepted", session_id=session_id, student_id=body.student_id,
        choice_count=len(body.choices), pet_energy=pet.energy if pet else None,
        submitted_count=tally.submitted_count, class_size=tally.class_size,
    )


@router.get("/vote-sessions/{session_id}/tally", response_model=TallyOut)
def tally(session_id: str, db: Session = Depends(get_db)):
    return _build_tally(db, _get_session(db, session_id))
