"""教师管理。"""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from ..auth import current_teacher, hash_password, verify_password
from ..db import get_db
from ..models import Teacher, TeacherClass, Class

router = APIRouter(prefix="/api/v1", tags=["teachers"])

def administrator(t: Teacher = Depends(current_teacher)):
    if t.role != "school_admin":
        raise HTTPException(403, "仅学校管理员可维护教师信息")
    return t

class TeacherIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.@-]+$")
    role: Literal["teacher", "group_leader", "school_admin"] = "teacher"
    status: Literal["active", "disabled"] = "active"
    class_ids: list[str] = Field(default_factory=list)

class CreateIn(TeacherIn):
    password: str = Field(min_length=8, max_length=128)

class PasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)

class ChangeIn(PasswordIn):
    old_password: str = Field(min_length=1, max_length=128)

def target(db, admin, teacher_id):
    t = db.get(Teacher, teacher_id)
    if not t or t.tenant_id != admin.tenant_id:
        raise HTTPException(404, "教师不存在")
    return t

def validate(db, admin, body, teacher_id=None):
    if not body.name.strip():
        raise HTTPException(422, "姓名不能为空")
    existing = db.scalar(select(Teacher).where(Teacher.username == body.username))
    if existing and existing.id != teacher_id:
        raise HTTPException(409, "用户名已存在")
    classes = set(db.scalars(select(Class.id).where(Class.tenant_id == admin.tenant_id)))
    if not set(body.class_ids) <= classes:
        raise HTTPException(422, "班级不属于当前学校")

def output(db, t):
    return dict(id=t.id, name=t.name, username=t.username, role=t.role, status=t.status,
        class_ids=list(db.scalars(select(TeacherClass.class_id).where(TeacherClass.teacher_id == t.id))))

@router.get("/management/classes")
def classes(admin: Teacher = Depends(administrator), db: Session = Depends(get_db)):
    return [dict(id=c.id, name=c.name) for c in db.scalars(select(Class).where(Class.tenant_id == admin.tenant_id).order_by(Class.name))]

@router.get("/teachers")
def teachers(admin: Teacher = Depends(administrator), db: Session = Depends(get_db)):
    return [output(db, t) for t in db.scalars(select(Teacher).where(Teacher.tenant_id == admin.tenant_id).order_by(Teacher.created_at))]

@router.post("/teachers", status_code=201)
def create(body: CreateIn, admin: Teacher = Depends(administrator), db: Session = Depends(get_db)):
    validate(db, admin, body)
    t = Teacher(tenant_id=admin.tenant_id, name=body.name.strip(), username=body.username,
        role=body.role, status=body.status, password_hash=hash_password(body.password))
    db.add(t)
    db.flush()
    for cid in set(body.class_ids):
        db.add(TeacherClass(teacher_id=t.id, class_id=cid))
    db.commit()
    return output(db, t)

@router.put("/teachers/{teacher_id}")
def update(teacher_id: str, body: TeacherIn, admin: Teacher = Depends(administrator), db: Session = Depends(get_db)):
    t = target(db, admin, teacher_id)
    validate(db, admin, body, teacher_id)
    if t.id == admin.id and (body.status != "active" or body.role != "school_admin"):
        raise HTTPException(409, "不能停用自己或移除自己的管理员角色")
    t.name, t.username, t.role, t.status = body.name.strip(), body.username, body.role, body.status
    t.token_version += 1
    db.execute(delete(TeacherClass).where(TeacherClass.teacher_id == t.id))
    for cid in set(body.class_ids):
        db.add(TeacherClass(teacher_id=t.id, class_id=cid))
    db.commit()
    return output(db, t)

@router.post("/teachers/{teacher_id}/password", status_code=204)
def reset(teacher_id: str, body: PasswordIn, admin: Teacher = Depends(administrator), db: Session = Depends(get_db)):
    t = target(db, admin, teacher_id)
    t.password_hash = hash_password(body.password)
    t.token_version += 1
    db.commit()

@router.post("/auth/password", status_code=204)
def change(body: ChangeIn, t: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    if not verify_password(body.old_password, t.password_hash):
        raise HTTPException(400, "原密码不正确")
    t.password_hash = hash_password(body.password)
    t.token_version += 1
    db.commit()
