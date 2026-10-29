# -*- coding: utf-8 -*-
"""数据模型 —— 对应 docs/DESIGN.md §3.1 / §3.4。

本切片只覆盖「大屏宠物选题」闭环所需的表:班级、学生、宠物、作业、题目、
投票会话、学生选择、提交记录、宠物能量流水。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# PRD §3.3.3 —— 归因是产品核心资产,固定七类枚举,不允许自由文本
REASON_CODES = (
    "NO_IDEA",        # 完全没思路
    "STUCK_MIDWAY",   # 有点思路但卡住
    "WRONG_UNKNOWN",  # 做错了不知错在哪
    "SLOW",           # 会做但太慢
    "WANT_BETTER",    # 想看更好的方法
    "WANT_EXTEND",    # 想看变式拓展
    "CARELESS",       # 粗心算错
)

DEFAULT_TENANT = "t-demo"


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Class(Base):
    __tablename__ = "classes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    name: Mapped[str] = mapped_column(String(64))
    grade: Mapped[int] = mapped_column(Integer)
    # {"rows": 6, "cols": 7} —— 宠物在大屏上按真实座位排布(FR-PET-03)
    seat_layout: Mapped[dict] = mapped_column(JSON, default=dict)

    students: Mapped[list["Student"]] = relationship(back_populates="klass", cascade="all, delete-orphan")


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("class_id", "seat_no", name="uq_student_seat"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    # 姓名属敏感信息:大屏 bootstrap 永不下发(FR-VOTE-10),仅教师端下钻可见
    name: Mapped[str] = mapped_column(String(64))
    student_no: Mapped[str | None] = mapped_column(String(32), nullable=True)   # 学号(PRIV-03 最小化字段)
    seat_no: Mapped[str] = mapped_column(String(8))   # "0305" = 3 排 5 座,后两位用于轻量确认
    status: Mapped[str] = mapped_column(String(16), default="active")

    klass: Mapped[Class] = relationship(back_populates="students")
    pet: Mapped["Pet"] = relationship(back_populates="student", uselist=False, cascade="all, delete-orphan")


class Pet(Base):
    __tablename__ = "pets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), unique=True, index=True
    )
    species: Mapped[str] = mapped_column(String(16))      # cube / sphere / cone / prism / cylinder / pyramid
    nickname: Mapped[str] = mapped_column(String(32))
    nickname_status: Mapped[str] = mapped_column(String(16), default="approved")  # 教师审核开关 FR-PET-02
    hue: Mapped[int] = mapped_column(Integer, default=210)
    level: Mapped[int] = mapped_column(Integer, default=1)
    energy: Mapped[int] = mapped_column(Integer, default=0)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)

    student: Mapped[Student] = relationship(back_populates="pet")


class PetEnergyEvent(Base):
    """能量流水,可审计可回滚。

    FR-PET-05:能量来源刻意**不含**答题正确率 —— 否则弱生的宠物永远长不大,
    产品对最需要帮助的学生失去意义。
    """

    __tablename__ = "pet_energy_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[str] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))   # vote_submit / correction_done / streak
    delta: Mapped[int] = mapped_column(Integer)
    ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    assign_date: Mapped[str] = mapped_column(String(10))          # YYYY-MM-DD
    # 未发布的作业对学生不可见(FR-HW-11):识别结果未经教师校对不得推给学生
    review_status: Mapped[str] = mapped_column(String(16), default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 上传的作业原图 [{uri, name}];教师录入时对照原图核对
    source_images: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    problems: Mapped[list["Problem"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", order_by="Problem.seq_no"
    )


class Problem(Base):
    __tablename__ = "problems"
    __table_args__ = (UniqueConstraint("assignment_id", "seq_no", name="uq_problem_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    seq_no: Mapped[int] = mapped_column(Integer)
    problem_type: Mapped[str] = mapped_column(String(16), default="choice")
    stem_text: Mapped[str] = mapped_column(Text)
    options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    kp_id: Mapped[str | None] = mapped_column(String(32), nullable=True)     # 指向 L1 图谱节点
    kp_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 自动标注的置信度;低于阈值要在校对页高亮,引导教师优先核对(FR-HW-10)
    kp_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    kp_source: Mapped[str] = mapped_column(String(16), default="suggested")   # suggested / manual
    # auto=机器给的还没人看过, edited=教师改过, confirmed=教师确认无误
    review_status: Mapped[str] = mapped_column(String(16), default="auto")

    assignment: Mapped[Assignment] = relationship(back_populates="problems")


class VoteSession(Base):
    """一次投票窗口。同一班级同时只能有一个开放窗口。"""

    __tablename__ = "vote_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="open")   # open / closed
    max_choices: Mapped[int] = mapped_column(Integer, default=3)      # FR-VOTE-04 强制取舍
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# 「一个班同时只能有一个开放窗口」必须是**部分**唯一索引 —— 只约束 status='open' 的行,
# 否则同一班级的历史会话也会互相冲突。SQLite 与 PostgreSQL 语法一致,由数据库兜住并发开窗。
Index(
    "uq_open_session_per_class",
    VoteSession.class_id,
    unique=True,
    sqlite_where=VoteSession.status == "open",
    postgresql_where=VoteSession.status == "open",
)


class StudentSubmission(Base):
    """提交记录 —— 参与率统计与幂等去重的载体。"""

    __tablename__ = "student_submissions"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_submission_once"),
        UniqueConstraint("idempotency_key", name="uq_submission_idem"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("vote_sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    choice_count: Mapped[int] = mapped_column(Integer, default=0)     # 0 = 今天没有想听的题(FR-VOTE-12)
    source: Mapped[str] = mapped_column(String(16), default="bigscreen")
    idempotency_key: Mapped[str] = mapped_column(String(64))
    # 离线期间的真实先后只有客户端知道,冲突以 client_ts 较大者为准
    client_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudentChoice(Base):
    __tablename__ = "student_choices"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id", "problem_id", name="uq_choice_once"),
        CheckConstraint(
            "reason_code IN ('" + "','".join(REASON_CODES) + "')", name="ck_reason_code"
        ),
        Index("ix_choice_tally", "session_id", "problem_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("vote_sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), index=True)
    reason_code: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="bigscreen")
    client_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ==================================================================== 身份与凭证
# 对应 DESIGN.md §3.1 / §10.1。两类主体的威胁模型不同,凭证也不同:
# 教师是「人在用」,给短期 JWT;大屏是「无人值守的教室设备」,给长效但权限极窄的令牌。

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    actor_name: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    object_type: Mapped[str] = mapped_column(String(32))
    object_id: Mapped[str] = mapped_column(String(36))
    changes: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Teacher(Base):
    __tablename__ = "teachers"
    __table_args__ = (UniqueConstraint("tenant_id", "username", name="uq_teacher_username"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    name: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(64), index=True)
    # scrypt 派生,格式见 auth.hash_password;明文口令任何时候都不落库、不进日志
    password_hash: Mapped[str] = mapped_column(String(256))
    # teacher=仅任教班级 / group_leader=本校只读全部班级 / school_admin=组织管理(看不到归因明细)
    role: Mapped[str] = mapped_column(String(16), default="teacher")
    status: Mapped[str] = mapped_column(String(16), default="active")
    # 改密或停用后让已签发的 refresh 立刻作废:token 里带上它,对不上就拒。
    # 没有这一条,30 天有效期的 refresh 在改密之后依然能换出新的 access。
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TeacherClass(Base):
    """任教关系 —— 教师的权限边界就是这张表(DESIGN §10.1)。"""

    __tablename__ = "teacher_classes"

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"), primary_key=True)
    class_id: Mapped[str] = mapped_column(
        ForeignKey("classes.id", ondelete="CASCADE"), primary_key=True)
    subject: Mapped[str] = mapped_column(String(16), default="math", primary_key=True)


class ScreenDevice(Base):
    """教室大屏设备(FR-SYS-04)。

    令牌**存哈希不存明文**:这是本文件唯一一处偏离 DESIGN §3.1 草案 schema 的地方。
    device_token 长期有效且放在教室里,一旦数据库被读走,明文令牌等于直接可用;
    存哈希则拿到库也换不回令牌,而按哈希查找同样是一次索引命中,没有代价。
    """

    __tablename__ = "screen_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_TENANT, index=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="教室大屏")
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # 一次性配对码:教师端生成 → 念给大屏前的人输入 → 换成长效令牌后立即作废
    pairing_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending / active / revoked
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
