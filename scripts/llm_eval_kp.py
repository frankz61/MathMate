# -*- coding: utf-8 -*-
"""知识点标注:检索基线 vs 检索 + LLM 重排。

    python scripts/llm_eval_kp.py --n 60

在 288 道**已标注真题**上做留一法抽样评测(把题目自己从检索语料里排除,
否则等于抄答案)。两条路走的是与线上完全相同的代码路径。

**会真实调用 LLM 并产生费用**,所以默认只抽样。样本小 → 置信区间宽,
输出里会一并给出 95% 区间,不要拿 60 题的结果当准确率定论。
"""
from __future__ import annotations

import argparse
import io
import math
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "apps", "api"))
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import llm                                # noqa: E402
from app.config import settings                    # noqa: E402
from app.kg_index import get_index                 # noqa: E402

CANDIDATES = 8          # 与 authoring.RERANK_CANDIDATES 保持一致
BATCH = 8               # 每次 LLM 调用带几道题(线上按整份作业批处理)


def wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% 区间 —— 小样本下比 ±1.96·√(p(1-p)/n) 诚实得多。"""
    if not n:
        return 0.0, 0.0
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - m) / d), min(1.0, (c + m) / d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="抽样题数(0 = 全部 288 题)")
    ap.add_argument("--seed", type=int, default=20260815)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    if not settings.llm_enabled:
        print("LLM 未启用（缺 MATHMATE_LLM_API_KEY）。只跑检索基线请用："
              "\n  cd apps/api && python -m app.kg_index --eval")
        return 1

    ix = get_index()
    idxs = list(range(len(ix.corpus)))
    random.Random(args.seed).shuffle(idxs)
    if args.n:
        idxs = idxs[: args.n]
    print(f"抽样 {len(idxs)} / {len(ix.corpus)} 题   模型 {settings.llm_model}   "
          f"候选 top-{CANDIDATES}")

    # ── 检索:候选与金标 ──
    tasks = []
    for i in idxs:
        gold_tid = ix.corpus[i][0]
        golds = ix.gold_by_qid.get(ix._corpus_qid[i], {gold_tid})
        cands = ix.suggest(ix._corpus_meta[i], grade=ix.topics[gold_tid]["grade"],
                           top_k=CANDIDATES, exclude_corpus_idx=i)
        if cands:
            tasks.append({"i": i, "stem": ix._corpus_meta[i], "golds": golds,
                          "gold_chapters": {ix.topics[g]["chapter"] for g in golds
                                            if g in ix.topics},
                          "cands": cands})

    # ── LLM:分批重排 ──
    batches = [tasks[k: k + BATCH] for k in range(0, len(tasks), BATCH)]
    done = [0]
    t0 = time.time()

    def run(batch: list[dict]) -> dict:
        payload = [{"index": j, "stem_text": t["stem"], "candidates": t["cands"]}
                   for j, t in enumerate(batch)]
        try:
            out = llm.rerank_kp(payload)
        except llm.LLMUnavailable as exc:
            print(f"  (一批降级: {exc})")
            out = {}
        done[0] += len(batch)
        print(f"\r  已处理 {done[0]}/{len(tasks)} …", end="", flush=True)
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, batches))
    print(f"\r  完成 {len(tasks)} 题，用时 {time.time() - t0:.0f}s" + " " * 12)

    # ── 计分 ──
    base1 = base3 = base_ch1 = oracle8 = 0
    llm1 = llm_ch1 = llm_abstain = llm_fallback = 0
    ans_n = ans_llm = ans_base = 0       # 只在「模型愿意作答」的子集上对比
    abst_base = 0                        # 模型弃权的那些题,检索自己对不对
    flips_good: list[tuple[str, str, str]] = []
    flips_bad: list[tuple[str, str, str]] = []

    for batch, picks in zip(batches, results):
        for j, t in enumerate(batch):
            ids = [c["kp_id"] for c in t["cands"]]
            chs = [ix.topics[c]["chapter"] for c in ids if c in ix.topics]
            b_ok = ids[0] in t["golds"]
            base1 += b_ok
            base3 += bool(t["golds"] & set(ids[:3]))
            base_ch1 += bool(chs and chs[0] in t["gold_chapters"])
            # 天花板:金标压根不在候选里时,再强的重排也救不回来
            oracle8 += bool(t["golds"] & set(ids))

            pick = picks.get(j)
            if pick is None:
                llm_fallback += 1              # 这批降级了 → 线上会退回检索结果
                chosen = ids[0]
            elif pick["kp_id"] is None:
                llm_abstain += 1               # 模型弃权 → 线上清空标注让教师手选
                abst_base += b_ok
                continue
            else:
                chosen = pick["kp_id"]

            l_ok = chosen in t["golds"]
            llm1 += l_ok
            ans_n += 1
            ans_llm += l_ok
            ans_base += b_ok
            llm_ch1 += ix.topics[chosen]["chapter"] in t["gold_chapters"] \
                if chosen in ix.topics else 0
            if l_ok and not b_ok:
                flips_good.append((t["stem"][:34], ix.topics[ids[0]]["name"],
                                   ix.topics[chosen]["name"]))
            elif b_ok and not l_ok:
                flips_bad.append((t["stem"][:34], ix.topics[ids[0]]["name"],
                                  ix.topics[chosen]["name"]))

    n = len(tasks)
    def row(label: str, k: int, d: int = 0) -> str:
        d = d or n
        lo, hi = wilson(k, d)
        return f"  {label:<24} {k / d:>6.1%}   [{lo:.1%}, {hi:.1%}]   {k}/{d}"

    print(f"\n── 全体 {n} 题（Wilson 95% 区间）──")
    print(row("检索 top-1", base1))
    print(row("检索 top-3", base3))
    print(row("检索 章节 top-1", base_ch1))
    print(row("检索+LLM top-1", llm1))
    print(row("检索+LLM 章节 top-1", llm_ch1))
    print(row("〔天花板〕金标在候选内", oracle8))

    print(f"\n── 只看模型愿意作答的 {ans_n} 题 ──")
    print("  (弃权算「未作答」而非「答错」:线上会清空标注并标黄逼教师手选，")
    print("   这与「自信地给出一个错标注」是完全不同性质的失败)")
    print(row("同一子集 · 检索 top-1", ans_base, ans_n))
    print(row("同一子集 · LLM top-1", ans_llm, ans_n))

    print(f"\n  模型弃权 {llm_abstain} 题（占 {llm_abstain / n:.0%}）"
          f"，其中检索原本能答对的有 {abst_base} 题 —— 这是弃权的真实代价")
    print(f"  调用降级（退回检索 top-1）      {llm_fallback}")
    print(f"  纠正了检索的错   +{len(flips_good)}     带偏了检索的对   -{len(flips_bad)}")

    for title, flips in (("纠正示例", flips_good[:5]), ("带偏示例", flips_bad[:5])):
        if flips:
            print(f"\n  【{title}】")
            for stem, was, now in flips:
                print(f"    {stem}\n      检索 {was}  →  LLM {now}")

    print("\n注：命中按标签集合判定（同一道题可能合法归属多个知识点）。"
          f"\n    n={n} 的抽样区间很宽，别把点估计当准确率定论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
