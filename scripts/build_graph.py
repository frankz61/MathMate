# -*- coding: utf-8 -*-
"""
构建脚本:校验 kg_source.json,合成 BELONGS_TO 层级边,
输出完整图谱 junior_math_kg.json 和 Neo4j 导入脚本 neo4j_import.cypher。
用法: 在项目根目录执行 python scripts/build_graph.py
"""
import json
import os
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # MathMate 项目根


def dpath(name):
    return os.path.join(BASE, "data", name)


SRC = dpath("kg_source.json")
OUT_JSON = dpath("junior_math_kg.json")
OUT_CYPHER = dpath("neo4j_import.cypher")

errors = []

with open(SRC, encoding="utf-8") as f:
    src = json.load(f)

nodes = src["nodes"]
manual_edges = src["edges"]

# ---------- 校验 ----------
id_set = set()
for n in nodes:
    if n["id"] in id_set:
        errors.append(f"重复节点 id: {n['id']}")
    id_set.add(n["id"])

by_id = {n["id"]: n for n in nodes}

# 层级字段校验
for n in nodes:
    if n["level"] == "chapter" and n.get("parent_domain") not in id_set:
        errors.append(f"章节 {n['id']} 的 parent_domain 不存在")
    if n["level"] == "topic" and n.get("parent") not in id_set:
        errors.append(f"知识点 {n['id']} 的 parent 不存在")
    if n["level"] == "topic" and n.get("parent", "").startswith("tp-"):
        errors.append(f"知识点 {n['id']} 的 parent 不是章节")

# 边校验
seen = set()
for e in manual_edges:
    key = (e["from"], e["to"], e["type"])
    if key in seen:
        errors.append(f"重复边: {key}")
    seen.add(key)
    if e["from"] not in id_set or e["to"] not in id_set:
        errors.append(f"悬空边: {key}")
    if e["from"] == e["to"]:
        errors.append(f"自环: {key}")

# 前置关系环检测(DFS)
prereq = defaultdict(list)
for e in manual_edges:
    if e["type"] == "PREREQUISITE_OF":
        prereq[e["from"]].append(e["to"])

WHITE, GRAY, BLACK = 0, 1, 2
color = defaultdict(int)

def dfs(u, path):
    color[u] = GRAY
    for v in prereq[u]:
        if color[v] == GRAY:
            errors.append(f"PREREQUISITE_OF 存在环: {' -> '.join(path + [v])}")
        elif color[v] == WHITE:
            dfs(v, path + [v])
    color[u] = BLACK

for nid in id_set:
    if color[nid] == WHITE:
        dfs(nid, [nid])

# PREREQUISITE_OF 不应指向自己的祖先章节的祖先(层级混乱检查,轻量版):
# topic 的前置不能是它自己的后代章节里的点 —— 由环检测覆盖,略。

if errors:
    print("校验失败:")
    for e in errors:
        print(" -", e)
    sys.exit(1)

# ---------- 教学辅助扩展数据校验(teaching_layers.json) ----------
import os

tl_stats = None
if os.path.exists(dpath("teaching_layers.json")):
    with open(dpath("teaching_layers.json"), encoding="utf-8") as f:
        tl = json.load(f)
    topic_ids = {n["id"] for n in nodes if n["level"] == "topic"}
    tl_ids = set()
    for t in tl.get("thoughts", []):
        tl_ids.add(t["id"])
        for tid in t.get("related_topics", []):
            if tid not in id_set:
                errors.append(f"思想方法 {t['id']} 引用了不存在的节点 {tid}")
    for m in tl.get("misconceptions", []):
        tl_ids.add(m["id"])
        if m.get("topic_id") not in topic_ids:
            errors.append(f"易错点 {m['id']} 引用了不存在的知识点 {m.get('topic_id')}")
        if set(m.keys()) - {"id", "topic_id", "point", "cause", "fix"}:
            errors.append(f"易错点 {m['id']} 存在异常字段名(检查是否笔误): {set(m.keys()) - {'id', 'topic_id', 'point', 'cause', 'fix'}}")
    for g in tl.get("geo_models", []):
        tl_ids.add(g["id"])
        for tid in g.get("related_topics", []):
            if tid not in id_set:
                errors.append(f"几何模型 {g['id']} 引用了不存在的节点 {tid}")
    if len(tl_ids) != sum(len(tl.get(k, [])) for k in ("thoughts", "misconceptions", "geo_models")):
        errors.append("teaching_layers.json 内部存在重复 id")

    if errors:
        print("校验失败:")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    # 统计被覆盖的知识点
    covered = set()
    for m in tl.get("misconceptions", []):
        covered.add(m["topic_id"])
    tl_stats = {
        "思想方法": len(tl.get("thoughts", [])),
        "易错点": len(tl.get("misconceptions", [])),
        "几何模型": len(tl.get("geo_models", [])),
        "易错点覆盖知识点数": len(covered),
    }
    print("教学辅助扩展数据校验通过:", tl_stats)

# ---------- 练习推荐数据校验(practice_recommend.json) ----------
if os.path.exists(dpath("practice_recommend.json")):
    with open(dpath("practice_recommend.json"), encoding="utf-8") as f:
        pr = json.load(f)
    for tid in pr.get("topics", {}):
        if tid not in id_set:
            errors.append(f"练习推荐引用了不存在的知识点 {tid}")
    if errors:
        print("校验失败:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print(f"练习推荐数据校验通过: {len(pr['topics'])} 个知识点, "
          f"共 {sum(t['practice_count'] for t in pr['topics'].values())} 道题")

# ---------- 合成 BELONGS_TO ----------
belong_edges = []
for n in nodes:
    if n["level"] == "topic":
        belong_edges.append({"from": n["id"], "to": n["parent"], "type": "BELONGS_TO"})
    elif n["level"] == "chapter":
        belong_edges.append({"from": n["id"], "to": n["parent_domain"], "type": "BELONGS_TO"})

# ---------- 输出完整 JSON ----------
level_rank = {"domain": 0, "chapter": 1, "topic": 2}
nodes_sorted = sorted(nodes, key=lambda n: (level_rank[n["level"]], n.get("seq", 0), n["id"]))
edge_rank = {"BELONGS_TO": 0, "PREREQUISITE_OF": 1, "RELATED_TO": 2}
all_edges = sorted(belong_edges + manual_edges, key=lambda e: (edge_rank[e["type"]], e["from"], e["to"]))

stats = {
    "domain": sum(1 for n in nodes if n["level"] == "domain"),
    "chapter": sum(1 for n in nodes if n["level"] == "chapter"),
    "topic": sum(1 for n in nodes if n["level"] == "topic"),
}

final = {
    "meta": {
        **{k: v for k, v in src["meta"].items() if k not in ("note",)},
        "node_count": len(nodes_sorted),
        "edge_count": len(all_edges),
        "node_stats": stats,
        "edge_stats": {
            t: sum(1 for e in all_edges if e["type"] == t)
            for t in ("BELONGS_TO", "PREREQUISITE_OF", "RELATED_TO")
        },
        "usage": {
            "neo4j": "neo4j_import.cypher 可在 Neo4j Browser / cypher-shell 直接执行",
            "note": "PREREQUISITE_OF 方向: from 是 to 的前置知识点",
        },
    },
    "nodes": nodes_sorted,
    "edges": all_edges,
}

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(final, f, ensure_ascii=False, indent=2)

# ---------- 输出 Cypher ----------
def q(s):
    return json.dumps(s, ensure_ascii=False)

label_by_level = {"domain": "Domain", "chapter": "Chapter", "topic": "Topic"}

lines = [
    f"// {src['meta']['title']} —— Neo4j 导入脚本",
    "// 由 build_graph.py 自动生成;可直接在 Neo4j Browser 或 cypher-shell 中执行",
    "",
    "CREATE CONSTRAINT kp_id IF NOT EXISTS FOR (n:KnowledgePoint) REQUIRE n.id IS UNIQUE;",
    "",
    "// ---------- 节点 ----------",
]
for n in nodes_sorted:
    props = [f"n.name = {q(n['name'])}", f"n.level = {q(n['level'])}"]
    if "grade" in n:
        props.append(f"n.grade = {q(n['grade'])}")
    if "seq" in n:
        props.append(f"n.seq = {n['seq']}")
    if "desc" in n:
        props.append(f"n.desc = {q(n['desc'])}")
    if "exam_weight" in n:
        props.append(f"n.exam_weight = {q(n['exam_weight'])}")
    label = label_by_level[n["level"]]
    lines.append(
        f"MERGE (n:KnowledgePoint {{id: {q(n['id'])}}}) "
        f"SET n:{label}, {', '.join(props)};"
    )

lines += ["", "// ---------- 关系 ----------"]
for e in all_edges:
    lines.append(
        f"MATCH (a:KnowledgePoint {{id: {q(e['from'])}}}), "
        f"(b:KnowledgePoint {{id: {q(e['to'])}}}) "
        f"MERGE (a)-[:{e['type']}]->(b);"
    )

lines += [
    "",
    "// ---------- 验证查询 ----------",
    "MATCH (n:KnowledgePoint) RETURN count(n) AS 节点总数;",
    "MATCH ()-[r]->() RETURN type(r) AS 关系类型, count(r) AS 数量 ORDER BY 关系类型;",
    "// 查看某知识点的全部前置(传递闭包):",
    "// MATCH (p:Topic)-[:PREREQUISITE_OF*1..]->(t:Topic {name:'配方法'}) RETURN DISTINCT p.name;",
    "",
]

with open(OUT_CYPHER, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("校验通过,生成完成:")
print(f"  节点 {len(nodes_sorted)} 个(领域 {stats['domain']} / 章节 {stats['chapter']} / 知识点 {stats['topic']})")
print(f"  边 {len(all_edges)} 条: " + ", ".join(f"{t} {final['meta']['edge_stats'][t]}" for t in edge_rank))
print(f"  -> {OUT_JSON}")
print(f"  -> {OUT_CYPHER}")
