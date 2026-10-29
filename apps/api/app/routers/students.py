# -*- coding: utf-8 -*-
"""教师端:学生信息维护(建班、花名册、增删改、批量导入)。

对应 PRD FR-SYS-01(批量导入花名册生成班级与座位表)、PRIV-03(学生信息
最小化:仅姓名/班级/座位号/学号)。**仅教师端** —— 返回真实姓名,
大屏公开视图不得调用(FR-VOTE-10 / PRIV-04)。
"""
from __future__ import annotations

import json
import os
import random
import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import current_teacher, require_class
from ..db import get_db
from ..models import Class, Pet, Student, StudentSubmission, Teacher, TeacherClass

# 花名册含学生真实姓名(PRIV-03),整个模块仅教师端可用。
# 路由级依赖挡匿名访问,班级边界收口在 _get_class / _get_student。
router = APIRouter(prefix="/api/v1", tags=["students"],
                   dependencies=[Depends(current_teacher)])

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # apps/api/app
PETS_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(APP_DIR)), "shared", "pets", "manifest.json")

# 物种清单:唯一来源是 fetch_pets.py 生成的 manifest.json,避免在服务端再抄一份。
SPECIES_IDS = []
SPECIES_ZH = {}


def _load_species():
    if SPECIES_IDS:
        return
    try:
        with open(PETS_MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
        SPECIES_IDS.extend(s["id"] for s in data.get("species", []))
        SPECIES_ZH.update({s["id"]: s["name"] for s in data.get("species", [])})
    except Exception:  # manifest 缺失不阻断学生维护,只是物种校验与自动分配降级
        SPECIES_IDS[:] = []
        SPECIES_ZH.clear()


HUE_PALETTE = [8, 28, 45, 150, 175, 200, 220, 260, 290, 330]


# ---------------------------------------------------------------- schemas

class ClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    grade: int = Field(ge=7, le=9)
    rows: int = Field(default=6, ge=1, le=20)
    cols: int = Field(default=7, ge=1, le=20)


class ClassPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    grade: int | None = Field(default=None, ge=7, le=9)
    rows: int | None = Field(default=None, ge=1, le=20)
    cols: int | None = Field(default=None, ge=1, le=20)


class StudentIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # 输入侧放宽:允许「3-5」「3排5座」,入库前归一成 4 位 RRCC
    seat_no: str = Field(min_length=1, max_length=16)
    student_no: str | None = Field(default=None, max_length=32)
    species: str | None = None    # 缺省 = 自动分配
    nickname: str | None = None   # 缺省 = 自动生成
    hue: int | None = Field(default=None, ge=0, le=360)


class StudentPatch(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    seat_no: str = Field(min_length=1, max_length=16)
    student_no: str | None = Field(default=None, max_length=32)
    species: str = Field(min_length=1, max_length=16)
    nickname: str | None = Field(default=None, max_length=32)
    hue: int = Field(ge=0, le=360)


class ImportIn(BaseModel):
    text: str = Field(min_length=1)


# ---------------------------------------------------------------- helpers

def _get_class(db, class_id, teacher):
    klass = db.get(Class, class_id)
    if not klass:
        raise HTTPException(404, {"code": "CLASS_NOT_FOUND", "message": "班级不存在"})
    require_class(db, teacher, class_id)
    return klass


def _get_student(db, student_id, teacher):
    st = db.get(Student, student_id)
    if not st:
        raise HTTPException(404, {"code": "STUDENT_NOT_FOUND", "message": "学生不存在"})
    # 学生的归属班级决定谁能改他 —— 从对象反查,不信任调用方传来的 class_id
    require_class(db, teacher, st.class_id)
    return st


# 大屏按 seat_no[:2] / seat_no[2:] 定位,后两位是防误触确认码(FR-VOTE-02)。
# 「9999」这种未校验座位会在 CSS Grid 上撑出 99×99 的空洞,所以入库前必须归一。
_SEAT_FOUR = re.compile(r"^\d{4}$")
_SEAT_PAIR = re.compile(r"^(\d{1,2})\s*[-−–/,，排]\s*(\d{1,2})\s*座?$")
_MAX_ROW_COL = 20


class SeatError(ValueError):
    pass


def _parse_seat(seat_no: str) -> str:
    raw = (seat_no or "").strip()
    row = col = None
    if _SEAT_FOUR.fullmatch(raw):
        row, col = int(raw[:2]), int(raw[2:])
    else:
        m = _SEAT_PAIR.fullmatch(raw)
        if m:
            row, col = int(m.group(1)), int(m.group(2))
    if row is None or not (1 <= row <= _MAX_ROW_COL and 1 <= col <= _MAX_ROW_COL):
        raise SeatError("座位号须为 4 位（排+座），排/座各 01–20，例如 0305")
    return f"{row:02d}{col:02d}"


def _seat_or_422(seat_no: str) -> str:
    try:
        return _parse_seat(seat_no)
    except SeatError as exc:
        raise HTTPException(422, {"code": "BAD_SEAT", "message": str(exc)}) from exc


def _norm_student_no(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s or None


def _seat_conflict(db, class_id, seat_no, exclude_id=None):
    q = select(Student).where(Student.class_id == class_id, Student.seat_no == seat_no)
    if exclude_id:
        q = q.where(Student.id != exclude_id)
    other = db.scalar(q)
    return other.name if other else None


def _student_no_conflict(db, class_id, student_no, exclude_id=None):
    if not student_no:
        return None
    q = select(Student).where(Student.class_id == class_id, Student.student_no == student_no)
    if exclude_id:
        q = q.where(Student.id != exclude_id)
    other = db.scalar(q)
    return other.name if other else None


def _fit_layout(klass, seat_no: str) -> None:
    """座位超出当前座位表时自动扩展,避免大屏 CSS Grid 把人丢到表外。"""
    row, col = int(seat_no[:2]), int(seat_no[2:])
    layout = dict(klass.seat_layout or {})
    rows = max(int(layout.get("rows") or 1), row)
    cols = max(int(layout.get("cols") or 1), col)
    if layout.get("rows") != rows or layout.get("cols") != cols:
        klass.seat_layout = {**layout, "rows": rows, "cols": cols}


def _known_species(sp: str | None) -> bool:
    _load_species()
    if not sp:
        return False
    if not SPECIES_IDS:   # manifest 缺失时不阻断教师改宠物
        return True
    return sp in SPECIES_IDS


def _auto_species(db, class_id):
    _load_species()
    if not SPECIES_IDS:
        return "fox"
    used = Counter(db.scalars(
        select(Pet.species).join(Student, Pet.student_id == Student.id)
        .where(Student.class_id == class_id)
    ).all())
    for sp in SPECIES_IDS:
        if used[sp] == 0:
            return sp
    return min(SPECIES_IDS, key=lambda s: used[s])


def _default_nickname(species):
    zh = SPECIES_ZH.get(species) or species
    return zh if zh.startswith("小") else "小" + zh


def _ensure_pet(db, student_id):
    pet = db.scalar(select(Pet).where(Pet.student_id == student_id))
    if not pet:
        pet = Pet(student_id=student_id)
        db.add(pet)
    return pet


def _student_out(st, pet, subs):
    return {
        "student_id": st.id, "name": st.name, "seat_no": st.seat_no,
        "student_no": st.student_no, "status": st.status,
        "pet": None if not pet else {
            "species": pet.species, "nickname": pet.nickname,
            "nickname_status": pet.nickname_status, "hue": pet.hue,
            "level": pet.level, "energy": pet.energy,
            "submissions": subs.get(st.id, 0),
        },
    }


def _commit(db):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, {"code": "SEAT_TAKEN", "message": "座位号已被占用"}) from exc


# ---------------------------------------------------------------- 班级

@router.post("/classes", status_code=201)
def create_class(body: ClassIn, teacher: Teacher = Depends(current_teacher),
                 db=Depends(get_db)):
    if teacher.role == "group_leader":
        raise HTTPException(403, "教研组长仅有班级查看权限")
    if not body.name.strip():
        raise HTTPException(422, "班级名称不能为空")
    klass = Class(name=body.name.strip(), grade=body.grade, tenant_id=teacher.tenant_id,
                  seat_layout={"rows": body.rows, "cols": body.cols})
    db.add(klass)
    db.flush()
    # 建班的人自动成为任教教师,否则他建完立刻就没权限打开这个班
    db.add(TeacherClass(teacher_id=teacher.id, class_id=klass.id))
    db.commit()
    return {"id": klass.id, "name": klass.name, "grade": klass.grade,
            "seat_layout": klass.seat_layout, "size": 0}


@router.patch("/classes/{class_id}")
def patch_class(class_id: str, body: ClassPatch,
                teacher: Teacher = Depends(current_teacher), db=Depends(get_db)):
    klass = _get_class(db, class_id, teacher)
    if teacher.role == "group_leader":
        raise HTTPException(403, "教研组长仅有班级查看权限")
    if body.name is not None and not body.name.strip():
        raise HTTPException(422, "班级名称不能为空")
    layout = dict(klass.seat_layout or {"rows": 6, "cols": 7})
    if body.rows is not None or body.cols is not None:
        rows = body.rows if body.rows is not None else layout.get("rows", 6)
        cols = body.cols if body.cols is not None else layout.get("cols", 7)
        for seat in db.scalars(select(Student.seat_no).where(Student.class_id == class_id)):
            if int(seat[:2]) > rows or int(seat[2:]) > cols:
                raise HTTPException(409, "新布局超出现有学生的座位范围，请先调整学生座位")
    if body.name is not None:
        klass.name = body.name.strip()
    if body.grade is not None:
        klass.grade = body.grade
    if body.rows is not None or body.cols is not None:
        layout = dict(klass.seat_layout or {})
        if body.rows is not None:
            layout["rows"] = body.rows
        if body.cols is not None:
            layout["cols"] = body.cols
        klass.seat_layout = layout
    db.commit()
    return {"id": klass.id, "name": klass.name, "grade": klass.grade,
            "seat_layout": klass.seat_layout}


# ---------------------------------------------------------------- 学生

@router.get("/classes/{class_id}/students")
def list_students(class_id: str, teacher: Teacher = Depends(current_teacher),
                  db=Depends(get_db)):
    klass = _get_class(db, class_id, teacher)
    rows = db.execute(
        select(Student, Pet).join(Pet, Pet.student_id == Student.id, isouter=True)
        .where(Student.class_id == class_id)
        .order_by(Student.seat_no)
    ).all()
    ids = [st.id for st, _ in rows]
    subs = dict(db.execute(
        select(StudentSubmission.student_id, func.count())
        .where(StudentSubmission.student_id.in_(ids))
        .group_by(StudentSubmission.student_id)
    ).all()) if ids else {}
    return {
        "class_id": class_id, "name": klass.name, "grade": klass.grade,
        "seat_layout": klass.seat_layout, "count": len(rows),
        "students": [_student_out(st, pet, subs) for st, pet in rows],
    }


@router.post("/classes/{class_id}/students", status_code=201)
def add_student(class_id: str, body: StudentIn,
                teacher: Teacher = Depends(current_teacher), db=Depends(get_db)):
    klass = _get_class(db, class_id, teacher)
    seat_no = _seat_or_422(body.seat_no)
    student_no = _norm_student_no(body.student_no)
    taken = _seat_conflict(db, class_id, seat_no)
    if taken:
        raise HTTPException(409, {"code": "SEAT_TAKEN",
                                  "message": f"座位 {seat_no} 已是「{taken}」"})
    dup = _student_no_conflict(db, class_id, student_no)
    if dup:
        raise HTTPException(409, {"code": "STUDENT_NO_TAKEN",
                                  "message": f"学号已被「{dup}」使用"})

    _load_species()
    if body.species:
        if not _known_species(body.species):
            raise HTTPException(422, {"code": "UNKNOWN_SPECIES",
                                      "message": "宠物物种不存在: " + body.species})
        species = body.species
    else:
        species = _auto_species(db, class_id)

    st = Student(class_id=class_id, name=body.name.strip(), seat_no=seat_no,
                 student_no=student_no)
    db.add(st)
    db.flush()

    pet = Pet(
        student_id=st.id, species=species,
        nickname=(body.nickname or "").strip() or _default_nickname(species),
        nickname_status="approved",
        hue=body.hue if body.hue is not None else random.choice(HUE_PALETTE),
    )
    db.add(pet)
    _fit_layout(klass, seat_no)
    _commit(db)
    return _student_out(st, pet, {})


@router.patch("/students/{student_id}")
def patch_student(student_id: str, body: StudentPatch,
                  teacher: Teacher = Depends(current_teacher), db=Depends(get_db)):
    st = _get_student(db, student_id, teacher)
    klass = _get_class(db, st.class_id, teacher)
    seat_no = _seat_or_422(body.seat_no)
    student_no = _norm_student_no(body.student_no)
    taken = _seat_conflict(db, st.class_id, seat_no, exclude_id=student_id)
    if taken:
        raise HTTPException(409, {"code": "SEAT_TAKEN",
                                  "message": f"座位 {seat_no} 已是「{taken}」"})
    dup = _student_no_conflict(db, st.class_id, student_no, exclude_id=student_id)
    if dup:
        raise HTTPException(409, {"code": "STUDENT_NO_TAKEN",
                                  "message": f"学号已被「{dup}」使用"})

    if not _known_species(body.species):
        raise HTTPException(422, {"code": "UNKNOWN_SPECIES",
                                  "message": "宠物物种不存在: " + body.species})

    st.name = body.name.strip()
    st.seat_no = seat_no
    st.student_no = student_no

    pet = _ensure_pet(db, student_id)
    pet.species = body.species
    pet.nickname = (body.nickname or "").strip() or _default_nickname(body.species)
    pet.nickname_status = "approved"
    pet.hue = body.hue
    _fit_layout(klass, seat_no)
    _commit(db)
    return _student_out(st, pet, {})


@router.delete("/students/{student_id}", status_code=204)
def delete_student(student_id: str, teacher: Teacher = Depends(current_teacher),
                   db=Depends(get_db)):
    st = _get_student(db, student_id, teacher)
    db.delete(st)
    db.commit()


# ---------------------------------------------------------------- 批量导入

def _split_line(line):
    for ch in (",", "，", "、"):
        line = line.replace(ch, " ")
    return line.split()


@router.post("/classes/{class_id}/students/import")
def import_students(class_id: str, body: ImportIn,
                    teacher: Teacher = Depends(current_teacher), db=Depends(get_db)):
    klass = _get_class(db, class_id, teacher)
    _load_species()

    imported, skipped = [], []
    for ln, raw in enumerate(body.text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = _split_line(line)
        if not parts:
            continue
        if parts[0] in ("姓名", "名字", "name", "Name", "学生"):
            continue
        if len(parts) < 2:
            skipped.append({"line": ln, "reason": "缺座位号", "raw": line[:40]})
            continue
        name, raw_seat = parts[0], parts[1]
        student_no = _norm_student_no(parts[2] if len(parts) > 2 else None)
        try:
            seat_no = _parse_seat(raw_seat)
        except SeatError as exc:
            skipped.append({"line": ln, "reason": str(exc), "raw": line[:40]})
            continue
        taken = _seat_conflict(db, class_id, seat_no)
        if taken:
            skipped.append({"line": ln, "reason": f"座位已被「{taken}」占用", "raw": line[:40]})
            continue
        dup = _student_no_conflict(db, class_id, student_no)
        if dup:
            skipped.append({"line": ln, "reason": f"学号已被「{dup}」使用", "raw": line[:40]})
            continue

        st = Student(class_id=class_id, name=name, seat_no=seat_no, student_no=student_no)
        db.add(st)
        db.flush()
        species = _auto_species(db, class_id)
        db.add(Pet(
            student_id=st.id, species=species,
            nickname=_default_nickname(species), nickname_status="approved",
            hue=random.choice(HUE_PALETTE),
        ))
        _fit_layout(klass, seat_no)
        imported.append(st.id)

    _commit(db)
    return {"imported": len(imported), "skipped": skipped,
            "seat_layout": _get_class(db, class_id, teacher).seat_layout}


# ---------------------------------------------------------------- 物种

@router.get("/pets/species")
def pet_species():
    _load_species()
    return [{"id": s, "name": SPECIES_ZH.get(s, s)} for s in SPECIES_IDS]
