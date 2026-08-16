# -*- coding: utf-8 -*-
"""知识点自动标注 —— 不依赖 LLM 的实现。

思路(对应 DESIGN §5 步骤 9「从清单中选,不自由生成」):

  1. 术语通道:题干与知识点画像(名称/描述/所属章节)的字符二元组相似度
  2. 近邻通道:题干与 778 道**已标注知识点**的真题做相似度检索,
     用邻居的标签投票 —— 这条通道贡献了绝大部分准确率

两路加权合并,只在 L1 母版的既有知识点里做选择,永不"发明"新知识点。
准确率可用 `python -m app.kg_index --eval` 留一法评测。
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from functools import lru_cache

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA = os.path.join(REPO, "data")

GRADES = ["七上", "七下", "八上", "八下", "九上", "九下"]

_MATH_BLOCK = re.compile(r"\$\$?.*?\$\$?", re.S)
_TEX_CMD = re.compile(r"\\[a-zA-Z]+")
_KEEP = re.compile(r"[一-鿿0-9a-zA-Z]+")

# 公式结构能帮助判断「两道题像不像」,但**不能**当成考点的直接证据:
# 一道题里有分数线不代表它考「分式」(分式要求分母含字母),而几乎每道数学题都有分数线。
# 早期版本把 \frac 映射成中文词「分式」,结果这些伪词与真实知识点名撞车,
# 「下列各式中是分式的是」反被一堆含 \frac 的实数题带偏。
# 因此统一加 zz 前缀隔离命名空间:它们只参与题目↔题目的相似度,永远不会冒充知识点名称。
_FEATURES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\\[dc]?frac"),                     "zzfrac"),
    (re.compile(r"\\sqrt"),                          "zzsqrt"),
    (re.compile(r"\^\s*\{?\s*2\s*\}?"),              "zzsquare"),
    (re.compile(r"\^\s*\{?\s*3\s*\}?"),              "zzcube"),
    (re.compile(r"\\angle|∠"),                       "zzangle"),
    (re.compile(r"\\triangle|△"),                    "zztriangle"),
    (re.compile(r"\\odot|⊙"),                        "zzcircle"),
    (re.compile(r"\\parallel|∥"),                    "zzparallel"),
    (re.compile(r"\\perp|⊥"),                        "zzperp"),
    (re.compile(r"\\(?:sin|cos|tan)\b"),             "zztrig"),
    (re.compile(r"\\pi|π"),                          "zzpi"),
    (re.compile(r"\\(?:le|ge|leq|geq|neq)\b|[<>≤≥＜＞]"), "zzineq"),
    (re.compile(r"\\%"),                             "zzpercent"),
    (re.compile(r"\\left\||\|\s*[a-zA-Z]"),          "zzabs"),
    (re.compile(r"\by\s*="),                         "zzfunc"),
    (re.compile(r"\bf\s*\("),                        "zzfunc"),
    (re.compile(r"\(\s*[a-zA-Z0-9\-]+\s*,\s*[a-zA-Z0-9\-]+\s*\)"), "zzcoord"),
    (re.compile(r"\\overline|\\bar"),                "zzseg"),
    (re.compile(r"\\circ"),                          "zzdeg"),
    (re.compile(r"\\cong|≌"),                        "zzcong"),
    (re.compile(r"\\sim(?![a-zA-Z])|∽"),             "zzsimilar"),
]


def math_features(raw: str) -> str:
    """从原始 LaTeX 中抽取结构特征,输出可参与匹配的中文伪词。"""
    hits = [word for pat, word in _FEATURES if pat.search(raw or "")]
    return " ".join(hits)


# 知识点名称里的「词根」:「分式的概念与基本性质」→「分式」、「一次函数的图象与性质」→「一次函数」。
# 词根在题干里原样出现,是判断考点最强的单条证据 —— 字符二元组相似度会把这种
# 精确命中稀释掉,所以单开一路。
_NAME_SPLIT = re.compile(r"[的与及和、，,（(]")
_LEAD_VERB = re.compile(r"^(?:认识|理解|探索|了解|感受|体会|初步认识)")


def anchor_of(name: str) -> str | None:
    n = _LEAD_VERB.sub("", name or "")
    parts = [p for p in _NAME_SPLIT.split(n) if len(p) >= 2]
    if parts:
        return parts[0]
    return n if len(n) >= 2 else None


def normalize(text: str) -> str:
    """保留自然语言部分,并把公式结构翻译成伪词一并纳入匹配。"""
    raw = text or ""
    feats = math_features(raw)
    t = _MATH_BLOCK.sub(" ", raw)
    t = _TEX_CMD.sub(" ", t)
    body = " ".join(_KEEP.findall(t))
    return (body + " " + feats).strip()


def bigrams(text: str) -> Counter:
    """中文没有空格分词,又不想引入 jieba 依赖 —— 字符二元组在术语匹配上够用。"""
    out: Counter = Counter()
    for tok in text.split():
        if re.fullmatch(r"[0-9a-zA-Z]+", tok):
            out[tok] += 1
            continue
        if len(tok) == 1:
            out[tok] += 1
        for i in range(len(tok) - 1):
            out[tok[i:i + 2]] += 1
    return out


def _cos(a: dict, b: dict, idf: dict) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    num = 0.0
    for k, v in small.items():
        if k in large:
            w = idf.get(k, 1.0)
            num += v * large[k] * w * w
    if num <= 0:
        return 0.0
    na = math.sqrt(sum((v * idf.get(k, 1.0)) ** 2 for k, v in a.items()))
    nb = math.sqrt(sum((v * idf.get(k, 1.0)) ** 2 for k, v in b.items()))
    return num / (na * nb) if na and nb else 0.0


class KgIndex:
    def __init__(self, kg_path: str | None = None, practice_path: str | None = None):
        kg_path = kg_path or os.path.join(DATA, "junior_math_kg.json")
        practice_path = practice_path or os.path.join(DATA, "practice_recommend.json")

        with open(kg_path, encoding="utf-8") as f:
            kg = json.load(f)
        by_id = {n["id"]: n for n in kg["nodes"]}

        self.topics: dict[str, dict] = {}
        for n in kg["nodes"]:
            if n["level"] != "topic":
                continue
            chapter = by_id.get(n["parent"], {})
            domain = by_id.get(chapter.get("parent_domain"), {})
            self.topics[n["id"]] = {
                "id": n["id"], "name": n["name"], "desc": n.get("desc", ""),
                "grade": n.get("grade"), "chapter": chapter.get("name", ""),
                "domain": domain.get("name", ""),
                "exam_weight": n.get("exam_weight", "medium"),
            }

        # 知识点画像:名称权重最高(它就是考点的规范说法),描述与章节名做补充
        self._topic_vec: dict[str, Counter] = {}
        self._anchor: dict[str, str | None] = {}
        for tid, t in self.topics.items():
            # 知识点画像里不掺公式伪词(它们只用于题目之间比对)
            prof = " ".join([t["name"]] * 3 + [t["desc"], t["chapter"]])
            self._topic_vec[tid] = bigrams(" ".join(_KEEP.findall(prof)))
            self._anchor[tid] = anchor_of(t["name"])

        # 已标注语料。注意:同一道题可能被映射到多个知识点(一题多考点),
        # 评测时必须按「标签集合」判命中,否则会把另一个正确答案算成错误。
        self.corpus: list[tuple[str, Counter]] = []      # (topic_id, vec)
        self._corpus_meta: list[str] = []                # 原始题干,评测时用于留一
        self._corpus_qid: list[str] = []
        self.gold_by_qid: dict[str, set[str]] = defaultdict(set)
        if os.path.exists(practice_path):
            with open(practice_path, encoding="utf-8") as f:
                pr = json.load(f)
            for tid, t in pr["topics"].items():
                if tid not in self.topics:
                    continue
                for q in t.get("questions", []):
                    stem = (q.get("problem") or "").strip()
                    if len(stem) < 6:
                        continue
                    qid = q.get("qid") or stem[:40]
                    self.corpus.append((tid, bigrams(normalize(stem))))
                    self._corpus_meta.append(stem)
                    self._corpus_qid.append(qid)
                    self.gold_by_qid[qid].add(tid)

        docs = list(self._topic_vec.values()) + [v for _, v in self.corpus]
        df: Counter = Counter()
        for d in docs:
            df.update(d.keys())
        n = max(1, len(docs))
        self.idf = {k: math.log((n + 1) / (v + 1)) + 1.0 for k, v in df.items()}

        # 考试套话("下列各式中…的是""如图""已知""求…的值")会让毫不相干的两道题
        # 看起来很像。语料只有几百篇,idf 压不住,所以按文档频率直接把它们剔除:
        # 出现在 >12% 文档里的二元组一律置零。这是数据驱动的,不需要手写停用词表。
        self.stop: set[str] = {k for k, v in df.items() if v / n > 0.12}
        for k in self.stop:
            self.idf[k] = 0.0

    # ---------------------------------------------------------------- 检索

    def suggest(self, stem: str, grade: str | None = None, top_k: int = 3,
                exclude_corpus_idx: int | None = None) -> list[dict]:
        qv = bigrams(normalize(stem))
        if not qv:
            return []

        term = {tid: _cos(qv, tv, self.idf) for tid, tv in self._topic_vec.items()}

        # 近邻投票:取相似度最高的若干道已标注题,按相似度累加到它们的知识点上
        sims: list[tuple[float, str]] = []
        for i, (tid, cv) in enumerate(self.corpus):
            if exclude_corpus_idx is not None and i == exclude_corpus_idx:
                continue
            s = _cos(qv, cv, self.idf)
            if s > 0.02:
                sims.append((s, tid))
        sims.sort(reverse=True)
        knn: dict[str, float] = defaultdict(float)
        for s, tid in sims[:12]:
            knn[tid] += s
        if knn:
            mx = max(knn.values())
            knn = {k: v / mx for k, v in knn.items()}

        # 词根精确命中:题干里原样出现「分式」「一次函数」这类考点词根
        body = normalize(stem)
        scored = []
        for tid in self.topics:
            anchor = self._anchor.get(tid)
            hit = 1.0 if (anchor and anchor in body) else 0.0
            score = 0.42 * term.get(tid, 0.0) + 0.38 * knn.get(tid, 0.0) + 0.20 * hit
            if score <= 0:
                continue
            # 年级只做柔性加权,不做硬过滤:同一考点可能跨学期复现
            if grade and self.topics[tid]["grade"]:
                gap = abs(GRADES.index(grade) - GRADES.index(self.topics[tid]["grade"])) \
                    if grade in GRADES and self.topics[tid]["grade"] in GRADES else 0
                score *= (1.0 if gap == 0 else 0.82 if gap == 1 else 0.62)
            scored.append((score, tid))

        scored.sort(reverse=True)
        top = scored[:top_k]
        if not top:
            return []
        # 归一到 0–1 的置信度:低置信度要在校对页高亮提示教师(FR-HW-10)
        best = top[0][0] or 1.0
        return [{
            "kp_id": tid,
            "kp_name": self.topics[tid]["name"],
            "grade": self.topics[tid]["grade"],
            "chapter": self.topics[tid]["chapter"],
            "confidence": round(min(1.0, score / best) * min(1.0, best / 0.45), 3),
        } for score, tid in top]

    def list_topics(self, grade: str | None = None, q: str | None = None) -> list[dict]:
        out = []
        for t in self.topics.values():
            if grade and t["grade"] != grade:
                continue
            if q and q not in t["name"] and q not in t["chapter"] and q not in (t["desc"] or ""):
                continue
            out.append(t)
        out.sort(key=lambda t: (GRADES.index(t["grade"]) if t["grade"] in GRADES else 9, t["chapter"], t["name"]))
        return out


@lru_cache(maxsize=1)
def get_index() -> KgIndex:
    return KgIndex()


# ---------------------------------------------------------------- 留一法评测

def evaluate(top_k: int = 3) -> dict:
    """留一法自评 —— 唯一诚实的质量口径,不评测就没资格说"标注准确率"。

    命中判定按**标签集合**:同一道题可能合法地属于多个知识点。
    同时给出章节级命中率:教师是在 3 个候选里点选,落对章节就已经很省事了。
    """
    ix = get_index()
    n = top1 = topk = ch1 = chk = 0
    for i, (gold, _) in enumerate(ix.corpus):
        qid = ix._corpus_qid[i]
        golds = ix.gold_by_qid.get(qid, {gold})
        gold_chapters = {ix.topics[g]["chapter"] for g in golds if g in ix.topics}
        sug = ix.suggest(ix._corpus_meta[i], grade=ix.topics[gold]["grade"],
                         top_k=top_k, exclude_corpus_idx=i)
        n += 1
        if not sug:
            continue
        ids = [s["kp_id"] for s in sug]
        chs = [ix.topics[t]["chapter"] for t in ids if t in ix.topics]
        if ids[0] in golds:
            top1 += 1
        if golds & set(ids):
            topk += 1
        if chs and chs[0] in gold_chapters:
            ch1 += 1
        if gold_chapters & set(chs):
            chk += 1
    r = lambda x: round(x / n, 4) if n else 0.0
    return {"n": n, "top1": r(top1), f"top{top_k}": r(topk),
            "chapter_top1": r(ch1), f"chapter_top{top_k}": r(chk)}


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--try", dest="probe", help="试一条题干")
    a = ap.parse_args()
    ix = get_index()
    print(f"知识点 {len(ix.topics)} 个 / 已标注语料 {len(ix.corpus)} 条", file=sys.stderr)
    if a.eval:
        print(evaluate())
    if a.probe:
        for s in ix.suggest(a.probe):
            print(f"  {s['confidence']:.2f}  {s['grade']} {s['chapter']} · {s['kp_name']}")
