# -*- coding: utf-8 -*-
"""教师端:学生选择明细与宠物状态。

对应 PRD FR-DEC-04(下钻名单)与 FR-PET-04。
**仅教师端** —— 这些接口返回真实姓名与个人归因,属敏感教学数据,
大屏公开视图不得调用(FR-VOTE-10 / PRIV-06)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_teacher, require_class, require_reason_detail
from ..db import get_db
from ..models import (
    Pet, PetEnergyEvent, Problem, Student, StudentChoice, StudentSubmission, Teacher,
    VoteSession,
)

# 这两个端点都返回真实姓名与个人归因,是全系统最敏感的读接口。
router = APIRouter(prefix="/api/v1", tags=["insight"],
                   dependencies=[Depends(current_teacher)])

REASON_LABEL = {
    "NO_IDEA": "完全没思路", "STUCK_MIDWAY": "中间卡住了", "WRONG_UNKNOWN": "做错不知错在哪",
    "SLOW": "会做但太慢", "WANT_BETTER": "想看更好方法", "WANT_EXTEND": "想看变式拓展",
    "CARELESS": "粗心算错",
}


@router.get("/vote-sessions/{session_id}/students")
def session_students(session_id: str, teacher: Teacher = Depends(current_teacher),
                     db: Session = Depends(get_db)):
    """按学生列出本次选题明细 —— 谁选了什么、为什么、还是没交。"""
    s = db.get(VoteSession, session_id)
    if not s:
        raise HTTPException(404, {"code": "SESSION_NOT_FOUND", "message": "投票会话不存在"})
    require_class(db, teacher, s.class_id)
    require_reason_detail(teacher)

    roster = db.execute(
        select(Student, Pet).join(Pet, Pet.student_id == Student.id, isouter=True)
        .where(Student.class_id == s.class_id, Student.status == "active")
        .order_by(Student.seat_no)
    ).all()

    subs = {r.student_id: r for r in db.scalars(
        select(StudentSubmission).where(StudentSubmission.session_id == session_id)).all()}

    choices: dict[str, list] = {}
    rows = db.execute(
        select(StudentChoice, Problem)
        .join(Problem, Problem.id == StudentChoice.problem_id)
        .where(StudentChoice.session_id == session_id)
        .order_by(Problem.seq_no)
    ).all()
    for ch, p in rows:
        choices.setdefault(ch.student_id, []).append({
            "problem_id": p.id, "seq_no": p.seq_no, "kp_name": p.kp_name,
            "reason_code": ch.reason_code, "reason_label": REASON_LABEL.get(ch.reason_code, ch.reason_code),
            "note": ch.note,
        })

    out = []
    for st, pet in roster:
        sub = subs.get(st.id)
        out.append({
            "student_id": st.id, "name": st.name, "seat_no": st.seat_no,
            "submitted": sub is not None,
            "submitted_at": sub.submitted_at if sub else None,
            "source": sub.source if sub else None,
            "choices": choices.get(st.id, []),
            "pet": None if not pet else {
                "nickname": pet.nickname, "species": pet.species, "hue": pet.hue,
                "level": pet.level, "energy": pet.energy,
            },
        })
    return {
        "session_id": session_id, "status": s.status,
        "class_size": len(out),
        "submitted_count": sum(1 for r in out if r["submitted"]),
        "silent": [r["seat_no"] for r in out if not r["submitted"]],
        "students": out,
    }


@router.get("/classes/{class_id}/pets")
def class_pets(class_id: str, teacher: Teacher = Depends(current_teacher),
               db: Session = Depends(get_db)):
    """宠物花名册。

    刻意**不做排名**(FR-PET-06):按座位号排序而非能量降序。
    公开的个人排行榜会变成对倒数学生的公开羞辱,与产品目的相悖。
    """
    require_class(db, teacher, class_id)
    rows = db.execute(
        select(Student, Pet).join(Pet, Pet.student_id == Student.id)
        .where(Student.class_id == class_id, Student.status == "active")
        .order_by(Student.seat_no)
    ).all()

    counts = dict(db.execute(
        select(PetEnergyEvent.pet_id, func.count())
        .group_by(PetEnergyEvent.pet_id)
    ).all())
    subs = dict(db.execute(
        select(StudentSubmission.student_id, func.count())
        .group_by(StudentSubmission.student_id)
    ).all())

    pets = [{
        "student_id": st.id, "name": st.name, "seat_no": st.seat_no,
        "nickname": p.nickname, "species": p.species, "hue": p.hue,
        "level": p.level, "energy": p.energy,
        "nickname_status": p.nickname_status,
        "energy_events": counts.get(p.id, 0),
        "submissions": subs.get(st.id, 0),
    } for st, p in rows]

    total = len(pets) or 1
    return {
        "class_id": class_id,
        "count": len(pets),
        "avg_energy": round(sum(p["energy"] for p in pets) / total, 1),
        "total_submissions": sum(p["submissions"] for p in pets),
        # 从未提交过的学生 —— 这是需要老师私下关注的信号,不是用来公示的
        "never_submitted": [p["seat_no"] for p in pets if p["submissions"] == 0],
        "pets": pets,
    }
