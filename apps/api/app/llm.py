# -*- coding: utf-8 -*-
"""多模态 LLM 客户端(OpenAI 兼容 /chat/completions)。

承接 DESIGN §5 里此前空缺的两步:
  · 作业照片版面分析与题目切分(FR-HW-04/05)—— `recognize_problems`
  · 知识点关联的重排(FR-HW-07)—— `rerank_kp`

两条**不可妥协的边界**:

1. **知识点只能从检索给出的候选里选。** LLM 负责在候选中挑,不负责发明。
   `rerank_kp` 会把不在候选集里的返回值直接丢弃并退回检索的 top-1 ——
   否则模型编一个「二次函数的对称轴性质」这种听起来很对但不在 L1 母版里的
   知识点,整条图谱统计就废了(DESIGN §5)。
2. **任何失败都降级,不阻断主流程。** 网络超时、限流、模型胡说,一律抛
   `LLMUnavailable` 由调用方接住,退回 NFR-AV-07 规定的既有路径。
   LLM 是加速器,不是必需依赖。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx

from .config import settings

try:
    from PIL import Image, ImageOps
except ImportError:                       # pragma: no cover - 缺 Pillow 时的降级
    Image = ImageOps = None

log = logging.getLogger("mathmate.llm")

# 网关对单张图片有 1 MiB 硬限制(超出直接 400 "Image input is too large")。
# 留出余量,并且**必须在发送前压**:手机拍的作业照普遍 5–10 MB。
IMAGE_BUDGET = 900 * 1024
# 实测 2400px 比 1600px 更快也更准(28s vs 48s,且长题干不被截断);
# 再往上只是徒增体积,识别质量不再提升。
MAX_EDGE = 2400


class LLMUnavailable(RuntimeError):
    """LLM 不可用或返回无法使用的内容 —— 调用方必须降级,不得向上冒泡成 500。"""

    def __init__(self, message: str, *, hint: str | None = None, status: int | None = None):
        super().__init__(message)
        # hint 是给教师看的人话;message 是给日志看的原始细节。
        # 把网关返回的 JSON 直接甩到界面上,教师除了困惑什么也得不到。
        self.hint = hint or message
        self.status = status


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(settings.llm_timeout, connect=15.0))
    return _client


def is_enabled() -> bool:
    return settings.llm_enabled


# ---------------------------------------------------------------- 底层调用

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.S)


def _content_of(resp: httpx.Response) -> str:
    """取出 assistant 文本。

    该网关在未显式指定 stream 时会返回 SSE;我们固定传 stream=false,
    但保留 SSE 解析分支 —— 网关行为不由我们控制,少一个崩溃点。
    """
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        parts = []
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                delta = json.loads(payload)["choices"][0].get("delta", {})
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            parts.append(delta.get("content") or "")
        return "".join(parts)

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable(f"返回结构无法解析: {resp.text[:200]}") from exc


def _hint_for(status: int | None, body: str) -> str:
    """把网关的原始报错翻成教师能据以行动的一句话。

    界面上甩一坨 JSON,教师除了困惑什么也得不到 —— 原始细节留在日志里。
    """
    if status is None:
        return "识别服务连不上（网络或超时），原图已保存，可稍后重试或改用粘贴文本。"

    low = body.lower()
    # 先看响应体再看状态码:同一个 400 可能是"图太大"也可能是"模型名不对",
    # 而这两者一个该改照片、一个该改配置。只按状态码给提示会把配置错误
    # 说成"稍后重试",而重试一万次也不会好。
    if "too large" in low or "exceeds" in low:
        return "图片超出识别服务的体积上限。系统已自动压缩仍未通过，请把照片裁到单页再传。"
    if "model" in low and status in (400, 404, 422):
        return "模型名或服务地址不对，请检查 MATHMATE_LLM_MODEL / MATHMATE_LLM_BASE_URL。"

    known = {
        400: "识别服务拒绝了这次请求（参数不合法），请查看服务端日志。重试不会有帮助。",
        401: "识别服务鉴权失败，请检查 MATHMATE_LLM_API_KEY。",
        403: "识别服务拒绝访问，请检查密钥权限。",
        404: "识别服务地址或模型名不对，请检查 MATHMATE_LLM_BASE_URL / MODEL。",
        413: "图片太大，请把照片裁到单页再传。",
        429: "识别服务繁忙（限流），请稍后重试。",
    }
    if status in known:
        return known[status]
    if 400 <= status < 500:
        return f"识别服务拒绝了这次请求（HTTP {status}），重试不会有帮助，请查看服务端日志。"
    return f"识别服务暂时不可用（HTTP {status}），原图已保存，可稍后重试。"


def chat(messages: list[dict], *, model: str | None = None,
         max_tokens: int = 2048, temperature: float = 0.0) -> str:
    """一次同步对话。失败抛 LLMUnavailable。"""
    if not settings.llm_enabled:
        raise LLMUnavailable("未配置 LLM(缺少 MATHMATE_LLM_API_KEY 或已显式关闭)",
                             hint="未配置识别服务，请填写 MATHMATE_LLM_API_KEY。")

    body = {
        "model": model or settings.llm_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.llm_base_url}/chat/completions"

    rid = uuid.uuid4().hex[:8]
    payload = len(json.dumps(body, ensure_ascii=False).encode())
    log.info("[%s] → %s model=%s 消息%d条 载荷%.1fKB max_tokens=%d",
             rid, url, body["model"], len(messages), payload / 1024, max_tokens)

    last, last_status, last_body = "", None, ""
    for attempt in range(settings.llm_max_retries + 1):
        t0 = time.time()
        try:
            resp = _http().post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
            last_status, last_body = None, ""
            log.warning("[%s] 第%d次 网络失败 %.1fs %s", rid, attempt + 1,
                        time.time() - t0, last)
        else:
            dt = time.time() - t0
            if resp.status_code == 200:
                text = _content_of(resp).strip()
                if text:
                    usage = ""
                    try:
                        u = resp.json().get("usage") or {}
                        usage = f" tokens={u.get('prompt_tokens')}→{u.get('completion_tokens')}"
                    except Exception:
                        pass
                    log.info("[%s] ← 200 %.1fs 返回%d字%s", rid, dt, len(text), usage)
                    return text
                last, last_status, last_body = "返回内容为空", 200, ""
                log.warning("[%s] ← 200 但内容为空 %.1fs", rid, dt)
            elif resp.status_code in (408, 409, 425, 429, 500, 502, 503, 504):
                last, last_status, last_body = f"HTTP {resp.status_code}", resp.status_code, resp.text
                log.warning("[%s] ← %d %.1fs 可重试 %s", rid, resp.status_code, dt,
                            resp.text[:200])
            else:
                # 4xx 配置类错误(401 密钥错、404 模型名错、400 图太大)重试没有意义
                log.error("[%s] ← %d %.1fs 不可重试 %s", rid, resp.status_code, dt,
                          resp.text[:400])
                raise LLMUnavailable(f"HTTP {resp.status_code}: {resp.text[:200]}",
                                     hint=_hint_for(resp.status_code, resp.text),
                                     status=resp.status_code)
        if attempt < settings.llm_max_retries:
            time.sleep(1.5 * (attempt + 1))
    log.error("[%s] 重试%d次后放弃: %s", rid, settings.llm_max_retries + 1, last)
    raise LLMUnavailable(last or "调用失败",
                         hint=_hint_for(last_status, last_body), status=last_status)


def chat_json(messages: list[dict], *, model: str | None = None,
              max_tokens: int = 2048) -> Any:
    """要求模型返回 JSON,容忍 ```json 围栏与前后废话。"""
    raw = chat(messages, model=model, max_tokens=max_tokens)
    text = _FENCE.sub("", raw).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退一步:截取第一个 [ 或 { 到最后一个配对括号
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = text.find(opener), text.rfind(closer)
        if 0 <= i < j:
            try:
                return json.loads(text[i: j + 1])
            except json.JSONDecodeError:
                continue
    raise LLMUnavailable(f"返回的不是合法 JSON: {raw[:200]}")


# ---------------------------------------------------------------- 照片识别

_RECOGNIZE_PROMPT = """你是初中数学作业录入助手。图片是一页学生作业/试卷的照片。

逐题识别，输出 JSON 数组，每个元素：
{
  "seq_no": 题号(整数，按图片中原有题号；无题号则按出现顺序从 1 编),
  "problem_type": "choice" | "blank" | "solve",
  "stem_text": "题干文本",
  "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
  "difficulty": 1-5 的整数,
  "has_figure": true/false
}

要求：
- 数学表达式一律用 LaTeX，行内用 $$...$$ 包裹，例如 $$\\frac{2}{x}$$、$$\\angle ABC$$、$$x^2-6x+5=0$$。
- 非选择题的 options 用 {}。
- **只识别题目本身**，不要包含学生的手写答案、批改痕迹、页眉页脚、分数。
- 图片模糊、缺角或看不清的字，用 ⬚ 占位，不要猜测填补。
- 若图中有几何图形，has_figure 为 true，并在题干里保留「如图」字样。
- 只输出 JSON 数组，不要任何解释文字。"""


def prepare_image(blob: bytes, mime: str = "image/jpeg") -> tuple[bytes, str, dict]:
    """把上传的原图整理成模型能收的样子。返回 (字节, mime, 诊断信息)。

    做两件事,缺一不可:

    1. **压到网关限额内。** 网关单图上限 1 MiB,而手机拍的作业照普遍 5–10 MB,
       直接发过去是稳定的 400。原图仍按原样留存供教师对照 —— 压缩只针对送给
       模型的那一份。
    2. **套用 EXIF 方向。** 手机横拍竖拍靠 EXIF 标记,原始像素常常是躺着的。
       不套的话教师在浏览器里看到的是正的、模型看到的是歪的,识别质量白白打折。
    """
    info: dict[str, Any] = {"src_bytes": len(blob)}
    if Image is None:                     # 没装 Pillow:只能原样发,并说清后果
        info["prepared"] = False
        info["reason"] = "未安装 Pillow"
        return blob, mime, info

    try:
        im = Image.open(io.BytesIO(blob))
        info["src_size"] = f"{im.width}×{im.height}"
        info["exif_orientation"] = im.getexif().get(274)
        im = ImageOps.exif_transpose(im) or im
        im = im.convert("RGB")
    except Exception as exc:              # 不是图片/损坏:交给模型自己判断
        info["prepared"] = False
        info["reason"] = f"{type(exc).__name__}: {exc}"
        return blob, mime, info

    edge = MAX_EDGE
    quality = 85
    out = blob
    for _ in range(6):
        work = im.copy()
        if max(work.size) > edge:
            work.thumbnail((edge, edge), Image.LANCZOS)
        buf = io.BytesIO()
        work.save(buf, "JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        info["out_size"] = f"{work.width}×{work.height}"
        if len(out) <= IMAGE_BUDGET:
            break
        # 先降边长再降画质:作业题的关键是笔画清晰,分辨率比压缩率更值钱
        if edge > 1400:
            edge = int(edge * 0.8)
        else:
            quality -= 12
        if quality < 45:
            break

    info["prepared"] = True
    info["out_bytes"] = len(out)
    info["quality"] = quality
    return out, "image/jpeg", info


def recognize_problems(image_bytes: bytes, mime: str = "image/jpeg",
                       max_items: int = 30) -> list[dict]:
    """从作业照片识别题目。返回 [{seq_no, problem_type, stem_text, options, difficulty, has_figure}]。

    识别结果一律以 `review_status="auto"` 落库,必须经教师逐题校对才能发布
    (FR-HW-11)—— 数学题一个符号错就毁掉整道题,模型识别得再好也不例外。
    """
    image_bytes, mime, prep = prepare_image(image_bytes, mime)
    log.info("识别预处理 %s", " ".join(f"{k}={v}" for k, v in prep.items()))

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data = chat_json([{
        "role": "user",
        "content": [
            {"type": "text", "text": _RECOGNIZE_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }], model=settings.llm_vision_model, max_tokens=6000)

    if isinstance(data, dict):
        # 有时会包一层 {"problems": [...]}
        for key in ("problems", "items", "data", "questions"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise LLMUnavailable("识别结果不是数组")

    out: list[dict] = []
    for it in data[:max_items]:
        if not isinstance(it, dict):
            continue
        stem = str(it.get("stem_text") or "").strip()
        if not stem:
            continue
        opts = it.get("options")
        opts = {str(k).strip().upper(): str(v).strip()
                for k, v in opts.items() if str(v).strip()} if isinstance(opts, dict) else {}
        ptype = str(it.get("problem_type") or "").strip()
        if ptype not in {"choice", "blank", "solve"}:
            ptype = "choice" if opts else "blank"
        try:
            diff = int(it.get("difficulty") or 3)
        except (TypeError, ValueError):
            diff = 3
        out.append({
            # 题号按**留下来的**题重排:丢掉一道空题不能在题号上留个洞,
            # 题号是学生在大屏上的定位锚点,必须连续。
            "seq_no": len(out) + 1,
            "problem_type": ptype,
            "stem_text": stem,
            "options": opts,
            "difficulty": min(5, max(1, diff)),
            "has_figure": bool(it.get("has_figure")),
        })
    if not out:
        raise LLMUnavailable("没能从图片中识别出题目")
    return out


# ---------------------------------------------------------------- 知识点重排

_RERANK_SYSTEM = """你是初中数学教研员，负责给作业题标注知识点。

规则（违反任何一条都算错）：
1. **只能从给定候选里选**，用候选的 kp_id 原样返回。绝对不要发明新的知识点，
   也不要返回候选之外的 id。
2. 若所有候选都明显不对，返回 kp_id 为 null，并说明理由。宁可不标，不要硬标。
3. confidence 是你的把握程度（0-1 的小数），不是候选原本的检索分。
4. difficulty 按初中生实际作答难度给 1-5。

输出 JSON 数组，每题一个元素：
{"index": 题目序号, "kp_id": "选中的候选 id 或 null", "confidence": 0.0-1.0,
 "difficulty": 1-5, "reason": "不超过 20 字"}
只输出 JSON。"""


def rerank_kp(items: list[dict]) -> dict[int, dict]:
    """批量重排知识点候选。

    `items`: [{"index": int, "stem_text": str, "candidates": [{kp_id, kp_name, grade, chapter}]}]
    返回 {index: {"kp_id", "confidence", "difficulty", "reason"}},只含**候选集内**的选择。

    一次调用处理整份作业,而不是每题一次 —— 教师点「识别」时等 10 次串行往返
    是不可接受的交互。
    """
    payload = []
    allowed: dict[int, set[str]] = {}
    for it in items:
        cands = it.get("candidates") or []
        if not cands:
            continue
        allowed[it["index"]] = {c["kp_id"] for c in cands}
        payload.append({
            "index": it["index"],
            "stem": it["stem_text"][:600],
            "candidates": [{
                "kp_id": c["kp_id"],
                "name": c["kp_name"],
                "grade": c.get("grade"),
                "chapter": c.get("chapter"),
            } for c in cands],
        })
    if not payload:
        return {}

    data = chat_json([
        {"role": "system", "content": _RERANK_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], max_tokens=400 + 260 * len(payload))

    if isinstance(data, dict):
        for key in ("results", "items", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise LLMUnavailable("重排结果不是数组")

    out: dict[int, dict] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        kp_id = row.get("kp_id")
        # 越界的 kp_id 一律丢弃 —— 这是「不发明知识点」这条约束的执行点
        if kp_id is not None and kp_id not in allowed.get(idx, ()):
            continue
        try:
            conf = float(row.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        try:
            diff = int(row.get("difficulty") or 0)
        except (TypeError, ValueError):
            diff = 0
        out[idx] = {
            "kp_id": kp_id,
            "confidence": min(1.0, max(0.0, conf)),
            "difficulty": diff if 1 <= diff <= 5 else None,
            "reason": str(row.get("reason") or "")[:60],
        }
    return out


def health() -> dict:
    """探活:确认密钥、地址、模型名三者可用。给 /api/v1/llm/health 用。"""
    info = settings.redacted()
    if not settings.llm_enabled:
        info["reachable"] = False
        info["message"] = "未配置 LLM，走 NFR-AV-07 降级路径"
        return info
    t0 = time.time()
    try:
        reply = chat([{"role": "user", "content": "只回复两个字：可用"}], max_tokens=16)
        info["reachable"] = True
        info["latency_ms"] = int((time.time() - t0) * 1000)
        info["sample"] = reply[:40]
    except LLMUnavailable as exc:
        info["reachable"] = False
        info["message"] = str(exc)[:300]
    return info
