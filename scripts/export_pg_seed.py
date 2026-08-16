# -*- coding: utf-8 -*-
"""
将图谱 + 教学层数据导出为 MathMate 的 PostgreSQL L1 母版种子 SQL。
目标 schema 见 docs/DESIGN.md §3.2(kg_nodes / kg_relations)。
- 节点: 知识点层级(domain/chapter/topic)→node_type='knowledge_point'
        思想方法/几何模型→'method',易错点→'misconception'
- 关系: BELONGS_TO→PART_OF, PREREQUISITE_OF/RELATED_TO 原样,
        misconception→COMMON_ERROR_IN→知识点, method→METHOD_FOR→知识点
- 层级 layer='L1',tenant_id 为 NULL,source='l1_seed'
- UUID 采用 uuid5(URL namespace + code),确定性、可幂等重导
用法: 在项目根目录执行 python scripts/export_pg_seed.py
"""
import json
import os
import uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://mathmate.local/l1/")

EXAM_WEIGHT = {"high": 0.90, "medium": 0.60, "low": 0.30, "optional": 0.10}
DIFF_BASE = {"high": 3, "medium": 2, "low": 1, "optional": 1}
GRADE_MAP = {"七": 7, "八": 8, "九": 9}
SEMESTER_MAP = {"上": 1, "下": 2}
TEXTBOOK = "beishida-2024"


def uid(code):
    return str(uuid.uuid5(NS, code))


def q(s):
    """SQL 字符串转义"""
    return "'" + str(s).replace("'", "''") + "'"


def arr(items):
    return "ARRAY[" + ", ".join(q(x) for x in items) + "]" if items else "ARRAY[]::TEXT[]"


def grade_semester(grade_str):
    if not grade_str or len(grade_str) < 2:
        return "NULL", "NULL"
    g = GRADE_MAP.get(grade_str[0])
    s = SEMESTER_MAP.get(grade_str[1])
    return (str(g) if g else "NULL"), (str(s) if s else "NULL")


def main():
    kg = json.load(open(os.path.join(BASE, "data", "junior_math_kg.json"), encoding="utf-8"))
    tl = json.load(open(os.path.join(BASE, "data", "teaching_layers.json"), encoding="utf-8"))
    nodes = {n["id"]: n for n in kg["nodes"]}
    edges = kg["edges"]

    node_rows, rel_rows = [], []

    # ---- 图谱节点 ----
    for n in kg["nodes"]:
        g, s = grade_semester(n.get("grade"))
        ew = EXAM_WEIGHT.get(n.get("exam_weight"))
        db = DIFF_BASE.get(n.get("exam_weight"))
        domain = None
        if n["level"] == "domain":
            domain = n["name"]
        elif n["level"] == "chapter":
            domain = nodes[n["parent_domain"]]["name"]
        elif n["level"] == "topic":
            domain = nodes[nodes[n["parent"]]["parent_domain"]]["name"]
        node_rows.append(
            f"({q(uid(n['id']))}, 'L1', {q(n['id'])}, {q(n['name'])}, 'knowledge_point', "
            f"{q(domain) if domain else 'NULL'}, {g}, {s}, {q(TEXTBOOK)}, "
            f"{str(db) if db else 'NULL'}, {ew if ew is not None else 'NULL'}, "
            f"{q(n.get('desc', ''))}, 'published')"
        )

    # ---- 思想方法 / 几何模型 → method ----
    for t in tl.get("thoughts", []):
        node_rows.append(
            f"({q(uid(t['id']))}, 'L1', {q(t['id'])}, {q(t['name'])}, 'method', NULL, NULL, NULL, "
            f"{q(TEXTBOOK)}, NULL, NULL, {q(t['desc'] + '。典型应用:' + t['applications'])}, 'published')"
        )
        for tid in t.get("related_topics", []):
            rel_rows.append((t["id"], tid, "METHOD_FOR"))

    for gm in tl.get("geo_models", []):
        desc = f"图形特征:{gm['features']}。核心结论:{gm['conclusion']}。适用:{gm['usage']}"
        node_rows.append(
            f"({q(uid(gm['id']))}, 'L1', {q(gm['id'])}, {q(gm['name'])}, 'method', NULL, NULL, NULL, "
            f"{q(TEXTBOOK)}, NULL, NULL, {q(desc)}, 'published')"
        )
        for tid in gm.get("related_topics", []):
            rel_rows.append((gm["id"], tid, "METHOD_FOR"))

    # ---- 易错点 → misconception ----
    for m in tl.get("misconceptions", []):
        desc = f"易错点:{m['point']}。错因:{m['cause']}。纠正:{m['fix']}"
        node_rows.append(
            f"({q(uid(m['id']))}, 'L1', {q(m['id'])}, {q(m['point'])}, 'misconception', NULL, NULL, NULL, "
            f"{q(TEXTBOOK)}, NULL, NULL, {q(desc)}, 'published')"
        )
        rel_rows.append((m["id"], m["topic_id"], "COMMON_ERROR_IN"))

    # ---- 图谱关系 ----
    rel_type_map = {"BELONGS_TO": "PART_OF", "PREREQUISITE_OF": "PREREQUISITE_OF", "RELATED_TO": "RELATED_TO"}
    for e in edges:
        rt = rel_type_map[e["type"]]
        if rt == "PREREQUISITE_OF":
            # 图谱 JSON 里 from 是 to 的前置;目标 schema 是 kp_id=后继, prerequisite_kp_id=前置,
            # 递归 CTE 由 kp_id 出发沿 prerequisite_kp_id 回溯,故此处必须调向。
            rel_rows.append((e["to"], e["from"], rt))
        else:
            rel_rows.append((e["from"], e["to"], rt))

    out = ["-- MathMate L1 母版种子数据(初中数学·北师大2024新版)",
           "-- 目标 schema: docs/DESIGN.md §3.2 (kg_nodes / kg_relations)",
           "-- 生成脚本: scripts/export_pg_seed.py;确定性 UUID,可重复导入(ON CONFLICT DO NOTHING)",
           "-- 说明: PREREQUISITE_OF 方向为 kp_id=后继知识点, prerequisite_kp_id=其前置;",
           "--       其他关系类型 kp_id=源, prerequisite_kp_id=目标(见 DESIGN.md §3.2 命名注)",
           "",
           "BEGIN;",
           "",
           f"-- 节点 {len(node_rows)} 条: 知识点层级 {len(kg['nodes'])} / 思想方法 {len(tl.get('thoughts', []))} / "
           f"几何模型 {len(tl.get('geo_models', []))} / 易错点 {len(tl.get('misconceptions', []))}",
           "INSERT INTO kg_nodes (id, layer, code, name, node_type, domain, grade, semester,",
           "    textbook_ver, difficulty_base, exam_weight, description, status) VALUES"]
    out.append(",\n".join(node_rows) + ";")
    out += ["", f"-- 关系 {len(rel_rows)} 条",
            "INSERT INTO kg_relations (id, kp_id, prerequisite_kp_id, relation_type, weight, source) VALUES"]
    out.append(",\n".join(
        f"({q(uid(f'{a}:{b}:{t}'))}, {q(uid(a))}, {q(uid(b))}, {q(t)}, 1.0, 'l1_seed')"
        for a, b, t in rel_rows) + ";")
    out += ["", "COMMIT;", ""]

    path = os.path.join(BASE, "data", "l1_seed.sql")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"生成 {path}: 节点 {len(node_rows)} 条, 关系 {len(rel_rows)} 条")


if __name__ == "__main__":
    main()
