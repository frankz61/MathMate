# -*- coding: utf-8 -*-
"""把整段作业文本切成一道道题(题号切分)。

对应 DESIGN §5 步骤 4「版面分析与题目切分」的**文本降级路径**:
NFR-AV-07 规定 LLM 不可用时降级为「仅切题 + 教师手动录入」,不阻断主流程。
照片路径需要多模态模型,当前环境未配置,故只实现本路径并保留原图供对照。
"""
from __future__ import annotations

import re

# 题号:行首的 1. / 1、/ 1．/ (1) / 1) / 第1题
_NUM = re.compile(r"^\s*(?:第\s*)?(\d{1,2})\s*(?:[.、．)）]|题)\s*")
_PAREN = re.compile(r"^\s*[(（]\s*(\d{1,2})\s*[)）]\s*")
# 选项:A. A、 A．A) 允许同一行内连续出现
_OPT = re.compile(r"(?:^|\s)([A-EＡ-Ｅ])\s*[.、．)）]\s*")

_FULL2HALF = {ord(c): ord(a) for c, a in zip("ＡＢＣＤＥ", "ABCDE")}


def _split_options(block: str) -> tuple[str, dict]:
    """从题块里分离题干与选项。找不到选项就整体当作题干(填空/解答题)。"""
    marks = list(_OPT.finditer(block))
    # 至少要有 A、B 两个选项才认定是选择题,避免把句中的字母误判
    letters = [m.group(1).translate(_FULL2HALF) for m in marks]
    if len(marks) < 2 or letters[0] != "A":
        return block.strip(), {}

    stem = block[: marks[0].start()].strip()
    opts: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(block)
        opts[m.group(1).translate(_FULL2HALF)] = block[m.end(): end].strip()
    opts = {k: v for k, v in opts.items() if v}
    return (stem or block.strip()), opts


def split_problems(text: str, max_items: int = 50) -> list[dict]:
    """返回 [{seq_no, stem_text, options}],题号按出现顺序重排为 1..n。"""
    if not text or not text.strip():
        return []

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[list[str]] = []
    cur: list[str] | None = None

    for line in lines:
        if not line.strip():
            if cur is not None:
                cur.append("")
            continue
        m = _NUM.match(line) or _PAREN.match(line)
        if m:
            if cur is not None:
                blocks.append(cur)
            cur = [line[m.end():]]
        elif cur is not None:
            cur.append(line)
        else:
            # 题号之前的抬头(如「八年级数学作业」)直接丢弃
            continue

    if cur is not None:
        blocks.append(cur)

    # 一个题号都没识别到:整段当作一道题,好过什么都不给
    if not blocks:
        stem, opts = _split_options(text)
        return [{"seq_no": 1, "stem_text": stem, "options": opts}]

    out = []
    for i, b in enumerate(blocks[:max_items], start=1):
        block = "\n".join(b).strip()
        if not block:
            continue
        stem, opts = _split_options(block)
        stem = re.sub(r"\n{2,}", "\n", stem).strip()
        if not stem:
            continue
        out.append({"seq_no": len(out) + 1, "stem_text": stem, "options": opts})
    return out
