# -*- coding: utf-8 -*-
"""LLM 接入的回归测试 —— 全部用假客户端,不打真实网络。

这里守的不是「模型识别得准不准」(那由 scripts/llm_eval_kp.py 离线评测),
而是**模型不听话时系统会怎样**:超时、限流、返回垃圾、编造知识点、
声称候选都不对。这些路径出错的代价比识别不准高得多。
"""
from __future__ import annotations

import io
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="mathmate-llm-")
os.environ.setdefault("MATHMATE_DB_URL", f"sqlite:///{os.path.join(_TMP, 'test.db')}")

from conftest import authenticate                  # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402

from app import llm                                # noqa: E402
from app.config import settings                    # noqa: E402
from app.db import engine                          # noqa: E402
from app.kg_index import get_index                 # noqa: E402
from app.main import app                           # noqa: E402
from app.models import Base                        # noqa: E402
from app.seed import seed                          # noqa: E402

# 最小合法 PNG(1×1);接口只按扩展名与大小校验,像素内容交给识别端处理
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)

FAKE_ITEMS = [
    {"seq_no": 1, "problem_type": "choice",
     "stem_text": "下列各式中，是分式的是（  ）",
     "options": {"A": "$$\\frac{x}{2}$$", "B": "$$\\frac{2}{x}$$",
                 "C": "$$x+1$$", "D": "$$\\sqrt{3}$$"},
     "difficulty": 2, "has_figure": False},
    {"seq_no": 2, "problem_type": "solve",
     "stem_text": "如图，在$$\\triangle ABC$$中，$$AB=AC$$，$$\\angle A=40^\\circ$$，求$$\\angle B$$。",
     "options": {}, "difficulty": 3, "has_figure": True},
]


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed()
    with TestClient(app) as c:
        yield authenticate(c)


@pytest.fixture
def klass(client):
    return client.get("/api/v1/classes").json()[0]["id"]


def _new_assignment(client, class_id, title="LLM 测试"):
    return client.post("/api/v1/assignments",
                       json={"class_id": class_id, "title": title}).json()["id"]


def _upload(client, aid, **form):
    return client.post(
        f"/api/v1/assignments/{aid}/images",
        files={"file": ("hw.png", io.BytesIO(PNG), "image/png")},
        data=form or None,
    )


def _enable(monkeypatch, *, recognize=None, rerank=None, rerank_kp=True):
    """把 LLM 打开,但把两个出口换成假的。

    `rerank_kp` 显式置位:批量重排线上默认是关的(实测不比检索准),
    这里不能靠默认值,否则测的是配置而不是代码。
    """
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(settings, "llm_rerank_kp", rerank_kp)
    if recognize is not None:
        monkeypatch.setattr(llm, "recognize_problems", recognize)
    if rerank is not None:
        monkeypatch.setattr(llm, "rerank_kp", rerank)


# ---------------------------------------------------------------- 降级路径

def test_no_llm_configured_keeps_image_and_says_so(client, klass):
    """未配置密钥时:原图照存,明确回报 unavailable,不产生任何题目。"""
    aid = _new_assignment(client, klass, "未配置 LLM")
    r = _upload(client, aid)
    assert r.status_code == 200
    body = r.json()
    assert body["recognition"]["status"] == "unavailable"
    assert len(body["images"]) == 1          # 原图必须留下,它是降级时唯一依据
    assert body["problem_count"] == 0


def test_recognition_failure_degrades_not_500(client, klass, monkeypatch):
    """识别炸了也必须是 200 + 降级说明 —— 教师的录入流程不能被模型中断。"""
    def boom(*_a, **_k):
        raise llm.LLMUnavailable("HTTP 429")
    _enable(monkeypatch, recognize=boom)

    aid = _new_assignment(client, klass, "识别失败")
    r = _upload(client, aid)
    assert r.status_code == 200
    assert r.json()["recognition"]["status"] == "failed"
    assert "429" in r.json()["recognition"]["message"]
    assert len(r.json()["images"]) == 1
    assert r.json()["problem_count"] == 0


def test_recognize_false_skips_but_still_stores(client, klass, monkeypatch):
    _enable(monkeypatch, recognize=lambda *a, **k: FAKE_ITEMS)
    aid = _new_assignment(client, klass, "只存图")
    r = _upload(client, aid, recognize="false")
    assert r.json()["recognition"]["status"] == "skipped"
    assert r.json()["problem_count"] == 0


# ---------------------------------------------------------------- 识别落库

def test_photo_recognition_still_requires_review(client, klass, monkeypatch):
    """识别得再准也必须逐题校对 —— FR-HW-11 不因为换了模型就松动。"""
    _enable(monkeypatch, recognize=lambda *a, **k: FAKE_ITEMS,
            rerank=lambda items: {})
    aid = _new_assignment(client, klass, "识别校对闸门")
    body = _upload(client, aid).json()

    assert body["recognition"]["status"] == "ok"
    assert body["recognition"]["recognized"] == 2
    assert body["recognition"]["with_figure"] == 1     # 含图题要让教师知道
    assert body["problem_count"] == 2
    assert all(p["review_status"] == "auto" for p in body["problems"])

    r = client.post(f"/api/v1/assignments/{aid}/publish")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "UNREVIEWED_PROBLEMS"


def test_multi_page_upload_appends_contiguous_seq(client, klass, monkeypatch):
    """多页作业逐张传:题号必须连续,它是学生在大屏上的定位锚点。"""
    _enable(monkeypatch, recognize=lambda *a, **k: FAKE_ITEMS, rerank=lambda items: {})
    aid = _new_assignment(client, klass, "多页")
    _upload(client, aid)
    body = _upload(client, aid).json()
    assert [p["seq_no"] for p in body["problems"]] == [1, 2, 3, 4]
    assert len(body["images"]) == 2


def test_replace_true_wipes_previous_page(client, klass, monkeypatch):
    _enable(monkeypatch, recognize=lambda *a, **k: FAKE_ITEMS, rerank=lambda items: {})
    aid = _new_assignment(client, klass, "重传覆盖")
    _upload(client, aid)
    body = _upload(client, aid, replace="true").json()
    assert [p["seq_no"] for p in body["problems"]] == [1, 2]


# ---------------------------------------------------------------- 不发明知识点

def test_rerank_cannot_invent_kp(client, klass, monkeypatch):
    """模型返回候选集之外的 kp_id → 整条丢弃,退回检索结果。

    这是「知识点只能选自 L1 母版」(DESIGN §5)的执行点。靠提示词约束不够,
    必须在代码里挡:编一个听起来很对但不存在的知识点,会污染整个图谱统计。
    """
    seen = {}

    def fake_rerank(items):
        seen["candidates"] = [c["kp_id"] for c in items[0]["candidates"]]
        return {0: {"kp_id": "tp-99-99", "confidence": 0.99,
                    "difficulty": None, "reason": "编的"},
                1: {"kp_id": "完全不存在", "confidence": 1.0,
                    "difficulty": None, "reason": "编的"}}

    _enable(monkeypatch, recognize=lambda *a, **k: FAKE_ITEMS, rerank=fake_rerank)
    aid = _new_assignment(client, klass, "编造知识点")
    body = _upload(client, aid).json()

    assert "tp-99-99" not in seen["candidates"]
    ix = get_index()
    for p in body["problems"]:
        assert p["kp_id"] in ix.topics          # 只可能是母版里的真实 id
        assert p["kp_source"] == "suggested"    # 退回检索,没被模型带跑


def test_rerank_null_clears_annotation_and_blocks_publish(client, klass, monkeypatch):
    """模型说「候选都不对」→ 宁可不标,让教师手选,而不是留一个错的。"""
    _enable(monkeypatch, recognize=lambda *a, **k: FAKE_ITEMS[:1],
            rerank=lambda items: {0: {"kp_id": None, "confidence": 0.0,
                                      "difficulty": None, "reason": "候选都不对"}})
    aid = _new_assignment(client, klass, "拒绝标注")
    body = _upload(client, aid).json()

    p = body["problems"][0]
    assert p["kp_id"] is None
    assert p["kp_source"] == "llm_rejected"
    assert p["low_confidence"] is True        # 校对页要标黄

    client.patch(f"/api/v1/problems/{p['id']}", json={"confirmed": True})
    r = client.post(f"/api/v1/assignments/{aid}/publish")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MISSING_KP"


def test_rerank_applies_pick_and_difficulty(client, klass, monkeypatch):
    picked = {}

    def fake_rerank(items):
        out = {}
        for it in items:
            # 挑候选里的最后一个,确保和检索 top-1 不同,能验证「真的换了」
            cid = it["candidates"][-1]["kp_id"]
            picked[it["index"]] = cid
            out[it["index"]] = {"kp_id": cid, "confidence": 0.88,
                                "difficulty": 5, "reason": "更贴题"}
        return out

    _enable(monkeypatch, recognize=lambda *a, **k: FAKE_ITEMS, rerank=fake_rerank)
    aid = _new_assignment(client, klass, "重排生效")
    body = _upload(client, aid).json()

    for i, p in enumerate(body["problems"]):
        assert p["kp_id"] == picked[i]
        assert p["kp_source"] == "llm"
        assert p["kp_confidence"] == pytest.approx(0.88)
        assert p["difficulty"] == 5


def test_import_text_reports_llm_degradation(client, klass, monkeypatch):
    """降级必须让教师看见 —— 悄悄降级比不降级更糟。"""
    def boom(items):
        raise llm.LLMUnavailable("connect timeout")
    _enable(monkeypatch, rerank=boom)

    aid = _new_assignment(client, klass, "文本导入降级")
    body = client.post(f"/api/v1/assignments/{aid}/import-text",
                       json={"text": "1. 计算 $$\\frac{2}{x}+\\frac{1}{x}$$。\n"
                                     "2. 解方程 $$x^2-6x+5=0$$。",
                             "use_llm": True}).json()
    assert body["annotation"]["llm"] is False
    assert "timeout" in body["annotation"]["message"]
    assert body["problem_count"] == 2          # 题目照样入库
    assert all(p["kp_id"] for p in body["problems"])   # 检索兜底标注仍在


def test_rerank_off_by_default_never_calls_llm(client, klass, monkeypatch):
    """批量重排默认关(实测无增益)。默认路径下不得偷偷发起 LLM 调用。"""
    def must_not_run(items):
        raise AssertionError("默认配置下不应调用 rerank_kp")
    _enable(monkeypatch, rerank=must_not_run, rerank_kp=False)

    aid = _new_assignment(client, klass, "默认不重排")
    body = client.post(f"/api/v1/assignments/{aid}/import-text",
                       json={"text": "1. 计算 $$\\frac{2}{x}+\\frac{1}{x}$$。"}).json()
    assert body["annotation"]["llm"] is False
    assert body["annotation"]["message"] is None       # 不是降级,是本来就不走
    assert body["problems"][0]["kp_source"] == "suggested"


# ---------------------------------------------------------------- 客户端本体

def test_chat_json_tolerates_fences_and_prose(monkeypatch):
    cases = [
        '```json\n[{"index": 0}]\n```',
        '好的，结果如下：\n[{"index": 0}]',
        '[{"index": 0}]',
        '```\n[{"index": 0}]```',
    ]
    for raw in cases:
        monkeypatch.setattr(llm, "chat", lambda *a, **k: raw)
        assert llm.chat_json([{"role": "user", "content": "x"}]) == [{"index": 0}]


def test_chat_json_raises_on_garbage(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "我无法完成这个请求。")
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_json([{"role": "user", "content": "x"}])


def test_recognize_drops_empty_and_clamps(monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: [
        {"stem_text": "  ", "options": {}},                        # 空题干 → 丢
        {"stem_text": "题一", "options": {"A": "x", "B": ""}, "difficulty": 99},
        {"stem_text": "题二", "options": "不是对象", "difficulty": "难"},
    ])
    out = llm.recognize_problems(b"x")
    assert [p["seq_no"] for p in out] == [1, 2]
    assert out[0]["options"] == {"A": "x"}      # 空选项被剔除
    assert out[0]["difficulty"] == 5            # 99 被夹到 5
    assert out[0]["problem_type"] == "choice"
    assert out[1]["options"] == {}
    assert out[1]["difficulty"] == 3            # 非法难度回落默认
    assert out[1]["problem_type"] == "blank"


def test_recognize_wrapped_in_object(monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"problems": [{"stem_text": "题"}]})
    assert len(llm.recognize_problems(b"x")) == 1


# ---------------------------------------------------------------- 图片预处理

def _photo(w, h, *, orientation=None, quality=95):
    """造一张接近真实手机作业照的图(大尺寸、噪点让它压不动)。"""
    from PIL import Image
    import random
    rng = random.Random(7)
    im = Image.new("RGB", (w, h))
    im.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                for _ in range(w * h)])
    buf = io.BytesIO()
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        im.save(buf, "JPEG", quality=quality, exif=exif)
    else:
        im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def test_oversized_photo_is_shrunk_below_gateway_limit():
    """网关单图上限 1 MiB,手机作业照普遍 5–10 MB —— 必须发送前压。

    这条是那次「7.8MB 作业照识别失败」的回归:当时根本没有预处理,
    原图直接发出去,稳定吃 400 "Image input is too large"。
    """
    blob = _photo(3200, 2400)
    assert len(blob) > llm.IMAGE_BUDGET          # 前提:样本确实超限
    out, mime, info = llm.prepare_image(blob, "image/jpeg")
    assert len(out) <= llm.IMAGE_BUDGET
    assert mime == "image/jpeg"
    assert info["prepared"] is True
    assert info["src_bytes"] == len(blob)


def test_exif_orientation_is_applied():
    """手机横竖拍靠 EXIF 标记,像素常常是躺着的。

    不摆正的话教师在浏览器里看到的是正的、模型看到的是歪的。
    """
    blob = _photo(1200, 600, orientation=6)      # 6 = 需顺时针旋转 90°
    out, _, info = llm.prepare_image(blob, "image/jpeg")
    assert info["exif_orientation"] == 6
    w, h = (int(x) for x in info["out_size"].split("×"))
    assert h > w, "旋转后应变成竖图"


def test_small_photo_passes_through_without_damage():
    blob = _photo(600, 400, quality=90)
    out, _, info = llm.prepare_image(blob, "image/jpeg")
    assert info["prepared"] is True
    assert info["out_size"] == "600×400"          # 没超限就不该缩


def test_corrupt_image_degrades_instead_of_raising():
    """不是图片/损坏时不能炸 —— 上传流程不该被预处理中断。"""
    out, mime, info = llm.prepare_image(b"this is not an image", "image/jpeg")
    assert out == b"this is not an image"
    assert info["prepared"] is False
    assert "reason" in info


def test_failure_gives_teacher_actionable_hint_not_raw_json(client, klass, monkeypatch):
    """界面上给人话,原始网关 JSON 只进日志。"""
    def boom(*_a, **_k):
        raise llm.LLMUnavailable(
            'HTTP 400: {"error":{"message":"Image input is too large (max 1 MiB)"}}',
            hint="图片超出识别服务的体积上限。系统已自动压缩仍未通过，请把照片裁到单页再传。",
            status=400)
    _enable(monkeypatch, recognize=boom)

    aid = _new_assignment(client, klass, "报错要说人话")
    body = _upload(client, aid).json()["recognition"]
    assert body["status"] == "failed"
    assert "裁到单页" in body["message"]
    assert "{" not in body["message"]             # 不把 JSON 甩给教师
    assert "max 1 MiB" in body["detail"]          # 但细节仍可取用


def test_hint_mapping_covers_common_gateway_errors():
    cases = {
        401: "鉴权", 403: "密钥权限", 404: "地址或模型名",
        429: "繁忙", 500: "暂时不可用", None: "连不上",
    }
    for status, expect in cases.items():
        assert expect in llm._hint_for(status, "")
    assert "体积上限" in llm._hint_for(400, "Image input is too large (max 1 MiB)")


def test_hint_reads_body_not_just_status():
    """同一个 400 可能是「图太大」也可能是「模型名不对」——一个该改照片、
    一个该改配置。这个网关对未知模型名返回的正是 400 而非 404。"""
    model_err = ("Unable to determine provider for model 'no-such-model-xyz'. "
                 "Use a provider/model prefix")
    assert "模型名" in llm._hint_for(400, model_err)
    assert "重试" not in llm._hint_for(400, model_err)   # 重试永远不会有用

    # 4xx 一律不该建议「稍后重试」,5xx 才该
    assert "重试不会有帮助" in llm._hint_for(418, "teapot")
    assert "可稍后重试" in llm._hint_for(503, "")


def test_log_filter_redacts_api_key():
    """兜底:哪天有人顺手 log 了整个 headers,密钥不能落进日志文件。"""
    import logging
    from app.logging_conf import _RedactKey
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "headers={'Authorization': 'Bearer sk-078fc5a6f2a31985-89394e-b81bf1e6'}",
                            (), None)
    _RedactKey().filter(rec)
    out = rec.getMessage()
    assert "sk-078fc5a6f2a31985-89394e-b81bf1e6" not in out
    assert "sk-078" in out and "…" in out


def test_health_never_echoes_full_key(client, monkeypatch):
    monkeypatch.setenv("MATHMATE_LLM_API_KEY", "sk-0123456789abcdef")
    from app.config import Settings
    red = Settings().redacted()
    assert red["api_key"] == "sk-012…cdef"
    assert "0123456789abcdef" not in str(red)

    body = client.get("/api/v1/llm/health").json()
    assert body["enabled"] is False            # 测试期固定关闭
    assert body["reachable"] is False
