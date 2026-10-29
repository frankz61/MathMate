# -*- coding: utf-8 -*-
"""生成演示数据:一个班 40 名学生 + 宠物,一份已发布的作业。

作业题目取自项目已有的 data/practice_recommend.json(TAL-SCQ5K 对齐结果),
挑选该班年级对应的知识点,避免用假题占位。

用法:
    python -m app.seed          # 已有数据则跳过
    python -m app.seed --reset  # 清库重建
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

from sqlalchemy import select

from .auth import hash_password
from .db import SessionLocal, engine
from .models import (
    DEFAULT_TENANT, Assignment, Base, Class, Pet, Problem, Student, Teacher,
    TeacherClass, utcnow,
)

# 演示账号。**仅供本地演示** —— 口令写在仓库里就等于没有口令,
# 部署到任何能被别人访问的地方之前必须改掉(或直接删掉这个账号)。
DEMO_TEACHER = {"username": "teacher", "password": "mathmate", "name": "李老师"}

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA = os.path.join(REPO, "data")

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = ["子涵", "浩然", "欣怡", "宇轩", "雨萱", "梓豪", "思远", "梦琪", "俊杰", "佳怡",
         "博文", "诗涵", "睿哲", "雅雯", "泽楷", "若曦", "浩宇", "晨曦", "嘉懿", "语彤"]

# 宠物物种(PRD FR-PET-01,A8 假设定案:几何精灵 → 十二生肖 → 24 种萌宠)。
# 必须与 apps/shared/pet.js 的 SPECIES_ZH 及 scripts/fetch_pets.py 的 ROSTER 一致;
# 24 种让 40 人班里每种最多 2 人重样,比 12 种时的 4 人重样好认得多。
SPECIES = ["fox", "panda", "penguin", "owl", "dog", "hedgehog",
           "frog", "turtle", "unicorn", "bear", "chick", "dolphin",
           "tiger", "octopus", "otter", "seal", "trex", "sauropod",
           "butterfly", "chipmunk", "pig", "rabbit", "flamingo", "sloth"]
NICK_A = ["小", "大", "团", "圆", "胖", "跳", "闪", "乖"]
NICK_B = ["灵灵", "朵朵", "旺旺", "福福", "跳跳", "闹闹", "乖乖", "多多", "淘淘", "安安"]

GRADE = 8
ROWS, COLS = 6, 7
STUDENT_COUNT = 40
PROBLEM_COUNT = 12
# 初二班级:优先取八上/八下的知识点,不够再放宽到七年级
PREFERRED_GRADES = ("八上", "八下")
FALLBACK_GRADES = ("七下", "七上")


def _pick_problems(rng: random.Random) -> list[dict]:
    path = os.path.join(DATA, "practice_recommend.json")
    with open(path, encoding="utf-8") as f:
        pr = json.load(f)

    def bucket(grades):
        out = []
        for tid, t in pr["topics"].items():
            if t.get("grade") not in grades:
                continue
            for q in t.get("questions", []):
                if not q.get("problem") or not q.get("options"):
                    continue
                out.append({
                    "kp_id": tid, "kp_name": t["name"],
                    "stem_text": q["problem"].strip(),
                    "options": q["options"],
                    "answer": q.get("answer"),
                    # 数据集 difficulty 是 0–4,产品口径是 1–5
                    "difficulty": min(5, max(1, int(q.get("difficulty") or 2) + 1)),
                })
        return out

    pool = bucket(PREFERRED_GRADES)
    if len(pool) < PROBLEM_COUNT:
        pool += bucket(FALLBACK_GRADES)
    if not pool:
        raise SystemExit("practice_recommend.json 中没有可用题目")

    # 每个知识点最多取 2 题,避免一份作业全压在同一个考点上
    rng.shuffle(pool)
    chosen, per_kp = [], {}
    for q in pool:
        if per_kp.get(q["kp_id"], 0) >= 2:
            continue
        per_kp[q["kp_id"]] = per_kp.get(q["kp_id"], 0) + 1
        chosen.append(q)
        if len(chosen) == PROBLEM_COUNT:
            break
    return chosen


def _ensure_demo_teacher(db) -> None:
    """建出演示账号,并补齐它对库里所有班级的任教关系。

    幂等,且**账号与任教关系分开判断** —— 班级可能是在账号之后才建出来的
    (seed 自己就是这个顺序),只看「有没有教师」会留下一个任教 0 个班的账号,
    登录成功但什么都看不见。

    对已有的演示库直接再跑一次 `python -m app.seed` 是安全的:只补账号,
    不动任何学生数据。
    """
    t = db.scalar(select(Teacher).where(Teacher.username == DEMO_TEACHER["username"]))
    created = t is None
    if created:
        t = Teacher(
            name=DEMO_TEACHER["name"], username=DEMO_TEACHER["username"],
            password_hash=hash_password(DEMO_TEACHER["password"]),
            role="teacher", tenant_id=DEFAULT_TENANT,
        )
        db.add(t)
        db.flush()

    linked = set(db.scalars(
        select(TeacherClass.class_id).where(TeacherClass.teacher_id == t.id)).all())
    added = [c for c in db.scalars(select(Class)).all() if c.id not in linked]
    for klass in added:
        db.add(TeacherClass(teacher_id=t.id, class_id=klass.id))
    if created or added:
        db.commit()
    if created:
        print(f"已建演示教师账号:{DEMO_TEACHER['username']} / {DEMO_TEACHER['password']}")
    if added:
        print(f"        已挂上 {len(added)} 个班级")


def seed(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    rng = random.Random(20260815)   # 固定种子:每次生成同一批演示数据,便于复现问题
    db = SessionLocal()
    try:
        # 先补账号再判断是否跳过 —— 鉴权是后加的,加之前建的库里一个教师都没有,
        # 不补的话那些演示数据会因为「登录不进去」而整体不可用。
        _ensure_demo_teacher(db)
        if db.scalar(select(Class).limit(1)) is not None:
            print("已有数据,跳过(需要重建请加 --reset)")
            return

        klass = Class(name="初二(3)班", grade=GRADE, seat_layout={"rows": ROWS, "cols": COLS})
        db.add(klass)
        db.flush()

        used_names, used_nicks = set(), set()
        seats = [(r, c) for r in range(1, ROWS + 1) for c in range(1, COLS + 1)][:STUDENT_COUNT]
        for i, (r, c) in enumerate(seats):
            while True:
                name = rng.choice(SURNAMES) + rng.choice(GIVEN)
                if name not in used_names:
                    used_names.add(name)
                    break
            while True:
                nick = rng.choice(NICK_A) + rng.choice(NICK_B)
                if nick not in used_nicks:
                    used_nicks.add(nick)
                    break
            st = Student(class_id=klass.id, name=name, seat_no=f"{r:02d}{c:02d}")
            db.add(st)
            db.flush()
            db.add(Pet(
                student_id=st.id,
                species=SPECIES[i % len(SPECIES)],
                nickname=nick,
                hue=rng.choice([8, 28, 45, 150, 175, 200, 220, 260, 290, 330]),
                level=1, energy=rng.choice([0, 10, 20, 30]),
            ))

        asg = Assignment(
            class_id=klass.id,
            title="今日作业 · 第 12 周",
            assign_date=utcnow().date().isoformat(),
            review_status="published",       # 已经教师校对并发布,大屏才可见
            published_at=utcnow(),
        )
        db.add(asg)
        db.flush()

        for seq, q in enumerate(_pick_problems(rng), start=1):
            db.add(Problem(
                assignment_id=asg.id, seq_no=seq, problem_type="choice",
                stem_text=q["stem_text"], options=q["options"], answer=q["answer"],
                difficulty=q["difficulty"], kp_id=q["kp_id"], kp_name=q["kp_name"],
                # 这份演示作业代表「已经教师校对过并发布」的状态,
                # 否则会出现已发布却仍有未校对题目的矛盾
                kp_source="manual", kp_confidence=1.0, review_status="confirmed",
            ))

        db.commit()
        _ensure_demo_teacher(db)

        print(f"已生成:{klass.name}({STUDENT_COUNT} 名学生 + 宠物)")
        print(f"        作业「{asg.title}」共 {PROBLEM_COUNT} 题")
        print(f"        class_id={klass.id}")
        print(f"        assignment_id={asg.id}")
        print()
        print(f"  教师端登录:{DEMO_TEACHER['username']} / {DEMO_TEACHER['password']}")
        print("  大屏首次打开需要配对:教师端「班级管理 → 配对大屏」生成 6 位配对码")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="清空并重建")
    seed(reset=ap.parse_args().reset)
