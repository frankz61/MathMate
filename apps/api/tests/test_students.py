# -*- coding: utf-8 -*-
"""学生信息维护(建班、花名册、增删改、批量导入)的回归测试。"""
from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="mathmate-students-")
os.environ.setdefault("MATHMATE_DB_URL", f"sqlite:///{os.path.join(_TMP, 'test.db')}")

from conftest import authenticate                  # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402

from app.db import engine                       # noqa: E402
from app.main import app                           # noqa: E402
from app.models import Base                        # noqa: E402
from app.seed import seed                          # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(engine)
    seed(reset=True)
    with TestClient(app) as c:
        yield authenticate(c)


@pytest.fixture()
def cls(client):
    return client.get("/api/v1/classes").json()[0]


# ---------------------------------------------------------------- 花名册

def test_list_students_returns_roster(client, cls):
    d = client.get(f"/api/v1/classes/{cls['id']}/students").json()
    assert d["count"] == 40
    assert len(d["students"]) == 40
    s0 = d["students"][0]
    assert s0["name"] and s0["seat_no"]
    assert s0["pet"]["species"]
    assert s0["pet"]["hue"] is not None
    # 按座位号排序,不做能量排名
    seats = [s["seat_no"] for s in d["students"]]
    assert seats == sorted(seats)


# ---------------------------------------------------------------- 增删改

def test_add_student(client, cls):
    r = client.post(f"/api/v1/classes/{cls['id']}/students",
                    json={"name": "测试生", "seat_no": "0606", "student_no": "2024001"})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["name"] == "测试生"
    assert s["seat_no"] == "0606"
    assert s["student_no"] == "2024001"
    assert s["pet"]["species"], "新增学生应自动领到一只宠物"
    assert s["pet"]["nickname"], "应自动生成昵称"

    # 座位号唯一
    r2 = client.post(f"/api/v1/classes/{cls['id']}/students",
                     json={"name": "撞座", "seat_no": "0606"})
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "SEAT_TAKEN"

    # 「3-5」归一成 0305;该座种子数据已占用
    r3 = client.post(f"/api/v1/classes/{cls['id']}/students",
                     json={"name": "别名座", "seat_no": "3-5"})
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "SEAT_TAKEN"


def test_patch_student(client, cls):
    d = client.get(f"/api/v1/classes/{cls['id']}/students").json()
    s0 = d["students"][0]
    r = client.patch(f"/api/v1/students/{s0['student_id']}", json={
        "name": "改名", "seat_no": s0["seat_no"], "student_no": "X-001",
        "species": "panda", "nickname": "团团", "hue": 200,
    })
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["name"] == "改名"
    assert s["student_no"] == "X-001"
    assert s["pet"]["species"] == "panda"
    assert s["pet"]["nickname"] == "团团"
    assert s["pet"]["hue"] == 200

    # 非法物种
    bad = client.patch(f"/api/v1/students/{s0['student_id']}", json={
        "name": "改名", "seat_no": s0["seat_no"], "student_no": None,
        "species": "dragonball", "nickname": "x", "hue": 100,
    })
    assert bad.status_code == 422


def test_delete_student(client, cls):
    r = client.post(f"/api/v1/classes/{cls['id']}/students",
                    json={"name": "待删", "seat_no": "0607"}).json()
    assert client.delete(f"/api/v1/students/{r['student_id']}").status_code == 204
    # 删除后花名册里不再出现
    d = client.get(f"/api/v1/classes/{cls['id']}/students").json()
    assert all(s["student_id"] != r["student_id"] for s in d["students"])


# ---------------------------------------------------------------- 批量导入

def test_import_students(client, cls):
    text = "姓名\t座位号\t学号\n甲一\t1001\tA001\n乙二\t1002\tA002\n丙三,1003"
    r = client.post(f"/api/v1/classes/{cls['id']}/students/import", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 3
    assert not r.json()["skipped"]

    d = client.get(f"/api/v1/classes/{cls['id']}/students").json()
    by_seat = {s["seat_no"]: s for s in d["students"]}
    assert by_seat["1001"]["name"] == "甲一"
    assert by_seat["1001"]["student_no"] == "A001"
    assert by_seat["1003"]["student_no"] is None
    assert d["seat_layout"]["rows"] >= 10


def test_reject_bad_seat(client, cls):
    r = client.post(f"/api/v1/classes/{cls['id']}/students",
                    json={"name": "坏座", "seat_no": "9999"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "BAD_SEAT"
    r2 = client.post(f"/api/v1/classes/{cls['id']}/students",
                     json={"name": "坏座", "seat_no": "座位"})
    assert r2.status_code == 422


def test_student_no_unique(client, cls):
    a = client.post(f"/api/v1/classes/{cls['id']}/students",
                    json={"name": "甲", "seat_no": "0901", "student_no": "NO-A"})
    assert a.status_code == 201, a.text
    b = client.post(f"/api/v1/classes/{cls['id']}/students",
                    json={"name": "乙", "seat_no": "0902", "student_no": "NO-A"})
    assert b.status_code == 409
    assert b.json()["detail"]["code"] == "STUDENT_NO_TAKEN"
    # 改自己的学号保持不变应当成功
    r = client.patch(f"/api/v1/students/{a.json()['student_id']}", json={
        "name": "甲", "seat_no": "0901", "student_no": "NO-A",
        "species": "fox", "nickname": "小狐狸", "hue": 30,
    })
    assert r.status_code == 200, r.text


def test_layout_expands_and_normalizes_seat(client, cls):
    r = client.post(f"/api/v1/classes/{cls['id']}/students",
                    json={"name": "远座", "seat_no": "12-1", "species": "panda"})
    assert r.status_code == 201, r.text
    assert r.json()["seat_no"] == "1201"
    assert r.json()["pet"]["species"] == "panda"
    d = client.get(f"/api/v1/classes/{cls['id']}/students").json()
    assert d["seat_layout"]["rows"] >= 12


# ---------------------------------------------------------------- 班级与物种

def test_create_class(client):
    r = client.post("/api/v1/classes", json={"name": "初一(1)班", "grade": 7, "rows": 5, "cols": 8})
    assert r.status_code == 201, r.text
    assert r.json()["seat_layout"] == {"rows": 5, "cols": 8}
    assert client.patch(f"/api/v1/classes/{r.json()['id']}",
                        json={"rows": 6}).json()["seat_layout"]["rows"] == 6


def test_pet_species(client):
    species = client.get("/api/v1/pets/species").json()
    assert len(species) == 24
    ids = {s["id"] for s in species}
    assert {"fox", "panda", "unicorn", "sloth"} <= ids
