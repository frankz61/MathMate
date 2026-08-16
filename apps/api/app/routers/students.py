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
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Class, Pet, Student, StudentSubmission

router = APIRouter(prefix="/api/v1", tags=["students"])

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
    seat_no: str = Field(min_length=1, max_length=8)
    student_no: str | None = Field(default=None, max_length=32)
    species: str | None = None    # 缺省 = 自动分配
    nickname: str | None = None   # 缺省 = 自动生成
    hue: int | None = Field(default=None, ge=0, le=360)


class StudentPatch(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    seat_no: str = Field(min_length=1, max_length=8)
    student_no: str | None = Field(default=None, max_length=32)
    species: str = Field(min_length=1, max_length=16)
    nickname: str | None = Field(default=None, max_length=32)
    hue: int = Field(ge=0, le=360)


class ImportIn(BaseModel):
    text: str = Field(min_length=1)


# ---------------------------------------------------------------- helpers

def _get_class(db, class_id):
    klass = db.get(Class, class_id)
    if not klass:
        raise HTTPException(404, {"code": "CLASS_NOT_FOUND", "message": "班级不存在"})
    return klass


def _get_student(db, student_id):
    st = db.get(Student, student_id)
    if not st:
        raise HTTPException(404, {"code": "STUDENT_NOT_FOUND", "message": "学生不存在"})
    return st


def _seat_conflict(db, class_id, seat_no, exclude_id=None):
    q = select(Student).where(Student.class_id == class_id, Student.seat_no == seat_no)
    if exclude_id:
        q = q.where(Student.id != exclude_id)
    other = db.scalar(q)
    return other.name if other else None


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
def create_class(body: ClassIn, db=Depends(get_db)):
    klass = Class(name=body.name, grade=body.grade,
                  seat_layout={"rows": body.rows, "cols": body.cols})
    db.add(klass)
    db.commit()
    return {"id": klass.id, "name": klass.name, "grade": klass.grade,
            "seat_layout": klass.seat_layout, "size": 0}


@router.patch("/classes/{class_id}")
def patch_class(class_id: str, body: ClassPatch, db=Depends(get_db)):
    klass = _get_class(db, class_id)
    if body.name is not None:
        klass.name = body.name
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
def list_students(class_id: str, db=Depends(get_db)):
    klass = _get_class(db, class_id)
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
def add_student(class_id: str, body: StudentIn, db=Depends(get_db)):
    _get_class(db, class_id)
    if _seat_conflict(db, class_id, body.seat_no):
        raise HTTPException(409, {"code": "SEAT_TAKEN", "message": "该座位号已被占用"})

    st = Student(class_id=class_id, name=body.name, seat_no=body.seat_no,
                 student_no=body.student_no or None)
    db.add(st)
    db.flush()

    _load_species()
    species = body.species
    if not species or species not in SPECIES_IDS:
        species = _auto_species(db, class_id)
    pet = Pet(
        student_id=st.id, species=species,
        nickname=body.nickname or _default_nickname(species),
        nickname_status="approved",
        hue=body.hue if body.hue is not None else random.choice(HUE_PALETTE),
    )
    db.add(pet)
    _commit(db)
    return _student_out(st, pet, {})


@router.patch("/students/{student_id}")
def patch_student(student_id: str, body: StudentPatch, db=Depends(get_db)):
    st = _get_student(db, student_id)
    if _seat_conflict(db, st.class_id, body.seat_no, exclude_id=student_id):
        raise HTTPException(409, {"code": "SEAT_TAKEN", "message": "该座位号已被占用"})

    _load_species()
    if body.species not in SPECIES_IDS:
        raise HTTPException(422, {"code": "UNKNOWN_SPECIES",
                                  "message": "宠物物种不存在: " + body.species})

    st.name = body.name
    st.seat_no = body.seat_no
    st.student_no = body.student_no or None

    pet = _ensure_pet(db, student_id)
    pet.species = body.species
    pet.nickname = body.nickname or _default_nickname(body.species)
    pet.nickname_status = "approved"
    pet.hue = body.hue
    _commit(db)
    return _student_out(st, pet, {})


@router.delete("/students/{student_id}", status_code=204)
def delete_student(student_id: str, db=Depends(get_db)):
    st = _get_student(db, student_id)
    db.delete(st)
    db.commit()


# ---------------------------------------------------------------- 批量导入

def _split_line(line):
    for ch in (",", "，", "、"):
        line = line.replace(ch, " ")
    return line.split()


@router.post("/classes/{class_id}/students/import")
def import_students(class_id: str, body: ImportIn, db=Depends(get_db)):
    _get_class(db, class_id)
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
        name, seat_no = parts[0], parts[1]
        student_no = parts[2] if len(parts) > 2 else None
        if _seat_conflict(db, class_id, seat_no):
            skipped.append({"line": ln, "reason": "座位号已被占用", "raw": line[:40]})
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
        imported.append(st.id)

    _commit(db)
    return {"imported": len(imported), "skipped": skipped,
            "seat_layout": _get_class(db, class_id).seat_layout}


# ---------------------------------------------------------------- 物种

@router.get("/pets/species")
def pet_species():
    _load_species()
    return [{"id": s, "name": SPECIES_ZH.get(s, s)} for s in SPECIES_IDS]
