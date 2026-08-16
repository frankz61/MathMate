# -*- coding: utf-8 -*-
"""LLM 连通性冒烟测试 —— 确认密钥、地址、模型名三者都对。

    python scripts/llm_smoke.py

依次验证:配置读取 → 文本对话 → 视觉识别 → 知识点重排。
视觉那步会现场画一张仿作业图,不需要准备素材。
**会产生真实调用与费用**(几次调用,量很小)。
"""
from __future__ import annotations

import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "apps", "api"))
if sys.platform == "win32":                       # 控制台默认 GBK,直接 print 中文会炸
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import llm                                # noqa: E402
from app.config import settings                    # noqa: E402
from app.kg_index import get_index                 # noqa: E402

OK, BAD = "  ✓", "  ✗"


def step(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 46 - len(title)))


def main() -> int:
    step("配置")
    for k, v in settings.redacted().items():
        print(f"  {k:<14} {v}")
    if not settings.llm_enabled:
        print(f"\n{BAD} LLM 未启用。请在仓库根目录建 .env（模板 .env.example）"
              f"并填 MATHMATE_LLM_API_KEY。")
        return 1

    step("文本对话")
    t0 = time.time()
    try:
        reply = llm.chat([{"role": "user", "content": "只回复两个字：可用"}], max_tokens=16)
    except llm.LLMUnavailable as exc:
        print(f"{BAD} {exc}")
        return 1
    print(f"{OK} {reply!r}  ({time.time() - t0:.1f}s)")

    step("视觉识别（现场生成一张仿作业图）")
    try:
        blob = _sample_image()
    except ImportError:
        print("  (未装 Pillow，跳过；pip install pillow 后可测)")
        blob = None
    if blob:
        t0 = time.time()
        try:
            items = llm.recognize_problems(blob, "image/png")
        except llm.LLMUnavailable as exc:
            print(f"{BAD} {exc}")
            return 1
        print(f"{OK} 识别出 {len(items)} 道题  ({time.time() - t0:.1f}s)")
        for it in items:
            fig = " [含图]" if it["has_figure"] else ""
            print(f"     {it['seq_no']}. [{it['problem_type']}] 难度{it['difficulty']}{fig} "
                  f"{it['stem_text'][:42]}")
            if it["options"]:
                print("        " + "  ".join(f"{k}.{v[:14]}" for k, v in it["options"].items()))

    step("知识点重排（候选来自检索，LLM 只负责挑）")
    ix = get_index()
    stem = "下列各式中，是分式的是（  ） A. $$\\frac{x}{2}$$ B. $$\\frac{2}{x}$$ C. $$x+1$$ D. $$\\sqrt{3}$$"
    cands = ix.suggest(stem, grade="八下", top_k=8)
    print("  检索候选:")
    for c in cands[:5]:
        print(f"     {c['confidence']:.2f} [{c['grade']}] {c['chapter']} · {c['kp_name']}")
    t0 = time.time()
    try:
        pick = llm.rerank_kp([{"index": 0, "stem_text": stem, "candidates": cands}]).get(0)
    except llm.LLMUnavailable as exc:
        print(f"{BAD} {exc}")
        return 1
    if not pick:
        print(f"{BAD} 重排返回空（可能被候选集过滤器拦下）")
        return 1
    name = ix.topics[pick["kp_id"]]["name"] if pick["kp_id"] else "（模型认为候选都不对）"
    print(f"{OK} 选中 {name}  把握 {pick['confidence']:.2f}  理由「{pick['reason']}」"
          f"  ({time.time() - t0:.1f}s)")

    print("\n全部通过。教师控制台「拍照录入」与「AI 重新推荐」现在可用。")
    return 0


def _sample_image() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 470), (250, 249, 245))
    d = ImageDraw.Draw(img)

    def font(sz: int):
        for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc",
                  "/System/Library/Fonts/PingFang.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(p, sz)
            except OSError:
                continue
        return ImageFont.load_default()

    d.text((40, 26), "八年级数学  每日作业（10月22日）", fill=(20, 20, 20), font=font(22))
    y = 80
    for ln in [
        "1. 下列各式中，是分式的是（   ）",
        "    A. x/2        B. 2/x        C. x+1        D. √3",
        "",
        "2. 已知一次函数 y = 2x - 3，它的图象不经过第（   ）象限",
        "    A. 一        B. 二        C. 三        D. 四",
        "",
        "3. 解不等式组：x + 1 > 0 且 2x - 4 < 0，并把解集在数轴上表示出来。",
        "",
        "4. 如图，在△ABC 中，AB = AC，∠A = 40°，求 ∠B 的度数。",
    ]:
        d.text((40, y), ln, fill=(30, 30, 34), font=font(18))
        y += 34
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


if __name__ == "__main__":
    raise SystemExit(main())
