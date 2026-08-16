# -*- coding: utf-8 -*-
"""
构建知识图谱预览页:把 junior_math_kg.json + teaching_layers.json + practice_recommend.json
裁剪合并后注入 preview/template.html 的占位符,输出自包含单文件 preview/index.html。
输出页面无任何外部依赖(CSP 环境可直接打开/发布)。
用法: 在项目根目录执行 python scripts/build_preview.py
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAX_Q_PER_TOPIC = 3      # 每个知识点内嵌的样例练习数(完整数据仍在 practice_recommend.json)
MAX_ANALYSIS_LEN = 600


def dpath(*p):
    return os.path.join(BASE, *p)


def main():
    kg = json.load(open(dpath("data", "junior_math_kg.json"), encoding="utf-8"))
    tl = json.load(open(dpath("data", "teaching_layers.json"), encoding="utf-8"))
    pr = json.load(open(dpath("data", "practice_recommend.json"), encoding="utf-8"))

    by_id = {n["id"]: n for n in kg["nodes"]}

    domains, chapters, topics = [], [], []
    for n in kg["nodes"]:
        if n["level"] == "domain":
            domains.append({"id": n["id"], "name": n["name"]})
        elif n["level"] == "chapter":
            chapters.append({
                "id": n["id"], "name": n["name"], "grade": n["grade"], "seq": n["seq"],
                "domain": n["parent_domain"], "desc": n.get("desc", ""),
                "weight": n.get("exam_weight", "medium"),
            })
        else:
            chapter = by_id[n["parent"]]
            topics.append({
                "id": n["id"], "name": n["name"], "chapter": n["parent"],
                "domain": chapter["parent_domain"], "grade": n["grade"], "seq": n["seq"],
                "desc": n.get("desc", ""), "weight": n.get("exam_weight", "medium"),
            })

    prereq, related = [], []
    for e in kg["edges"]:
        if e["type"] == "PREREQUISITE_OF":
            prereq.append([e["from"], e["to"]])
        elif e["type"] == "RELATED_TO":
            related.append([e["from"], e["to"]])

    practice = {}
    for tid, t in pr["topics"].items():
        qs = []
        for q in t["questions"][:MAX_Q_PER_TOPIC]:
            analysis = (q.get("analysis") or "").strip()
            if len(analysis) > MAX_ANALYSIS_LEN:
                analysis = analysis[:MAX_ANALYSIS_LEN] + "…"
            qs.append({
                "problem": (q.get("problem") or "").strip(),
                "options": q.get("options") or {},
                "answer": q.get("answer", ""),
                "analysis": analysis,
                "difficulty": q.get("difficulty", ""),
                "source": (q.get("source") or "")[:60],
            })
        practice[tid] = {"count": t["practice_count"], "questions": qs}

    payload = {
        "meta": {
            "title": kg["meta"]["title"],
            "textbook": kg["meta"]["textbook"],
            "version": kg["meta"]["version"],
            "tocNote": kg["meta"].get("toc_verification", ""),
            "nodeCount": kg["meta"]["node_count"],
            "edgeCount": kg["meta"]["edge_count"],
            # 注意区分两个口径:mapped_questions 是去重后的题目数;
            # sum(practice_count) 会把一题多考点的题目重复计入,是"知识点↔题目"挂载关系数。
            "practiceTotal": pr["meta"]["mapped_questions"],
            "practiceLinks": sum(t["practice_count"] for t in pr["topics"].values()),
            "practiceTopics": len(pr["topics"]),
            "practiceSource": pr["meta"]["source_dataset"],
            "practiceAlign": pr["meta"]["align_method"],
        },
        "domains": domains,
        "chapters": chapters,
        "topics": topics,
        "prereq": prereq,
        "related": related,
        "thoughts": tl.get("thoughts", []),
        "geoModels": tl.get("geo_models", []),
        "misconceptions": tl.get("misconceptions", []),
        "practice": practice,
    }

    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 避免 JSON 内容意外闭合 <script> 标签
    blob = blob.replace("<", "\\u003c")

    tpl = open(dpath("preview", "template.html"), encoding="utf-8").read()
    if "__KG_DATA__" not in tpl:
        raise SystemExit("template.html 缺少 __KG_DATA__ 占位符")
    out = tpl.replace("__KG_DATA__", blob)

    path = dpath("preview", "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)

    print(f"生成 {path}")
    print(f"  内嵌数据 {len(blob)/1024:.0f} KB,页面总计 {len(out)/1024:.0f} KB")
    print(f"  知识点 {len(topics)} / 章节 {len(chapters)} / 领域 {len(domains)}")
    print(f"  前置关系 {len(prereq)} / 相关关系 {len(related)}")
    print(f"  易错点 {len(payload['misconceptions'])} / 思想方法 {len(payload['thoughts'])} / 几何模型 {len(payload['geoModels'])}")
    print(f"  练习 {payload['meta']['practiceTotal']} 道 / 挂载关系 {payload['meta']['practiceLinks']} 条,"
          f"页内样例 {sum(len(v['questions']) for v in practice.values())} 道")


if __name__ == "__main__":
    main()
