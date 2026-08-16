# -*- coding: utf-8 -*-
"""请求/响应模型。对应 docs/DESIGN.md §9。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .models import REASON_CODES

ReasonCode = Literal[REASON_CODES]  # type: ignore[valid-type]


# ---------- 大屏 bootstrap ----------

class PetOut(BaseModel):
    student_id: str
    seat_no: str
    species: str
    nickname: str
    hue: int
    level: int
    energy: int
    # 刻意不含 student name —— FR-VOTE-10:大屏公开视图不得出现学生真实姓名


class ProblemOut(BaseModel):
    id: str
    seq_no: int
    problem_type: str
    stem_text: str
    options: dict | None = None
    difficulty: int
    kp_name: str | None = None


class SessionOut(BaseModel):
    id: str
    class_id: str
    assignment_id: str
    status: str
    max_choices: int
    opened_at: datetime
    closed_at: datetime | None = None


class BootstrapOut(BaseModel):
    session: SessionOut
    class_name: str
    seat_layout: dict
    assignment_title: str
    pets: list[PetOut]
    problems: list[ProblemOut]
    submitted_student_ids: list[str]


# ---------- 提交 ----------

class ChoiceIn(BaseModel):
    problem_id: str
    reason_code: ReasonCode
    note: str | None = Field(default=None, max_length=64)


class SubmissionIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=64)
    student_id: str
    client_ts: datetime
    source: Literal["bigscreen", "mobile"] = "bigscreen"
    choices: list[ChoiceIn] = Field(default_factory=list)

    @field_validator("choices")
    @classmethod
    def _distinct_problems(cls, v: list[ChoiceIn]) -> list[ChoiceIn]:
        ids = [c.problem_id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("同一道题不能重复选择")
        return v


class SubmissionOut(BaseModel):
    status: Literal["accepted", "duplicate", "stale"]
    session_id: str
    student_id: str
    choice_count: int
    pet_energy: int | None = None
    submitted_count: int
    class_size: int


# ---------- 聚合 ----------

class ProblemTally(BaseModel):
    problem_id: str
    seq_no: int
    kp_name: str | None
    vote_count: int
    reasons: dict[str, int]
    dominant_reason: str | None


class TallyOut(BaseModel):
    session_id: str
    status: str
    class_size: int
    submitted_count: int
    participation: float
    problems: list[ProblemTally]


# ---------- 教师端 ----------

class OpenSessionIn(BaseModel):
    class_id: str
    assignment_id: str
    max_choices: int = Field(default=3, ge=1, le=5)


class StudentBrief(BaseModel):
    """仅教师端可见(FR-DEC-04 下钻名单)。"""

    student_id: str
    name: str
    seat_no: str
    reason_code: str
    note: str | None = None
