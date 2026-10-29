# -*- coding: utf-8 -*-
"""作业录入 → 切题 → 知识点关联 → 校对 → 发布 的回归测试。"""
from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="mathmate-authoring-")
os.environ.setdefault("MATHMATE_DB_URL", f"sqlite:///{os.path.join(_TMP, 'test.db')}")

from conftest import authenticate                  # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402

from app.db import engine                          # noqa: E402
from app.importer import split_problems            # noqa: E402
from app.kg_index import get_index                 # noqa: E402
from app.main import app                           # noqa: E402
from app.models import Base                        # noqa: E402
from app.seed import seed                          # noqa: E402

SAMPLE = """八年级数学 · 今日作业

1. 下列各式中，是分式的是（  ）
A. $$\\frac{x}{2}$$
B. $$\\frac{2}{x}$$
C. $$x+1$$
D. $$\\sqrt{3}$$

2、已知一次函数 $$y=2x-3$$，则它的图象不经过第几象限？
A. 一  B. 二  C. 三  D. 四

3．解不等式组 $$\\begin{cases}x+1>0\\\\ 2x-4<0\\end{cases}$$，并把解集在数轴上表示出来。

4. 如图，在 $$\\triangle ABC$$ 中，$$AB=AC$$，$$\\angle A=40^\\circ$$，求 $$\\angle B$$ 的度数。
"""


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(engine)
    seed(reset=True)
    with TestClient(app) as c:
        yield authenticate(c)


@pytest.fixture()
def cls(client):
    return client.get("/api/v1/classes").json()[0]


@pytest.fixture()
def draft(client, cls):
    a = client.post("/api/v1/assignments",
                    json={"class_id": cls["id"], "title": "测试作业"}).json()
    yield a
    client.delete(f"/api/v1/assignments/{a['id']}")


# ---------------------------------------------------------------- 切题

def test_splitter_handles_mixed_numbering_and_options():
    items = split_problems(SAMPLE)
    assert len(items) == 4, [i["stem_text"][:20] for i in items]
    assert [i["seq_no"] for i in items] == [1, 2, 3, 4]
    # 抬头「八年级数学 · 今日作业」不应被当成题目
    assert "今日作业" not in items[0]["stem_text"]
    # 1. 用 A./B. 换行分列
    assert set(items[0]["options"]) == {"A", "B", "C", "D"}
    # 2、选项挤在同一行也要能切开
    assert set(items[1]["options"]) == {"A", "B", "C", "D"}
    assert items[1]["options"]["A"] == "一"
    # 解答题没有选项,不能被误判
    assert items[2]["options"] == {}
    assert "数轴" in items[2]["stem_text"]


def test_splitter_falls_back_to_single_problem():
    items = split_problems("求证：等腰三角形两底角相等。")
    assert len(items) == 1 and items[0]["seq_no"] == 1


def test_splitter_ignores_letters_that_are_not_options():
    items = split_problems("1. 设 A 为集合，B 为另一集合，判断说法是否正确。")
    assert items[0]["options"] == {}


# ---------------------------------------------------------------- 知识点

def test_suggestions_only_come_from_l1_master():
    """绝不发明新知识点 —— 只能从 L1 母版里选(DESIGN §5)。"""
    ix = get_index()
    sug = ix.suggest("化简分式 $$\\frac{x^2-1}{x+1}$$", grade="八下")
    assert sug, "应给出候选"
    for s in sug:
        assert s["kp_id"] in ix.topics
        assert 0.0 <= s["confidence"] <= 1.0


def test_suggestion_is_topically_sensible():
    ix = get_index()
    got = [s["kp_name"] for s in ix.suggest(
        "已知一组数据 3,5,5,7,10，求这组数据的方差。", grade="八上", top_k=5)]
    assert any("方差" in n or "数据" in n or "平均数" in n for n in got), got


def test_import_text_creates_problems_with_kp_candidates(client, draft):
    a = client.post(f"/api/v1/assignments/{draft['id']}/import-text",
                    json={"text": SAMPLE}).json()
    assert a["problem_count"] == 4
    assert a["unconfirmed_count"] == 4, "刚导入的题必须处于未校对状态"
    for p in a["problems"]:
        assert p["review_status"] == "auto"
        assert p["kp_source"] == "suggested"


# ---------------------------------------------------------------- 校对闸门

def test_cannot_publish_with_unreviewed_problems(client, draft):
    """未经教师逐题过目不得推给学生 —— 这条约束不可妥协(FR-HW-11)。"""
    client.post(f"/api/v1/assignments/{draft['id']}/import-text", json={"text": SAMPLE})
    r = client.post(f"/api/v1/assignments/{draft['id']}/publish")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "UNREVIEWED_PROBLEMS"
    assert len(r.json()["detail"]["details"]["seq_nos"]) == 4


def test_confirm_then_publish(client, draft):
    a = client.post(f"/api/v1/assignments/{draft['id']}/import-text",
                    json={"text": SAMPLE}).json()
    for p in a["problems"]:
        client.patch(f"/api/v1/problems/{p['id']}", json={"confirmed": True})
    r = client.post(f"/api/v1/assignments/{draft['id']}/publish")
    assert r.status_code == 200, r.json()
    assert r.json()["review_status"] == "published"


def test_editing_marks_reviewed_and_manual_kp_overrides(client, draft):
    a = client.post(f"/api/v1/assignments/{draft['id']}/import-text",
                    json={"text": SAMPLE}).json()
    pid = a["problems"][0]["id"]
    target = get_index().list_topics(grade="八下")[0]
    p = client.patch(f"/api/v1/problems/{pid}",
                     json={"kp_id": target["id"], "difficulty": 5}).json()
    assert p["kp_id"] == target["id"]
    assert p["kp_name"] == target["name"]
    assert p["kp_source"] == "manual"
    assert p["kp_confidence"] == 1.0
    assert p["review_status"] == "edited", "教师改过即视为已过目"
    assert p["low_confidence"] is False


def test_rejects_kp_outside_master(client, draft):
    a = client.post(f"/api/v1/assignments/{draft['id']}/import-text",
                    json={"text": SAMPLE}).json()
    r = client.patch(f"/api/v1/problems/{a['problems'][0]['id']}", json={"kp_id": "tp-not-real"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "UNKNOWN_KP"


def test_published_assignment_is_frozen(client, draft):
    """已发布的作业可能正在被投票引用,改动会让题面与已投的票对不上。"""
    a = client.post(f"/api/v1/assignments/{draft['id']}/import-text",
                    json={"text": SAMPLE}).json()
    for p in a["problems"]:
        client.patch(f"/api/v1/problems/{p['id']}", json={"confirmed": True})
    client.post(f"/api/v1/assignments/{draft['id']}/publish")

    pid = a["problems"][0]["id"]
    assert client.patch(f"/api/v1/problems/{pid}", json={"difficulty": 1}).status_code == 409
    assert client.delete(f"/api/v1/problems/{pid}").status_code == 409
    assert client.post(f"/api/v1/assignments/{draft['id']}/import-text",
                       json={"text": SAMPLE}).status_code == 409

    assert client.post(f"/api/v1/assignments/{draft['id']}/unpublish").status_code == 200
    assert client.patch(f"/api/v1/problems/{pid}", json={"difficulty": 1}).status_code == 200


def test_delete_problem_keeps_seq_contiguous(client, draft):
    """题号是学生在大屏上的定位锚点,不能出现空洞。"""
    a = client.post(f"/api/v1/assignments/{draft['id']}/import-text",
                    json={"text": SAMPLE}).json()
    client.delete(f"/api/v1/problems/{a['problems'][1]['id']}")
    after = client.get(f"/api/v1/assignments/{draft['id']}").json()
    assert [p["seq_no"] for p in after["problems"]] == [1, 2, 3]


def test_image_upload_reports_recognition_unavailable(client, draft):
    """识别服务未配置时要明确告知并保留原图,而不是假装成功(NFR-AV-07)。"""
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    r = client.post(f"/api/v1/assignments/{draft['id']}/images",
                    files={"file": ("hw.png", png, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["recognition"]["status"] == "unavailable"
    assert body["images"][0]["uri"].startswith("/uploads/")

    bad = client.post(f"/api/v1/assignments/{draft['id']}/images",
                      files={"file": ("hw.txt", b"nope", "text/plain")})
    assert bad.status_code == 422


# ---------------------------------------------------------------- 教师视图

def test_teacher_sees_choices_and_pets(client, cls):
    asg = next(a for a in client.get("/api/v1/assignments",
                                     params={"class_id": cls["id"]}).json()
               if a["review_status"] == "published")
    active = client.get("/api/v1/vote-sessions/active", params={"class_id": cls["id"]}).json()
    if active:
        client.post(f"/api/v1/vote-sessions/{active['id']}/close")
    s = client.post("/api/v1/vote-sessions",
                    json={"class_id": cls["id"], "assignment_id": asg["id"]}).json()
    bs = client.get(f"/api/v1/vote-sessions/{s['id']}/bootstrap").json()
    stu = bs["pets"][0]
    client.post(f"/api/v1/vote-sessions/{s['id']}/submissions", json={
        "idempotency_key": "authoring-test-key-1", "student_id": stu["student_id"],
        "client_ts": "2026-08-16T01:00:00+00:00", "source": "bigscreen",
        "choices": [{"problem_id": bs["problems"][0]["id"], "reason_code": "NO_IDEA"}],
    })

    detail = client.get(f"/api/v1/vote-sessions/{s['id']}/students").json()
    assert detail["class_size"] == 40
    assert detail["submitted_count"] == 1
    me = next(r for r in detail["students"] if r["student_id"] == stu["student_id"])
    assert me["submitted"] and me["name"], "教师端应能看到真实姓名"
    assert me["choices"][0]["reason_label"] == "完全没思路"
    assert me["pet"]["nickname"]
    assert len(detail["silent"]) == 39

    pets = client.get(f"/api/v1/classes/{cls['id']}/pets").json()
    assert pets["count"] == 40
    assert pets["total_submissions"] >= 1
    # 不按能量排名(FR-PET-06):公开排行榜会变成对倒数学生的羞辱
    seats = [p["seat_no"] for p in pets["pets"]]
    assert seats == sorted(seats)

    client.post(f"/api/v1/vote-sessions/{s['id']}/close")
