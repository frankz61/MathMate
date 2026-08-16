# -*- coding: utf-8 -*-
"""
将 TAL-SCQ5K(好未来中英数学选择题,研究用途)按知识标签对齐到本图谱知识点。
输出:
  1) tal_scq5k_junior_mapped.jsonl —— 全部映射成功的题目(带 topic_ids)
  2) practice_recommend.json —— 每个知识点:Top练习 + 关联易错点(推荐数据)
用法: 在项目根目录执行 python scripts/align_practice.py
"""
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # MathMate 项目根
DATA_DIR = os.path.join(BASE, "data", "raw", "tal-scq5k", "ch_single_choice_constructed_5K")
FILES = ["ch_single_choice_train_3K.jsonl", "ch_single_choice_test_2K.jsonl"]
MAX_PER_TOPIC = 6

# ---------- 映射规则:按顺序匹配完整标签路径(仅处理 课内体系->知识点 分支) ----------
# 目标 id 为北师大版 2024 新版教材编码(见 data/kg_source.json)
RULES = [
    # 排除高中内容
    (r"直线和圆的方程|圆锥曲线|弧度制|诱导公式|二倍角|任意角|三角恒等|平面向量|复数|导数|数列|排列组合|二项式|立方和|立方差", None),

    # ---- 数(七上有理数 / 八上实数) ----
    (r"正数和负数", "tp-02-01"),
    (r"有理数的概念及分类|有理数的定义|数->有理数->分数", "tp-02-01"),
    (r"->数轴|相反数|->绝对值", "tp-02-02"),
    (r"有理数比较大小", "tp-02-03"),
    (r"有理数加法|有理数减法|加减混合", "tp-02-04"),
    (r"有理数乘法|有理数除法|乘除混合|四则混合|倒数", "tp-02-05"),
    (r"有理数乘方|含乘方的有理数混合", "tp-02-06"),
    (r"科学记数法", "tp-02-06"),
    (r"数->有理数->有理数与实际问题", "tp-02-07"),
    (r"平方根", "tp-14-02"),
    (r"立方根", "tp-14-02"),
    (r"无理数|实数基础概念|实数运算|实数与数轴", "tp-14-01"),

    # ---- 式(七上整式 / 七下整式乘除 / 八下因式分解与分式 / 八上二次根式) ----
    (r"同底数幂|幂的乘方|积的乘方", "tp-07-01"),
    (r"乘法公式", "tp-07-03"),
    (r"式->整式的乘除", "tp-07-02"),
    (r"合并同类项|去括号|整式的加减", "tp-03-04"),
    (r"代数式求值|代数式的值", "tp-03-02"),
    (r"因式分解->提公因式", "tp-23-02"),
    (r"因式分解->公式法->利用平方差|因式分解->公式法->提公因式\+平方差|完全平方公式因式分解", "tp-23-03"),
    (r"因式分解", "ch-23"),
    (r"分式方程", "tp-24-04"),
    (r"分式->分式的定义|分式的基本性质|分式的概念", "tp-24-01"),
    (r"约分|通分", "tp-24-02"),
    (r"分式的运算|分式的加减|分式的乘除", "tp-24-03"),
    (r"式->分式", "tp-24-03"),
    (r"二次根式->二次根式的定义|二次根式的概念", "tp-14-03"),
    (r"二次根式", "ch-14"),

    # ---- 方程与不等式 ----
    (r"一元一次方程.*应用|实际问题.*一元一次方程", "tp-05-04"),
    (r"一元一次方程", "tp-05-03"),
    (r"二元一次方程组.*代入", "tp-17-02"),
    (r"二元一次方程组.*加减", "tp-17-03"),
    (r"三元一次方程组", "tp-17-05"),
    (r"二元一次方程组.*应用|实际问题.*二元一次方程组", "tp-17-04"),
    (r"二元一次方程组.*概念|二元一次方程组.*的解", "tp-17-01"),
    (r"二元一次方程组", "ch-17"),
    (r"一元二次方程.*概念|一元二次方程的定义", "tp-27-01"),
    (r"一元二次方程.*配方", "tp-27-02"),
    (r"一元二次方程.*公式法|一元二次方程.*判别式|求根公式", "tp-27-03"),
    (r"一元二次方程.*因式分解法", "tp-27-04"),
    (r"韦达|根与系数", "tp-27-05"),
    (r"一元二次方程.*应用", "tp-27-06"),
    (r"一元二次方程", "ch-27"),
    (r"不等式的性质", "tp-21-01"),
    (r"一元一次不等式组", "tp-21-04"),
    (r"一元一次不等式", "tp-21-02"),

    # ---- 函数(八上一次函数 / 九上反比例 / 九下二次) ----
    (r"一次函数与方程、不等式", "tp-21-03"),
    (r"一次函数与几何综合|一次函数与实际问题|一次函数模型", "tp-16-04"),
    (r"正比例函数|一次函数基础|一次函数的图象|一次函数的增减性|一次函数的几何变换", "tp-16-03"),
    (r"函数->一次函数", "tp-16-03"),
    (r"变量与函数|函数的概念|函数的图象", "tp-16-01"),
    (r"二次函数与一元二次方程|与坐标轴交点", "tp-33-05"),
    (r"二次函数与几何|二次函数.*最值|二次函数.*面积|二次函数模型|二次函数应用|函数的应用.*二次", "tp-33-04"),
    (r"二次函数的概念|二次函数的定义", "tp-33-01"),
    (r"求二次函数.*解析式|待定系数法.*二次", "tp-33-03"),
    (r"二次函数", "tp-33-02"),
    (r"反比例函数的定义|根据反比例函数定义", "tp-31-01"),
    (r"反比例函数图象与性质|反比例函数增减性|反比例函数的图象", "tp-31-02"),
    (r"反比例函数", "tp-31-03"),

    # ---- 图形与几何 ----
    (r"正数|勾股定理的逆定理|勾股逆定理", None),  # 占位防误配(不会被先命中,删除影响小)
    (r"勾股定理的逆定理|勾股逆定理", "tp-13-02"),
    (r"勾股定理", "tp-13-01"),
    (r"三角形的基础|三边关系|三角形的分类|三角形->三角形及多边形->三角形的基础", "tp-10-01"),
    (r"与三角形有关的线段|与中线或等分线|三角形的周长与面积", "tp-10-03"),
    (r"与三角形有关的角|内角|外角", "tp-10-02"),
    (r"三角形及多边形->多边形|多边形.*内角", "tp-25-04"),
    (r"全等", "tp-10-05"),
    (r"线段垂直平分线|中垂线", "tp-11-02"),
    (r"角平分线", "tp-11-03"),
    (r"等边三角形", "tp-20-02"),
    (r"等腰三角形", "tp-20-02"),
    (r"轴对称", "tp-11-01"),
    (r"三角形中位线|中位线", "tp-25-03"),
    (r"矩形", "tp-26-02"),
    (r"菱形", "tp-26-01"),
    (r"正方形", "tp-26-03"),
    (r"四边形->平行四边形", "tp-25-01"),
    (r"特殊平行四边形", "ch-26"),
    (r"比例线段", "tp-29-01"),
    (r"相似三角形的判定", "tp-29-04"),
    (r"相似三角形的性质", "tp-29-05"),
    (r"位似", "tp-29-06"),
    (r"相似三角形|相似多边形", "ch-29"),
    (r"解直角三角形", "tp-32-03"),
    (r"锐角三角函数", "tp-32-01"),
    (r"垂径定理", "tp-34-02"),
    (r"正多边形与圆|圆内接正多边形|圆与正多边形", "tp-34-07"),
    (r"内切圆|切线", "tp-34-06"),
    (r"圆周角", "tp-34-03"),
    (r"弧长|扇形", "tp-34-08"),
    (r"课内体系->知识点->圆->", "ch-34"),
    (r"对顶角|邻补角|相交线->相交的定义", "tp-08-01"),
    (r"平行线的性质", "tp-08-05"),
    (r"平行线的判定", "tp-08-04"),
    (r"相交线与平行线", "ch-08"),
    (r"点线面体", "tp-01-01"),
    (r"立体图形与平面图形|展开图", "tp-01-02"),
    (r"直线、射线、线段", "tp-04-01"),
    (r"余角和补角", "tp-04-04"),
    (r"几何图形初步->角", "tp-04-03"),
    (r"命题与证明|区分真假命题", "tp-19-01"),
    (r"平移和旋转|图形的旋转|->旋转", "tp-22-02"),
    (r"->平移|图形的平移", "tp-22-01"),
    (r"平面直角坐标系|坐标系", "tp-15-02"),

    # ---- 统计与概率 ----
    (r"数据的分析->中位数|众数", "tp-18-02"),
    (r"数据的分析->方差", "tp-18-04"),
    (r"数据的分析->平均数", "tp-18-01"),
    (r"用样本估计总体|全面调查|抽样调查", "tp-06-01"),
    (r"直方图", "tp-06-03"),
    (r"统计->", "tp-06-02"),
    (r"统计图表|趋势图", "tp-06-02"),
    (r"概率.*频率|频率.*概率", "tp-28-02"),
    (r"随机事件|必然事件|不可能事件", "tp-09-01"),
    (r"列举法|树状图|列表法", "tp-28-01"),
    (r"概率初步|->概率", "tp-28-01"),
]

PREFIX_RE = re.compile(r"^【[^】]*】")

def map_routes(routes):
    """返回该题命中的知识点 id 集合"""
    hits = set()
    for r in routes:
        r = r.replace("知识标签->", "")
        if not r.startswith("课内体系->知识点"):
            continue
        for pat, tid in RULES:
            if re.search(pat, r):
                if tid:
                    hits.add(tid)
                break
    return hits

def clean_options(opt_list):
    opts = {}
    for grp in opt_list:
        for o in grp:
            opts[o.get("aoVal", "?")] = (o.get("content") or "").strip()
    return opts

def load_questions():
    out = []
    for fn in FILES:
        with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                topics = map_routes(d.get("knowledge_point_routes", []))
                if not topics:
                    continue
                src = (d.get("competition_source_list") or [""])
                # 剔除高中题源(标签分支中混有高中联赛题)
                if src and re.search(r"高中|高一|高二|高三", src[0]):
                    continue
                out.append({
                    "qid": d.get("queId") or d.get("qid"),
                    "split": "train" if "train" in fn else "test",
                    "problem": PREFIX_RE.sub("", d.get("problem", "")).strip(),
                    "options": clean_options(d.get("answer_option_list", [])),
                    "answer": d.get("answer_value", ""),
                    "analysis": " ".join(x.strip() for x in d.get("answer_analysis", []) if x),
                    "difficulty": d.get("difficulty", ""),
                    "source": src[0][:60],
                    "routes": [r.replace("知识标签->", "") for r in d.get("knowledge_point_routes", [])],
                    "topic_ids": sorted(topics),
                })
    return out

def main():
    kg = json.load(open(os.path.join(BASE, "data", "junior_math_kg.json"), encoding="utf-8"))
    nodes = {n["id"]: n for n in kg["nodes"]}
    tl = json.load(open(os.path.join(BASE, "data", "teaching_layers.json"), encoding="utf-8"))
    mc_by_topic = {}
    for m in tl["misconceptions"]:
        mc_by_topic.setdefault(m["topic_id"], []).append(
            {"id": m["id"], "point": m["point"], "fix": m["fix"]})

    questions = load_questions()

    # 全量映射结果
    with open(os.path.join(BASE, "data", "tal_scq5k_junior_mapped.jsonl"), "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    # 按知识点聚合(仅知识点级,章节级不进推荐)
    by_topic = {}
    for q in questions:
        for tid in q["topic_ids"]:
            if tid.startswith("tp-"):
                by_topic.setdefault(tid, []).append(q)

    def rank_key(q):
        junior_src = 0 if re.search(r"初一|初二|初三|中考|初中", q["source"]) else 1
        return (junior_src, 0 if q["analysis"] else 1, len(q["problem"]), int(q["difficulty"] or 0))

    topics_out = {}
    for tid, qs in sorted(by_topic.items()):
        n = nodes.get(tid)
        qs = sorted(qs, key=rank_key)
        topics_out[tid] = {
            "name": n["name"],
            "grade": n.get("grade"),
            "chapter": nodes[n["parent"]]["name"] if n.get("parent") else None,
            "misconceptions": mc_by_topic.get(tid, []),
            "practice_count": len(qs),
            "questions": [{
                "qid": q["qid"], "problem": q["problem"], "options": q["options"],
                "answer": q["answer"], "analysis": q["analysis"],
                "difficulty": q["difficulty"], "source": q["source"],
            } for q in qs[:MAX_PER_TOPIC]],
        }

    result = {
        "meta": {
            "title": "知识点→易错点→对应练习 推荐数据",
            "source_dataset": "TAL-SCQ5K(好未来, https://github.com/math-eval/TAL-SCQ5K, 研究用途,遵循其许可)",
            "align_method": "基于题目自带 knowledge_point_routes 标签的规则映射(课内体系分支→图谱知识点id),并剔除题源含高中的题目;映射表见 align_practice.py",
            "total_questions_in_dataset": 5000,
            "mapped_questions": len(questions),
            "topics_with_practice": len(topics_out),
            "max_questions_per_topic": MAX_PER_TOPIC,
            "generated": "2026-08-15",
        },
        "topics": topics_out,
    }
    with open(os.path.join(BASE, "data", "practice_recommend.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"映射成功题目: {len(questions)}/5000")
    print(f"有练习的知识点: {len(topics_out)} 个")
    print("\nTop15 知识点题量:")
    for tid, info in sorted(topics_out.items(), key=lambda kv: -kv[1]["practice_count"])[:15]:
        print(f"  {info['practice_count']:4d}  {tid} {info['name']} ({info['grade']}) 易错点x{len(info['misconceptions'])}")

if __name__ == "__main__":
    main()
