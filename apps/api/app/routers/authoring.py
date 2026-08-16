# -*- coding: utf-8 -*-
"""教师端:每日作业录入 → 切题 → 知识点关联 → 校对 → 发布。

对应 PRD FR-HW-01~14 与 DESIGN §5。

两条录入路径,产物完全一致(都落成 `review_status="auto"` 的待校对题目):
  · 照片:多模态 LLM 版面分析 + 公式识别(FR-HW-04/05),见 app/llm.py
  · 文本:粘贴整段作业按题号切分(app/importer.py)

知识点关联是**检索给候选 + LLM 在候选里选**:候选恒来自 L1 母版,
LLM 不参与生成 id,所以「永不发明知识点」这条约束由结构保证而非靠提示词。
LLM 不可用时(未配置密钥、超时、限流)全部自动降级,不阻断录入 —— NFR-AV-07。
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import settings
from ..db import get_db
from ..importer import split_problems
from ..kg_index import get_index
from ..models import Assignment, Class, Problem, VoteSession, utcnow

log = logging.getLogger("mathmate.authoring")

router = APIRouter(prefix="/api/v1", tags=["authoring"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".heic": "image/heic"}
# 这个上限只管**留存**:发给模型的那一份由 llm.prepare_image 压到 1 MiB 内。
# 定 24MB 是因为 4800 万像素手机随手一拍就 15–20MB,而原图是识别失败时
# 教师唯一的降级依据,不该因为大而被拒收。
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
# 低于此置信度在校对页标黄(FR-HW-10)
LOW_CONFIDENCE = 0.55
# 送给 LLM 重排的候选数。检索 top-8 的章节召回已接近饱和,再多只是给模型添噪。
RERANK_CANDIDATES = 8


# ---------------------------------------------------------------- schemas

class AssignmentIn(BaseModel):
    class_id: str
    title: str = Field(min_length=1, max_length=128)
    assign_date: str | None = None


class ImportTextIn(BaseModel):
    text: str = Field(min_length=1)
    replace: bool = True          # 覆盖已有题目(重新导入)还是追加
    use_llm: bool | None = None   # 知识点是否过 LLM 重排;None = 跟随配置(默认关)


class ProblemIn(BaseModel):
    stem_text: str = Field(min_length=1)
    options: dict | None = None
    answer: str | None = None
    difficulty: int = Field(default=3, ge=1, le=5)
    problem_type: str = "choice"


class ProblemPatch(BaseModel):
    stem_text: str | None = None
    options: dict | None = None
    answer: str | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)
    kp_id: str | None = None
    confirmed: bool | None = None


class SuggestIn(BaseModel):
    stem_text: str
    grade: str | None = None
    use_llm: bool = False    # 教师在校对页输入时会频繁触发,默认走快的检索路径


# ---------------------------------------------------------------- helpers

def _grade_label(db: Session, class_id: str) -> str | None:
    """按班级年级给出当前学期标签,用于给知识点推荐做柔性加权。"""
    klass = db.get(Class, class_id)
    if not klass:
        return None
    m = {7: "七", 8: "八", 9: "九"}.get(klass.grade)
    if not m:
        return None
    # 没有学期字段,统一按下学期估计;推荐里年级只做柔性加权,估偏不致命
    return f"{m}下"


def _annotate(p: Problem, grade: str | None) -> None:
    """检索标注:给题目挂上知识点候选。只从 L1 母版里选,不发明新知识点。"""
    sug = get_index().suggest(p.stem_text, grade=grade, top_k=3)
    if sug:
        p.kp_id = sug[0]["kp_id"]
        p.kp_name = sug[0]["kp_name"]
        p.kp_confidence = sug[0]["confidence"]
        p.kp_source = "suggested"


def _annotate_batch(problems: list[Problem], grade: str | None,
                    use_llm: bool | None = None) -> dict:
    """整份作业一次性标注:先检索出候选,再(可选)让 LLM 在候选里挑。

    `use_llm=None` 表示跟随配置(默认关 —— 实测重排不比检索更准,见 config.py)。
    返回 {"llm": bool, "message": str|None} 供接口如实回报走了哪条路 ——
    降级必须让教师看见,悄悄降级比不降级更糟。
    """
    if use_llm is None:
        use_llm = settings.llm_rerank_kp
    ix = get_index()
    batch = []
    allowed: dict[int, set[str]] = {}
    for i, p in enumerate(problems):
        cands = ix.suggest(p.stem_text, grade=grade, top_k=RERANK_CANDIDATES)
        if cands:                      # 先按检索结果落一份保底标注
            p.kp_id = cands[0]["kp_id"]
            p.kp_name = cands[0]["kp_name"]
            p.kp_confidence = cands[0]["confidence"]
            p.kp_source = "suggested"
            allowed[i] = {c["kp_id"] for c in cands}
            batch.append({"index": i, "stem_text": p.stem_text, "candidates": cands})

    if not (use_llm and llm.is_enabled() and batch):
        return {"llm": False, "message": None if batch else "没有可用的知识点候选"}

    try:
        picks = llm.rerank_kp(batch)
    except llm.LLMUnavailable as exc:
        log.warning("知识点重排降级为检索结果(%d 题): %s", len(batch), exc)
        return {"llm": False, "message": f"知识点重排未生效，已用检索结果：{exc.hint}"}

    for idx, pick in picks.items():
        if not 0 <= idx < len(problems):
            continue
        p = problems[idx]
        kp_id = pick["kp_id"]
        # 第二道闸:llm.rerank_kp 里已按候选集过滤过一遍,这里再校验一次。
        # 单点防守不够 —— 这个 id 会直接进库并汇入图谱统计,而 ix.topics[未知 id]
        # 会把「模型胡说」升级成整次上传 500,恰好违反「不阻断录入」的初衷。
        if kp_id is not None and (kp_id not in allowed.get(idx, ()) or kp_id not in ix.topics):
            log.warning("丢弃候选集外的知识点 %r(第 %d 题)", kp_id, idx + 1)
            continue
        if kp_id:
            p.kp_id = kp_id
            p.kp_name = ix.topics[kp_id]["name"]
            p.kp_confidence = pick["confidence"]
            p.kp_source = "llm"
        else:
            # 模型明确表示候选都不对:清空标注,逼教师手选,而不是留一个错的
            p.kp_id = None
            p.kp_name = None
            p.kp_confidence = None
            p.kp_source = "llm_rejected"
        if pick["difficulty"]:
            p.difficulty = pick["difficulty"]
    return {"llm": True, "message": None}


def _problem_out(p: Problem) -> dict:
    return {
        "id": p.id, "seq_no": p.seq_no, "problem_type": p.problem_type,
        "stem_text": p.stem_text, "options": p.options, "answer": p.answer,
        "difficulty": p.difficulty, "kp_id": p.kp_id, "kp_name": p.kp_name,
        "kp_confidence": float(p.kp_confidence) if p.kp_confidence is not None else None,
        "kp_source": p.kp_source, "review_status": p.review_status,
        "low_confidence": (p.kp_confidence is not None and float(p.kp_confidence) < LOW_CONFIDENCE)
                          or p.kp_id is None,
    }


def _assignment_out(db: Session, a: Assignment) -> dict:
    probs = db.scalars(
        select(Problem).where(Problem.assignment_id == a.id).order_by(Problem.seq_no)
    ).all()
    out = [_problem_out(p) for p in probs]
    return {
        "id": a.id, "class_id": a.class_id, "title": a.title,
        "assign_date": a.assign_date, "review_status": a.review_status,
        "published_at": a.published_at, "source_images": a.source_images or [],
        "problem_count": len(out),
        "unconfirmed_count": sum(1 for p in out if p["review_status"] == "auto"),
        "low_confidence_count": sum(1 for p in out if p["low_confidence"]),
        "problems": out,
    }


def _get_assignment(db: Session, aid: str) -> Assignment:
    a = db.get(Assignment, aid)
    if not a:
        raise HTTPException(404, {"code": "ASSIGNMENT_NOT_FOUND", "message": "作业不存在"})
    return a


def _guard_published(a: Assignment) -> None:
    """已发布的作业可能正在被投票引用,改动会让学生看到的题目与已投的票对不上。"""
    if a.review_status == "published":
        raise HTTPException(409, {
            "code": "ASSIGNMENT_PUBLISHED",
            "message": "作业已发布，如需修改请先撤回",
            "details": {"assignment_id": a.id},
        })


# ---------------------------------------------------------------- 知识点

@router.get("/kg/topics")
def kg_topics(grade: str | None = None, q: str | None = None, limit: int = 400):
    return get_index().list_topics(grade=grade, q=q)[:limit]


@router.post("/kg/suggest")
def kg_suggest(body: SuggestIn):
    """知识点候选。`use_llm=true` 时由 LLM 在检索候选里重排,把选中的顶到第一位。"""
    ix = get_index()
    k = RERANK_CANDIDATES if (body.use_llm and llm.is_enabled()) else 3
    sug = ix.suggest(body.stem_text, grade=body.grade, top_k=k)
    if not (body.use_llm and llm.is_enabled() and sug):
        return sug[:3]
    try:
        pick = llm.rerank_kp([{"index": 0, "stem_text": body.stem_text, "candidates": sug}]).get(0)
    except llm.LLMUnavailable as exc:
        log.warning("suggest 重排降级: %s", exc)
        return sug[:3]
    if not pick or not pick["kp_id"]:
        return sug[:3]
    chosen = next((s for s in sug if s["kp_id"] == pick["kp_id"]), None)
    if not chosen:
        return sug[:3]
    rest = [s for s in sug if s["kp_id"] != pick["kp_id"]][:2]
    return [{**chosen, "confidence": pick["confidence"], "picked_by": "llm",
             "reason": pick["reason"]}, *rest]


@router.get("/llm/health")
def llm_health():
    """确认 LLM 配置是否到位。密钥只回显首尾,不泄露完整值。"""
    return llm.health()


# ---------------------------------------------------------------- 作业

@router.get("/assignments")
def list_assignments(class_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Assignment).where(Assignment.class_id == class_id)
        .order_by(Assignment.created_at.desc())
    ).all()
    return [{
        "id": a.id, "title": a.title, "assign_date": a.assign_date,
        "review_status": a.review_status,
        "problem_count": len(a.problems),
    } for a in rows]


@router.post("/assignments", status_code=201)
def create_assignment(body: AssignmentIn, db: Session = Depends(get_db)):
    if not db.get(Class, body.class_id):
        raise HTTPException(404, {"code": "CLASS_NOT_FOUND", "message": "班级不存在"})
    a = Assignment(
        class_id=body.class_id, title=body.title,
        assign_date=body.assign_date or utcnow().date().isoformat(),
        review_status="draft",
    )
    db.add(a)
    db.commit()
    return _assignment_out(db, a)


@router.get("/assignments/{aid}")
def get_assignment(aid: str, db: Session = Depends(get_db)):
    return _assignment_out(db, _get_assignment(db, aid))


@router.delete("/assignments/{aid}", status_code=204)
def delete_assignment(aid: str, db: Session = Depends(get_db)):
    a = _get_assignment(db, aid)
    _guard_published(a)
    db.delete(a)
    db.commit()


@router.post("/assignments/{aid}/import-text")
def import_text(aid: str, body: ImportTextIn, db: Session = Depends(get_db)):
    """粘贴整段作业文本 → 自动切题 → 自动挂知识点候选(仍需教师逐题确认)。"""
    a = _get_assignment(db, aid)
    _guard_published(a)

    items = split_problems(body.text)
    if not items:
        raise HTTPException(422, {"code": "NOTHING_PARSED", "message": "没能从文本中识别出题目"})

    if body.replace:
        db.query(Problem).filter(Problem.assignment_id == a.id).delete(synchronize_session=False)
        db.flush()
        start = 0
    else:
        start = db.query(Problem).filter(Problem.assignment_id == a.id).count()

    grade = _grade_label(db, a.class_id)
    fresh = []
    for i, it in enumerate(items):
        p = Problem(
            assignment_id=a.id, seq_no=start + i + 1,
            problem_type="choice" if it["options"] else "blank",
            stem_text=it["stem_text"], options=it["options"] or None,
            difficulty=3, review_status="auto",
        )
        fresh.append(p)
        db.add(p)
    note = _annotate_batch(fresh, grade, use_llm=body.use_llm)
    db.commit()
    return {**_assignment_out(db, a), "annotation": note}


@router.post("/assignments/{aid}/images")
async def upload_image(aid: str, file: UploadFile = File(...),
                       recognize: bool = Form(True), replace: bool = Form(False),
                       db: Session = Depends(get_db)):
    """上传作业原图并自动识别题目(FR-HW-04/05)。

    原图**始终保存**,识别只是加速录入 —— 教师需要对照原图校对,
    而识别失败时原图就是唯一的降级依据(NFR-AV-07)。

    多页作业逐张上传,默认 `replace=False` 追加;识别出的题目一律
    `review_status="auto"`,不经教师逐题确认无法发布。
    """
    a = _get_assignment(db, aid)
    _guard_published(a)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_IMAGE:
        raise HTTPException(422, {"code": "UNSUPPORTED_IMAGE",
                                  "message": f"仅支持 {'/'.join(sorted(ALLOWED_IMAGE))}"})
    blob = await file.read()
    if len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, {
            "code": "IMAGE_TOO_LARGE",
            "message": f"图片超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB"})

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
        f.write(blob)
    # 文件名进日志:出问题时能凭 /uploads/<name> 直接找到那张原图复现
    log.info("上传 %s → %s  %.1fMB  作业=%s(%s)  识别=%s 覆盖=%s",
             file.filename, name, len(blob) / 1024 / 1024, a.title, a.id, recognize, replace)

    imgs = list(a.source_images or [])
    imgs.append({"uri": f"/uploads/{name}", "name": file.filename or name})
    a.source_images = imgs

    recog = _recognize_into(db, a, blob, MIME_BY_EXT.get(ext, "image/jpeg"),
                            recognize=recognize, replace=replace)
    db.commit()
    return {"images": imgs, "recognition": recog, **_assignment_out(db, a)}


def _recognize_into(db: Session, a: Assignment, blob: bytes, mime: str,
                    *, recognize: bool, replace: bool) -> dict:
    """跑一次照片识别并落库。任何失败都返回降级说明,而不是抛 5xx。"""
    if not recognize:
        return {"status": "skipped", "message": "已保存原图，未做识别。"}
    if not llm.is_enabled():
        return {"status": "unavailable",
                "message": "未配置识别服务（MATHMATE_LLM_API_KEY）："
                           "原图已保存，请对照原图使用「粘贴文本」或手动录入。"}
    t0 = time.time()
    try:
        items = llm.recognize_problems(blob, mime)
    except llm.LLMUnavailable as exc:
        # 原始细节(网关 JSON、HTTP 码)进日志,界面上给教师能据以行动的一句话
        log.warning("照片识别失败 %.1fs,降级为仅留存原图: %s", time.time() - t0, exc)
        return {"status": "failed", "message": exc.hint, "detail": str(exc)[:300]}
    log.info("照片识别成功 %.1fs,得到 %d 道题", time.time() - t0, len(items))

    if replace:
        db.query(Problem).filter(Problem.assignment_id == a.id).delete(synchronize_session=False)
        db.flush()
        start = 0
    else:
        start = db.query(Problem).filter(Problem.assignment_id == a.id).count()

    fresh = []
    for i, it in enumerate(items):
        p = Problem(
            assignment_id=a.id, seq_no=start + i + 1,
            problem_type=it["problem_type"],
            stem_text=it["stem_text"], options=it["options"] or None,
            difficulty=it["difficulty"], review_status="auto",
        )
        fresh.append(p)
        db.add(p)
    db.flush()
    note = _annotate_batch(fresh, _grade_label(db, a.class_id))
    return {
        "status": "ok",
        "recognized": len(fresh),
        "with_figure": sum(1 for it in items if it["has_figure"]),
        "kp_by_llm": note["llm"],
        "message": (f"识别出 {len(fresh)} 道题，均需逐题校对后才能发布。"
                    + (f"（{note['message']}）" if note["message"] else "")),
    }


@router.post("/assignments/{aid}/problems", status_code=201)
def add_problem(aid: str, body: ProblemIn, db: Session = Depends(get_db)):
    a = _get_assignment(db, aid)
    _guard_published(a)
    nxt = (db.query(Problem).filter(Problem.assignment_id == a.id).count()) + 1
    p = Problem(
        assignment_id=a.id, seq_no=nxt, problem_type=body.problem_type,
        stem_text=body.stem_text, options=body.options, answer=body.answer,
        difficulty=body.difficulty, review_status="auto",
    )
    _annotate(p, _grade_label(db, a.class_id))
    db.add(p)
    db.commit()
    return _problem_out(p)


@router.patch("/problems/{pid}")
def patch_problem(pid: str, body: ProblemPatch, db: Session = Depends(get_db)):
    p = db.get(Problem, pid)
    if not p:
        raise HTTPException(404, {"code": "PROBLEM_NOT_FOUND", "message": "题目不存在"})
    a = _get_assignment(db, p.assignment_id)
    _guard_published(a)

    touched = False
    if body.stem_text is not None and body.stem_text != p.stem_text:
        p.stem_text = body.stem_text
        touched = True
    if body.options is not None:
        p.options = body.options or None
        touched = True
    if body.answer is not None:
        p.answer = body.answer
        touched = True
    if body.difficulty is not None:
        p.difficulty = body.difficulty
        touched = True
    if body.kp_id is not None:
        ix = get_index()
        if body.kp_id not in ix.topics:
            raise HTTPException(422, {"code": "UNKNOWN_KP",
                                      "message": "知识点不在 L1 母版中",
                                      "details": {"kp_id": body.kp_id}})
        p.kp_id = body.kp_id
        p.kp_name = ix.topics[body.kp_id]["name"]
        p.kp_source = "manual"      # 教师手选的标注置信度视为 1.0
        p.kp_confidence = 1.0
        touched = True

    if body.confirmed:
        p.review_status = "confirmed"
    elif touched:
        p.review_status = "edited"   # 教师改过 = 已经看过,视同确认口径的一种
    db.commit()
    return _problem_out(p)


@router.delete("/problems/{pid}", status_code=204)
def delete_problem(pid: str, db: Session = Depends(get_db)):
    p = db.get(Problem, pid)
    if not p:
        raise HTTPException(404, {"code": "PROBLEM_NOT_FOUND", "message": "题目不存在"})
    a = _get_assignment(db, p.assignment_id)
    _guard_published(a)
    aid = a.id
    db.delete(p)
    db.flush()

    # 题号必须连续 —— 它是学生在大屏上的定位锚点。
    # 必须两阶段重排:直接 3→2、4→3 会在 flush 顺序不定时撞上
    # (assignment_id, seq_no) 唯一约束,先整体挪到负值区避开。
    remaining = db.scalars(
        select(Problem).where(Problem.assignment_id == aid).order_by(Problem.seq_no)
    ).all()
    for i, q in enumerate(remaining, start=1):
        q.seq_no = -i
    db.flush()
    for i, q in enumerate(remaining, start=1):
        q.seq_no = i
    db.commit()


@router.post("/assignments/{aid}/publish")
def publish(aid: str, db: Session = Depends(get_db)):
    """发布到大屏。

    未经教师逐题过目的内容不得推给学生 —— 数学题一个符号错误就毁掉整道题,
    这条约束不可妥协(FR-HW-11)。
    """
    a = _get_assignment(db, aid)
    probs = db.scalars(select(Problem).where(Problem.assignment_id == a.id)).all()
    if not probs:
        raise HTTPException(422, {"code": "NO_PROBLEMS", "message": "作业里还没有题目"})

    pending = [p.seq_no for p in probs if p.review_status == "auto"]
    if pending:
        raise HTTPException(409, {
            "code": "UNREVIEWED_PROBLEMS",
            "message": f"还有 {len(pending)} 道题未校对，发布前请逐题确认",
            "details": {"seq_nos": pending},
        })
    missing = [p.seq_no for p in probs if not p.kp_id]
    if missing:
        raise HTTPException(409, {
            "code": "MISSING_KP",
            "message": f"第 {', '.join(map(str, missing))} 题还没有关联知识点",
            "details": {"seq_nos": missing},
        })

    a.review_status = "published"
    a.published_at = datetime.now(timezone.utc)
    db.commit()
    return _assignment_out(db, a)


@router.post("/assignments/{aid}/unpublish")
def unpublish(aid: str, db: Session = Depends(get_db)):
    a = _get_assignment(db, aid)
    open_session = db.scalar(select(VoteSession).where(
        VoteSession.assignment_id == a.id, VoteSession.status == "open"))
    if open_session:
        raise HTTPException(409, {
            "code": "SESSION_IN_PROGRESS",
            "message": "该作业正在投票中，请先关闭投票窗口",
        })
    a.review_status = "draft"
    a.published_at = None
    db.commit()
    return _assignment_out(db, a)
