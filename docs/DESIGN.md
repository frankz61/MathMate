# MathMate 系统设计文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v0.1（初稿，待评审） |
| 创建日期 | 2026-08-15 |
| 对应需求 | [PRD.md](./PRD.md) v0.1 |
| 状态 | 草案 |

本文档描述 MathMate 的技术架构、数据模型、核心算法与关键工程决策。需求编号（`FR-*` / `NFR-*`）引用自 PRD。

---

## 1. 架构总览

### 1.1 分层视图

```
┌──────────────────────────────────────────────────────────────────┐
│  接入层                                                            │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  │
│  │ 教室大屏    │  │ 教师端 Web  │  │ 教师移动端  │  │ 管理后台    │  │
│  │ (离线优先)  │  │ (React)    │  │ (PWA/拍照)  │  │ (React)    │  │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  │
└────────┼───────────────┼───────────────┼───────────────┼─────────┘
         │ WS + HTTP     │ HTTP          │ HTTP          │ HTTP
┌────────┴───────────────┴───────────────┴───────────────┴─────────┐
│  API 网关 (Nginx)  — TLS 终止 / 限流 / 路由 / 静态资源             │
└────────┬─────────────────────────────────────────────────────────┘
         │
┌────────┴─────────────────────────────────────────────────────────┐
│  应用层 (FastAPI)                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ 身份与    │ │ 知识图谱  │ │ 作业解析  │ │ 互动投票  │ │ 讲评   │ │
│  │ 组织服务  │ │ 服务      │ │ 服务      │ │ 服务(WS) │ │ 决策   │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
└────────┬─────────────────────────────────────────────────────────┘
         │
┌────────┴─────────────────────────────────────────────────────────┐
│  异步处理层 (Celery Workers)                                       │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────┐        │
│  │ 文档解析队列    │ │ 图像解析队列    │ │ 通用队列        │        │
│  │ (资料→知识点)   │ │ (照片→题目)     │ │ (报表/清理)     │        │
│  └────────────────┘ └────────────────┘ └────────────────┘        │
└────────┬─────────────────────────────────────────────────────────┘
         │
┌────────┴─────────────────────────────────────────────────────────┐
│  能力层                                                            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐              │
│  │ LLM 网关      │ │ 图像预处理    │ │ 文档解析      │              │
│  │ (多模型路由/   │ │ (OpenCV)     │ │ (PyMuPDF/    │              │
│  │  重试/缓存/    │ │              │ │  python-pptx)│              │
│  │  成本计量)     │ │              │ │              │              │
│  └──────────────┘ └──────────────┘ └──────────────┘              │
└────────┬─────────────────────────────────────────────────────────┘
         │
┌────────┴─────────────────────────────────────────────────────────┐
│  存储层                                                            │
│  ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐    │
│  │ PostgreSQL   │ │ Redis    │ │ 对象存储  │ │ ClickHouse   │    │
│  │ + pgvector   │ │ 缓存/队列 │ │ MinIO/OSS│ │ (P2 行为分析) │    │
│  │ 业务+图谱+向量│ │ /WS状态  │ │ 图片/文档 │ │              │    │
│  └──────────────┘ └──────────┘ └──────────┘ └──────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 服务划分

初期采用**模块化单体**（FastAPI 单应用 + 清晰的模块边界），而非微服务。理由：团队规模小、单校数据量小（5000 学生量级）、跨模块事务多（作业发布同时写题目、知识点关联、大屏推送）。模块间通过应用内 service 层调用，但**禁止跨模块直接访问对方的表**，为将来拆分保留边界。

Celery Worker 独立部署，是唯一从一开始就物理分离的部分——因为解析任务的资源画像（长耗时、高内存、外部 API 依赖）与 Web 请求完全不同，必须隔离，否则一个大 PPT 解析会拖垮整个 API。

```
mathmate/
├── apps/
│   ├── api/              # FastAPI 应用
│   │   ├── modules/
│   │   │   ├── identity/     # 租户/学校/班级/教师/学生/设备
│   │   │   ├── kg/           # 知识图谱
│   │   │   ├── homework/     # 作业与题目
│   │   │   ├── interaction/  # 投票会话、WebSocket
│   │   │   └── decision/     # 讲评优先级与看板
│   │   └── core/         # 配置/依赖注入/中间件/异常
│   ├── worker/           # Celery 任务
│   │   └── pipelines/    # 解析管线
│   ├── screen/           # 大屏前端 (React + Vite + PixiJS)
│   ├── teacher/          # 教师端前端 (React + Vite + AntD)
│   └── admin/            # 管理后台
├── packages/
│   ├── llm/              # LLM 网关
│   ├── vision/           # 图像预处理
│   └── schemas/          # 跨端共享的 Pydantic / TS 类型定义
└── docs/
```

---

## 2. 技术选型决策

以下为影响面较大的选型，记录决策与理由。

### ADR-01：知识图谱用 PostgreSQL 存储，不引入 Neo4j

**上下文**：需要存储知识点节点与前置/包含/相关等关系，并支持前置链回溯查询（讲评策略依赖）。

**决策**：使用 PostgreSQL 的邻接表 + 递归 CTE。

**理由**：

- 规模极小。初中数学三年知识点约 400–500 个节点、约 2000 条关系。这个量级用递归 CTE 查询前置链，深度 ≤ 6 层，响应在毫秒级。整个 L1 母版可以完整装进内存。
- 引入 Neo4j 意味着第二套存储、第二套备份策略、第二套运维知识、跨库事务一致性问题（题目在 PG、知识点关联在 Neo4j）。这些成本远超收益。
- 图谱、题目、投票数据在同一个库，讲评优先级计算可以一条 SQL 完成，无需应用层拼装。

**触发重估的条件**：节点数超过 5 万，或出现需要路径搜索/社区发现等真正的图算法需求。

**前置链查询示例**：

```sql
WITH RECURSIVE prereq_chain AS (
    SELECT kp_id, prerequisite_kp_id, 1 AS depth
    FROM kg_relations
    WHERE kp_id = :target_kp AND relation_type = 'PREREQUISITE_OF'
  UNION ALL
    SELECT r.kp_id, r.prerequisite_kp_id, c.depth + 1
    FROM kg_relations r
    JOIN prereq_chain c ON r.kp_id = c.prerequisite_kp_id
    WHERE r.relation_type = 'PREREQUISITE_OF' AND c.depth < 6
)
SELECT DISTINCT prerequisite_kp_id, MIN(depth) AS depth
FROM prereq_chain GROUP BY prerequisite_kp_id ORDER BY depth;
```

### ADR-02：作业解析以多模态 LLM 为主管线，传统 OCR 为辅

**上下文**：需要从照片中切分题目、识别中文题干、数学公式（→LaTeX）、几何图形与题目结构。

**决策**：主管线 = 图像预处理（OpenCV）→ 多模态 LLM 一次性完成版面理解 + 内容识别 + 结构化输出；传统 OCR（PaddleOCR）仅作为纯文本兜底与置信度交叉校验。

**理由**：

- 传统管线需要串联版面分析、文本检测、文本识别、公式检测、公式识别（如 pix2tex）、结构重建六个模型，每个环节的误差累积，且题目切分逻辑（"第 7 题"从哪到哪）用规则写非常脆弱——题号格式、分栏排版、跨页题目都是坑。
- 多模态模型可以在一次调用里理解"这是一份数学作业，请按题号切分并输出每题的结构"，版面理解与语义理解是同一件事，天然更适合。
- 关键代价是**幻觉风险**：模型可能"补全"照片里没有的内容。这个风险由两个机制控制：① 强制教师校对（FR-HW-09，产品级不可妥协的约束）；② 交叉校验——用 PaddleOCR 提取纯文本，与 LLM 输出做文本相似度比对，偏差大的题目标记为低置信度并高亮（FR-HW-10）。

**风险与前置验证**：手写体识别质量存在不确定性。PRD 的 M0 里程碑专门用于验证，未通过则回退到"仅自动切题 + 教师手动录入"的降级方案（该降级路径同时也是 NFR-AV-07 的 LLM 不可用兜底）。

### ADR-03：后端 Python / FastAPI

- 解析管线深度依赖 Python 生态（OpenCV、PyMuPDF、python-pptx、各类模型 SDK），后端与 Worker 同语言避免跨语言服务调用。
- FastAPI 的 Pydantic 模型可直接生成 OpenAPI，前端类型由此生成，减少前后端契约漂移。
- 异步 IO 适合本系统的画像：大量时间花在等待 LLM 与数据库，CPU 密集工作全部在 Celery Worker 里。

### ADR-04：大屏前端 React + Vite + PixiJS

- React + Vite：与教师端同栈，组件与类型定义可复用（`packages/schemas`）。
- 宠物花园需要 40 个精灵同屏动画且保证 ≥50fps（NFR），DOM + CSS 动画在教室一体机这种中低端硬件上不可靠，**用 PixiJS（WebGL）渲染花园场景**；UI 层（题目卡片、原因选择）仍用 React DOM，两层通过事件桥接。
- 宠物动画资源用 Spine 或序列帧图集，避免 Lottie 在低端 Android WebView 上的性能问题。

### ADR-05：实时通信用 WebSocket + Redis Pub/Sub

投票数据需要实时推给教师端看板（NFR：≤1s）。用 FastAPI 原生 WebSocket，多实例间通过 Redis Pub/Sub 广播。会话状态（哪些学生已提交）存 Redis Hash，作为权威内存态，异步落库。

不用 SSE 的原因：大屏侧也需要上行（心跳、投票提交的低延迟确认），双向连接更自然。

### ADR-06：大屏采用离线优先架构

见第 8 章。这是被 NFR-AV-01~05 强制的，学校网络不稳定是常态。

---

## 3. 数据模型

### 3.1 组织与身份

```sql
CREATE TABLE tenants (            -- 租户 = 学校
    id            UUID PRIMARY KEY,
    name          TEXT NOT NULL,
    region_code   TEXT,
    textbook_ver  TEXT NOT NULL,          -- 'renjiao' | 'beishida' | ...
    settings      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE classes (
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    name          TEXT NOT NULL,          -- '初二(3)班'
    grade         SMALLINT NOT NULL,      -- 7 | 8 | 9
    enroll_year   SMALLINT NOT NULL,
    seat_layout   JSONB NOT NULL,         -- {rows:6, cols:7, seats:[{row,col,student_id}]}
    archived_at   TIMESTAMPTZ
);

CREATE TABLE students (
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    class_id      UUID NOT NULL REFERENCES classes(id),
    name          TEXT NOT NULL,          -- 应用层加密，见 §10.2
    student_no    TEXT,
    seat_no       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active'
);
CREATE UNIQUE INDEX ON students(class_id, seat_no) WHERE status = 'active';

CREATE TABLE teachers (
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    name          TEXT NOT NULL,
    phone         TEXT NOT NULL,          -- 加密存储 + phone_hash 用于查询
    phone_hash    TEXT NOT NULL,
    role          TEXT NOT NULL           -- 'teacher'|'group_leader'|'school_admin'
);

CREATE TABLE teacher_classes (
    teacher_id    UUID NOT NULL REFERENCES teachers(id),
    class_id      UUID NOT NULL REFERENCES classes(id),
    subject       TEXT NOT NULL DEFAULT 'math',
    PRIMARY KEY (teacher_id, class_id, subject)
);

CREATE TABLE screen_devices (         -- 大屏设备，FR-SYS-04
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    class_id      UUID NOT NULL REFERENCES classes(id),
    device_token  TEXT NOT NULL UNIQUE,  -- 长效令牌，权限受限
    pairing_code  TEXT,                  -- 一次性配对码
    last_seen_at  TIMESTAMPTZ,
    app_version   TEXT
);
```

**多租户隔离策略**：所有业务表带 `tenant_id`，通过 PostgreSQL 行级安全（RLS）+ 每请求设置 `SET LOCAL app.tenant_id` 强制隔离。应用层遗漏 WHERE 条件时由数据库兜底，这是防止跨校数据泄露的最后一道防线。

### 3.2 知识图谱

```sql
CREATE TABLE kg_nodes (
    id              UUID PRIMARY KEY,
    layer           TEXT NOT NULL,        -- 'L1' 平台母版 | 'L2' 校本层
    tenant_id       UUID REFERENCES tenants(id),   -- L1 为 NULL
    parent_id       UUID REFERENCES kg_nodes(id),  -- L2 节点挂载到 L1 节点
    code            TEXT NOT NULL,
    name            TEXT NOT NULL,
    node_type       TEXT NOT NULL,        -- 'knowledge_point'|'method'|'misconception'
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    domain          TEXT,                 -- '数与代数'|'图形与几何'|'统计与概率'|'综合与实践'
    grade           SMALLINT,
    semester        SMALLINT,
    textbook_ver    TEXT,
    difficulty_base SMALLINT CHECK (difficulty_base BETWEEN 1 AND 5),
    core_literacy   TEXT[] NOT NULL DEFAULT '{}',
    exam_weight     NUMERIC(3,2) DEFAULT 0.5,   -- 中考权重，用于讲评优先级
    description     TEXT,
    embedding       vector(1024),         -- pgvector，用于对齐消歧
    status          TEXT NOT NULL DEFAULT 'draft',
    version         INT NOT NULL DEFAULT 1,
    created_by      UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON kg_nodes(layer, COALESCE(tenant_id,'00000000-0000-0000-0000-000000000000'::uuid), code);
CREATE INDEX ON kg_nodes USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON kg_nodes(grade, semester, textbook_ver) WHERE status = 'published';

CREATE TABLE kg_relations (
    id                  UUID PRIMARY KEY,
    tenant_id           UUID REFERENCES tenants(id),
    kp_id               UUID NOT NULL REFERENCES kg_nodes(id),
    prerequisite_kp_id  UUID NOT NULL REFERENCES kg_nodes(id),  -- 通用"目标节点"
    relation_type       TEXT NOT NULL,   -- PREREQUISITE_OF|PART_OF|RELATED_TO|COMMON_ERROR_IN|METHOD_FOR
    weight              NUMERIC(3,2) DEFAULT 1.0,
    source              TEXT NOT NULL,   -- 'l1_seed'|'llm_suggested'|'teacher_manual'
    confirmed_by        UUID,            -- 教师确认人，FR-KG-04
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON kg_relations(kp_id, prerequisite_kp_id, relation_type);
CREATE INDEX ON kg_relations(prerequisite_kp_id, relation_type);

CREATE TABLE kg_versions (            -- FR-KG-07 版本管理
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL,
    version_no    INT NOT NULL,
    snapshot      JSONB NOT NULL,      -- 该版本的全量节点与关系快照
    published_by  UUID NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_log    TEXT
);
```

**关于 `kg_relations` 的列命名**：为了让"前置关系"这一最常用查询直观，列名沿用 `kp_id → prerequisite_kp_id` 语义（A 的前置是 B）。对于其他关系类型，语义为「源 → 目标」。若后续关系类型继续增加，应重命名为 `source_id / target_id` 以免误导。

### 3.3 教学资料与题目

```sql
CREATE TABLE materials (              -- 教师上传的教学资料
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL,
    uploader_id   UUID NOT NULL REFERENCES teachers(id),
    file_name     TEXT NOT NULL,
    file_type     TEXT NOT NULL,       -- pdf|docx|pptx|image
    storage_uri   TEXT NOT NULL,
    file_hash     TEXT NOT NULL,       -- 去重
    parse_status  TEXT NOT NULL,       -- pending|processing|success|failed|partial
    parse_error   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE material_fragments (     -- 资料切片，可作为讲解素材 FR-KG-09
    id            UUID PRIMARY KEY,
    material_id   UUID NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    tenant_id     UUID NOT NULL,
    seq_no        INT NOT NULL,
    page_no       INT,
    title         TEXT,
    content_text  TEXT,
    preview_uri   TEXT,                -- 该片段的截图，用于课堂直接展示
    embedding     vector(1024)
);

CREATE TABLE fragment_kp_links (
    fragment_id   UUID NOT NULL REFERENCES material_fragments(id) ON DELETE CASCADE,
    kp_id         UUID NOT NULL REFERENCES kg_nodes(id),
    confidence    NUMERIC(3,2),
    source        TEXT NOT NULL,       -- 'llm_suggested'|'teacher_manual'
    confirmed_by  UUID,                -- NULL = 未确认，不参与检索
    PRIMARY KEY (fragment_id, kp_id)
);

CREATE TABLE assignments (            -- 一次作业
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    class_id        UUID NOT NULL REFERENCES classes(id),
    teacher_id      UUID NOT NULL REFERENCES teachers(id),
    title           TEXT NOT NULL,
    assign_date     DATE NOT NULL,
    source_images   JSONB NOT NULL DEFAULT '[]',  -- [{uri, order, preprocessed_uri}]
    parse_status    TEXT NOT NULL DEFAULT 'pending',
    review_status   TEXT NOT NULL DEFAULT 'draft', -- draft|reviewing|published
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON assignments(class_id, assign_date DESC);

CREATE TABLE problems (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    assignment_id   UUID NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    seq_no          INT NOT NULL,        -- 题号，大屏显示用
    problem_type    TEXT NOT NULL,       -- choice|blank|solution|proof|construction
    stem_text       TEXT,                -- 含内联 LaTeX，用 $...$ 包裹
    stem_image_uri  TEXT NOT NULL,       -- 原图裁切区域，永远保留作为权威呈现
    figure_uris     TEXT[] DEFAULT '{}', -- 几何图形，不做矢量化
    options         JSONB,               -- 选择题选项
    score           NUMERIC(4,1),
    difficulty      SMALLINT CHECK (difficulty BETWEEN 1 AND 5),
    est_time_sec    INT,
    solution_outline JSONB,              -- [{step, description}] 解题要点
    ocr_confidence  NUMERIC(3,2),        -- 低于阈值高亮提示教师 FR-HW-10
    review_status   TEXT NOT NULL DEFAULT 'auto',  -- auto|edited|confirmed
    embedding       vector(1024),        -- 题目查重 FR-HW-12
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON problems(assignment_id, seq_no);
CREATE INDEX ON problems USING hnsw (embedding vector_cosine_ops);

CREATE TABLE problem_kp_links (
    problem_id    UUID NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    kp_id         UUID NOT NULL REFERENCES kg_nodes(id),
    weight        NUMERIC(3,2) NOT NULL DEFAULT 1.0,  -- 主考点 1.0，次要考点更低
    confidence    NUMERIC(3,2),
    source        TEXT NOT NULL,
    confirmed_by  UUID,
    PRIMARY KEY (problem_id, kp_id)
);
```

**设计要点：`stem_image_uri` 永不丢弃。** 大屏和教师端展示题目时，**默认显示原图裁切**，识别出的文本仅用于检索、标注与知识点关联。这样即使 OCR 有误，学生看到的题目也永远是真实的。这条决策把 OCR 质量从"正确性问题"降级为"检索质量问题"，大幅降低了整体风险。

### 3.4 互动投票

```sql
CREATE TABLE vote_sessions (          -- 一次投票窗口
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    class_id        UUID NOT NULL REFERENCES classes(id),
    assignment_id   UUID NOT NULL REFERENCES assignments(id),
    opened_by       UUID NOT NULL REFERENCES teachers(id),
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    max_choices     SMALLINT NOT NULL DEFAULT 3,   -- FR-VOTE-04
    status          TEXT NOT NULL DEFAULT 'open'   -- open|closed
);
CREATE UNIQUE INDEX ON vote_sessions(class_id) WHERE status = 'open';  -- 一个班同时只能有一个开放窗口

CREATE TABLE student_choices (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    session_id      UUID NOT NULL REFERENCES vote_sessions(id) ON DELETE CASCADE,
    student_id      UUID NOT NULL REFERENCES students(id),
    problem_id      UUID NOT NULL REFERENCES problems(id),
    reason_code     TEXT NOT NULL,      -- 见 PRD §3.3.3 的 7 类枚举
    note            TEXT,               -- 可选补充，≤30 字
    source          TEXT NOT NULL,      -- 'bigscreen'|'mobile'
    client_ts       TIMESTAMPTZ NOT NULL,  -- 客户端时间，离线同步冲突判定用
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON student_choices(session_id, student_id, problem_id);
CREATE INDEX ON student_choices(session_id, problem_id);

CREATE TABLE student_submissions (    -- 提交记录，用于参与率与幂等
    session_id      UUID NOT NULL REFERENCES vote_sessions(id) ON DELETE CASCADE,
    student_id      UUID NOT NULL REFERENCES students(id),
    submitted_at    TIMESTAMPTZ NOT NULL,
    client_ts       TIMESTAMPTZ NOT NULL,
    choice_count    SMALLINT NOT NULL,   -- 0 表示"无补充需求" FR-VOTE-12
    idempotency_key TEXT NOT NULL,
    PRIMARY KEY (session_id, student_id)
);

CREATE TABLE pets (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    student_id      UUID NOT NULL UNIQUE REFERENCES students(id),
    species         TEXT NOT NULL,       -- 'cube'|'sphere'|'cone'|'triangle'|...
    nickname        TEXT NOT NULL,
    nickname_status TEXT NOT NULL DEFAULT 'pending',  -- 教师审核 FR-PET-02
    appearance      JSONB NOT NULL DEFAULT '{}',      -- 颜色、配饰
    level           SMALLINT NOT NULL DEFAULT 1,
    energy          INT NOT NULL DEFAULT 0,
    streak_days     SMALLINT NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pet_energy_events (      -- 能量流水，可审计可回滚
    id            BIGSERIAL PRIMARY KEY,
    pet_id        UUID NOT NULL REFERENCES pets(id),
    event_type    TEXT NOT NULL,       -- 'vote_submit'|'correction_done'|'streak'|'kp_mastered'
    delta         INT NOT NULL,
    ref_id        UUID,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.5 讲评决策

```sql
CREATE TABLE review_plans (           -- 每次投票关闭后生成的讲评方案
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    session_id      UUID NOT NULL UNIQUE REFERENCES vote_sessions(id),
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    weights         JSONB NOT NULL,     -- 生成时使用的权重快照，保证可复现
    items           JSONB NOT NULL      -- 见下方结构
);

CREATE TABLE review_records (         -- 教师标记"已讲" FR-DEC-06
    id            UUID PRIMARY KEY,
    plan_id       UUID NOT NULL REFERENCES review_plans(id),
    problem_id    UUID NOT NULL REFERENCES problems(id),
    taught        BOOLEAN NOT NULL,
    duration_sec  INT,
    teacher_note  TEXT,
    marked_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`review_plans.items` 结构：

```json
[{
  "problem_id": "...",
  "seq_no": 7,
  "priority_score": 0.87,
  "rank": 1,
  "vote_count": 19,
  "vote_ratio": 0.475,
  "reason_breakdown": {"NO_IDEA": 14, "STUCK_MIDWAY": 4, "CARELESS": 1},
  "dominant_reason": "NO_IDEA",
  "kp_ids": ["..."],
  "strategy": {
    "type": "TEACH_FROM_PREREQUISITE",
    "headline": "从前置知识点回讲",
    "detail": "14 人完全没思路。该题主考「一元二次方程根与系数关系」，其前置「配方法」在近两周作业中也是高频归因点，建议先花 3 分钟回顾配方法。",
    "prerequisite_kps": [{"id": "...", "name": "配方法", "depth": 1}],
    "suggested_minutes": 8
  },
  "materials": [{"fragment_id": "...", "title": "配方法专题", "preview_uri": "..."}]
}]
```

---

## 4. 知识图谱构建管线

对应 FR-KG-02~05。

```
上传文件
   │
   ▼
[1] 落对象存储 + 计算 file_hash 去重
   │
   ▼
[2] 文档解析（按类型分发）
   │   PDF  → PyMuPDF 提页文本 + 页面截图
   │   PPTX → python-pptx 提每页文本 + 渲染截图
   │   DOCX → python-docx 提段落与标题层级
   │   图片 → 直接进多模态识别
   ▼
[3] 语义切片
   │   按标题层级/页边界切分，合并过短片段，目标 300–800 字/片
   │   每片保留 preview_uri（截图），供课堂直接展示
   ▼
[4] LLM 抽取知识点候选  ← 输入：片段文本 + 该年级学期的 L1 候选知识点清单
   │   输出：[{kp_name, evidence_span, confidence}]
   │   关键：提示中注入 L1 清单，引导模型"选择"而非"发明"
   ▼
[5] 与 L1 母版对齐（三级匹配）
   │   ① 精确匹配 name / aliases
   │   ② 向量召回 top-10（pgvector cosine），阈值 0.82
   │   ③ LLM 重排消歧，输出匹配或"无法匹配→建议新建 L2 节点"
   ▼
[6] 关系抽取（仅对新建 L2 节点）
   │   父节点归属 + 与兄弟节点的前置关系候选
   ▼
[7] 写入 staging 表，状态 = suggested
   │
   ▼
[8] 教师审核界面：逐条 通过 / 修改 / 拒绝    ← FR-KG-04 强制人工闸门
   │
   ▼
[9] 确认项写入 kg_nodes / kg_relations / fragment_kp_links（confirmed_by 置值）
   │
   ▼
[10] 教师点击发布 → 生成 kg_versions 快照
```

**为什么第 4 步要注入 L1 清单**：让模型做**分类**（从已知清单里选）而不是**生成**（自由命名）。自由生成会产出「一元二次方程的解法」「解一元二次方程」「一元二次方程求解」三个语义相同的节点，图谱迅速变成垃圾。注入清单后，绝大多数抽取直接落到 L1 节点上，只有真正新颖的内容才走 L2 新建流程。

**第 5 步无法匹配时不自动新建**，而是作为"建议新建"进入教师审核队列。教师可能更愿意把它归并到某个已有节点。

---

## 5. 作业照片解析管线

对应 FR-HW-01~11。

```
教师拍照上传（1–10 张）
   │
   ▼
[1] 客户端预压缩（长边 ≤ 2400px, JPEG q=85）+ 直传对象存储（预签名 URL）
   │   理由：不经过 API 服务器，避免大文件占用 Web 进程
   ▼
[2] 创建 assignment (parse_status=pending) → 投递 Celery 任务 → 立即返回
   │
   ▼
[3] 图像预处理（OpenCV）                              ← FR-HW-03
   │   透视校正（四边形检测）→ 去阴影（背景估计除法）
   │   → 二值化增强 → 方向纠正
   │   产出 preprocessed_uri，教师可预览并手动调整裁切
   ▼
[4] 质量闸门：模糊度（Laplacian 方差）、亮度、倾斜角
   │   不达标 → parse_status=failed + 明确原因 + 重拍指引    ← FR-HW-14
   ▼
[5] 多模态 LLM 版面理解 + 结构化抽取                   ← FR-HW-04/05/06
   │   输入：预处理图（多张，标注页序）
   │   输出（结构化 JSON schema 约束）：
   │     [{seq_no, bbox, problem_type, stem_text(含LaTeX),
   │       options[], figure_bboxes[], score, confidence}]
   │   跨页题目合并规则：题号连续性 + 页尾未闭合检测
   ▼
[6] 并行：PaddleOCR 纯文本提取
   │
   ▼
[7] 交叉校验：LLM 文本 vs OCR 文本 的字符级相似度
   │   相似度 < 0.75 → ocr_confidence 降级 → 教师端高亮    ← FR-HW-10
   ▼
[8] 按 bbox 裁切每题图像 → stem_image_uri / figure_uris
   │   （这是学生最终看到的内容，见 §3.3 设计要点）
   ▼
[9] 知识点标注                                        ← FR-HW-07
   │   输入：题干文本 + 该班当前 KG 版本中本学期的候选知识点
   │   输出：1–3 个 kp_id + weight + confidence
   │   同样是"从清单中选"，不自由生成
   ▼
[10] 难度/用时/易错点/解题要点生成                     ← FR-HW-08
   │   难度基线取自知识点 difficulty_base，LLM 在 ±1 范围内调整
   ▼
[11] 题目查重：embedding 检索本班历史题目，cosine > 0.92 标记相似  ← FR-HW-12
   │
   ▼
[12] 写入 problems，parse_status=success，推送通知教师
   │
   ▼
[13] 教师校对页（对照原图逐题确认）                     ← FR-HW-09
   │   修改 → review_status=edited，修改内容沉淀为评测数据
   ▼
[14] 发布 → assignments.review_status=published        ← FR-HW-11
        大屏才能看到这份作业
```

**幂等性**：Celery 任务以 `assignment_id` 为幂等键，重复投递不产生重复题目。步骤 5、9、10 的 LLM 调用结果按「图片 hash + prompt 版本 + 模型版本」缓存，重试不重复计费。

**部分失败处理**：某一张图片解析失败不导致整份作业失败，`parse_status=partial`，教师端明确标出哪张图有问题，可单独重试或手动补录。

---

## 6. 大屏交互与实时聚合

### 6.1 状态机

```
        教师开启投票窗口
IDLE ──────────────────────► GARDEN (投票态·花园)
 ▲                              │
 │                              │ 学生点击宠物 + 座位号确认
 │                              ▼
 │                          PICKING (题目卡片墙，多选 ≤3)
 │                              │
 │                              │ 每选一题
 │                              ▼
 │                          REASONING (归因选择，7 选 1)
 │                              │
 │                     ┌────────┴────────┐
 │                     │ 继续选下一题     │ 提交
 │                     ▼                 ▼
 │                  PICKING          SUBMITTED (宠物返回花园，亮星)
 │                                       │
 │                                       └──► GARDEN（等待下一位）
 │   教师关闭投票窗口
 └────────────────── RESULT (聚合结果动画) ──┘
```

**分区并发（FR-VOTE-08）**：`GARDEN` 态下屏幕逻辑上划分为 N 个操作区（≥86 寸为 3 区，否则 2 区）。每个区维护独立的状态机实例。触摸事件按 X 坐标路由到对应分区。花园本身是共享的背景层；一旦某分区有学生进入 `PICKING`，该分区上层渲染独立的 React 子树。

宠物被占用时在花园中标记为"使用中"，防止两个分区同时操作同一只宠物。

### 6.2 实时数据流

```
大屏 ──WS──► interaction 服务 ──► Redis (会话权威态)
                    │                    │
                    │                    ├──► Pub/Sub 广播
                    │                    │         │
                    │                    │         ▼
                    │                    │    其他 API 实例 ──WS──► 教师端看板
                    │                    │
                    └──► 异步批量落库 (每 5s 或每 20 条)
```

Redis 中的会话结构：

```
vote:session:{session_id}:submitted     → Set<student_id>
vote:session:{session_id}:tally         → Hash<"{problem_id}:{reason_code}", count>
vote:session:{session_id}:pending_flush → List<choice_json>
```

**为什么 Redis 是权威态而非 PG**：投票窗口内会有 40 人在几分钟内密集提交，每次提交都要立刻反映到教师端的实时看板。走 PG 会产生大量小事务与聚合查询。Redis 内存计数 + 批量落库，PG 只承担持久化职责。会话关闭时做一次强制 flush 与对账（Redis 计数 vs PG 记录数），不一致则以 PG 明细为准重算。

### 6.3 身份确认（FR-VOTE-02）

学生无账号密码。身份 = 点击宠物 + 输入座位号后两位。

这不是安全机制，是**防误触机制**——目标是防止小明手滑点到小红的宠物，不是防止小明故意冒充小红。在教室这个物理场景里，同学之间互相看得见，社会约束足够。引入密码会让 20 秒的操作目标彻底不可能达成。

被冒充的后果也有限（一条错误的选题数据），且教师端可见提交明细，异常可追溯。这是有意识的取舍，需在评审时确认。

---

## 7. 讲评优先级算法

对应 FR-DEC-01~03。

### 7.1 评分公式

对投票会话中的每道题 $p$：

```
Priority(p) = w₁·VoteSignal(p) + w₂·KPImportance(p) + w₃·HistoryPenalty(p)
```

**① 投票信号**（核心项）——不是简单人数，而是按归因加权：

```
VoteSignal(p) = Σ_r ( count(p, r) · reasonWeight(r) ) / (classSize · maxReasonWeight)
```

| 归因 | 权重 | 依据 |
| --- | --- | --- |
| `NO_IDEA` | 1.00 | 前置知识缺失，不讲则后续内容全部崩塌 |
| `WRONG_UNKNOWN` | 0.90 | 存在错误概念，不纠正会固化 |
| `STUCK_MIDWAY` | 0.80 | 关键步骤缺失，讲解性价比最高 |
| `SLOW` | 0.50 | 方法优化，重要但不紧急 |
| `WANT_BETTER` | 0.40 | 已掌握，属于拔高 |
| `WANT_EXTEND` | 0.30 | 可放课后 |
| `CARELESS` | 0.15 | 非知识性问题，不值得占用集体时间 |

**② 知识点重要度**：

```
KPImportance(p) = Σ_k ( linkWeight(p,k) · [α·examWeight(k) + β·normFanout(k)] ) / Σ_k linkWeight(p,k)
```

`normFanout(k)` = 该知识点在 KG 中作为前置节点的出度（归一化）。**出度高 = 后续很多知识点依赖它 = 现在不补以后更麻烦。** 这是知识图谱在本产品中最直接的价值兑现点，也是"零人选择但客观重要"（FR-DEC-08）的判定依据。

默认 α=0.6, β=0.4。

**③ 历史惩罚**：同一知识点在近 14 天内已讲过则扣分（`review_records`），避免重复。

默认权重 w₁=0.65, w₂=0.30, w₃=-0.05，教师可调（FR-DEC-03），调整后的权重快照存入 `review_plans.weights` 保证结果可复现。

### 7.2 讲评策略生成

由**主导归因**（占比最高的 reason_code）映射到策略类型：

| 主导归因 | 策略 | 系统额外提供 |
| --- | --- | --- |
| `NO_IDEA` | `TEACH_FROM_PREREQUISITE` | 沿 KG 回溯 1–2 层前置知识点（ADR-01 的递归 CTE），列出建议回讲的起点 |
| `STUCK_MIDWAY` | `FOCUS_ON_KEY_STEP` | 从 `solution_outline` 中定位关键转折步骤 |
| `WRONG_UNKNOWN` | `ERROR_ANALYSIS` | 调出该知识点关联的 `misconception` 节点 |
| `SLOW` | `OPTIMIZE_METHOD` | 调出关联的 `METHOD_FOR` 节点 |
| `WANT_BETTER` | `MULTI_SOLUTION` | 标注"建议控制在 3 分钟" |
| `WANT_EXTEND` | `EXTENSION` | 标注"建议课后处理" |
| `CARELESS` | `SKIP_CLASS_WIDE` | 建议跳过，转为个人提醒 |

建议时长按 `基础时长(按题型) × 归因系数 × 人数系数` 计算，全部题目的建议时长之和会与教师设定的讲评总时长比对，超出时给出提示。

策略的 `detail` 文案由 LLM 基于结构化数据生成（人数、归因分布、前置知识点历史表现），但**结构化字段本身全部由确定性计算得出**——LLM 只负责把数据写成人话，不负责决策。这样保证优先级排序是可解释、可复现的。

---

## 8. 大屏离线优先设计

对应 NFR-AV-01~05。这是架构中最需要专门设计的部分。

### 8.1 分层缓存

| 层 | 内容 | 时机 |
| --- | --- | --- |
| Service Worker Cache | 应用外壳（JS/CSS/字体/宠物图集） | 首次加载后常驻，版本更新时增量 |
| IndexedDB `screen_data` | 班级信息、座位表、宠物数据、当日题目元数据 | 投票窗口开启时全量预拉 |
| Cache Storage `problem_images` | 全部题目图片 + 图形图片 | 同上，预拉完成前不允许开启投票 |
| IndexedDB `outbox` | 未同步的投票提交 | 提交时立即写入 |

**预拉取闸门**：教师点击"开启投票"后，大屏先完成资源预拉（通常 3–8 秒，显示进度），全部就绪才切换到花园态。宁可让教师多等 5 秒，也不能让学生点开题目时看到加载失败。

### 8.2 提交与同步

```
学生提交
   │
   ▼
写入 IndexedDB outbox（含 idempotency_key = uuid, client_ts）
   │
   ├──► UI 立即反馈成功（乐观提交，本地即真实）
   │
   └──► 尝试通过 WS 发送
          ├─ 成功 → 收到 ACK → 从 outbox 删除
          └─ 失败 → 保留在 outbox，退避重试（1s/2s/5s/15s/60s）
                     网络恢复事件触发立即重试
```

**幂等**：服务端以 `idempotency_key` 去重（`student_submissions.idempotency_key` 唯一约束）。重复上报返回相同结果，不产生重复数据。

**冲突解决**：同一学生在同一会话多次提交（如断网时提交后又修改），以 `client_ts` 最大者为准，服务端整体替换该学生在本会话的全部 choices（先删后插，单事务）。使用客户端时间戳是因为离线期间的真实顺序只有客户端知道；大屏启动时与服务端做一次时钟校准，记录偏移量用于纠正。

**保底导出**：若网络长时间不恢复（> 24h）且 outbox 非空，大屏提供"导出投票数据"，生成二维码或本地文件供教师带走，避免数据永久丢失。

### 8.3 崩溃恢复

大屏应用由外壳（Android 端为原生壳，Windows 端为 Electron 或看板模式浏览器 + 守护进程）监控，崩溃后 5 秒内重启。重启后从 IndexedDB 恢复会话状态与 outbox，回到花园态。已提交但未同步的数据不丢失。

---

## 9. API 设计

REST + WebSocket。全部路径前缀 `/api/v1`。认证：教师与管理员用 JWT（Bearer），大屏设备用长效 `device_token`（权限限定为：读取本班当日已发布作业、写入本班投票）。

### 9.1 主要端点

**知识图谱**

```
POST   /kg/materials                        上传资料（返回预签名 URL）
POST   /kg/materials/{id}/parse             触发解析
GET    /kg/materials/{id}/suggestions       获取待审核的知识点建议
POST   /kg/suggestions/batch-review         批量通过/修改/拒绝
GET    /kg/nodes?grade=8&semester=1         查询知识点
POST   /kg/nodes                            新建校本知识点(L2)
POST   /kg/relations                        新建关系
GET    /kg/nodes/{id}/prerequisites?depth=2 前置链查询
POST   /kg/versions                         发布版本
GET    /kg/graph?domain=...&focus={node_id} 图谱可视化数据
```

**作业**

```
POST   /assignments                              创建作业（含图片直传信息）
GET    /assignments/{id}                         详情（含解析状态）
GET    /assignments/{id}/problems                题目列表
PATCH  /problems/{id}                            校对修改
POST   /problems/{id}/kp-links                   调整知识点标注
POST   /problems/{id}/split | /merge              拆分/合并题目
POST   /assignments/{id}/publish                 发布到大屏
GET    /assignments/{id}/parse-events            解析进度（SSE）
```

**投票**

```
POST   /vote-sessions                            开启投票窗口
POST   /vote-sessions/{id}/close                 关闭
GET    /vote-sessions/{id}/bootstrap             大屏预拉取（班级+座位+宠物+题目+图片清单）
POST   /vote-sessions/{id}/submissions           提交（幂等，支持批量离线补传）
GET    /vote-sessions/{id}/tally                 实时聚合
```

**讲评**

```
GET    /review-plans/by-session/{session_id}     讲评方案
PATCH  /review-plans/{id}/weights                调整权重并重算
GET    /review-plans/{id}/items/{problem_id}/students   下钻学生名单（教师权限）
POST   /review-records                           标记已讲
GET    /reports/class/{class_id}/weekly          周报
```

### 9.2 WebSocket

```
连接：wss://.../ws/vote/{session_id}?token=...

大屏 → 服务端
  {"type":"submit","idempotency_key":"...","student_id":"...",
   "choices":[{"problem_id":"...","reason_code":"NO_IDEA","note":null}],
   "client_ts":"2026-08-15T08:12:33Z"}
  {"type":"heartbeat","screen_id":"...","outbox_size":0}

服务端 → 大屏
  {"type":"ack","idempotency_key":"...","status":"accepted"}
  {"type":"session_closed"}
  {"type":"tally_snapshot", ...}          // 用于结果动画

服务端 → 教师端
  {"type":"tally_update","problem_id":"...","reason_code":"NO_IDEA",
   "count":14,"submitted_students":27,"class_size":40}
```

### 9.3 错误约定

统一错误体，业务错误码与 HTTP 状态分离，便于客户端精确处理：

```json
{"error": {"code": "ASSIGNMENT_NOT_PUBLISHED",
           "message": "该作业尚未发布，学生不可见",
           "details": {"assignment_id": "..."}}}
```

---

## 10. 安全与隐私实现

对应 PRD 第 5 章。

### 10.1 访问控制

| 主体 | 凭证 | 权限边界 |
| --- | --- | --- |
| 教师 | JWT（2h）+ Refresh Token（30d） | 仅其任教班级（`teacher_classes`） |
| 教研组长 | JWT | 本校本学科全部班级（只读 + 图谱审核） |
| 校管理员 | JWT | 本校组织管理，**不可见**学生归因明细 |
| 大屏设备 | `device_token` | 仅绑定班级；只能读当日已发布作业、写投票；**不可读学生姓名以外的任何信息，不可读归因聚合** |
| 学生移动端 | 短期会话票据（当次投票窗口有效） | 仅提交自己的投票 |

大屏设备的权限必须严格收窄——它是物理上最不可控的终端（放在教室，任何人可接触）。即使 token 泄露，攻击者也只能提交垃圾投票，拿不到学生名单或历史数据。

### 10.2 数据保护

- **传输**：全链路 TLS 1.3；大屏与服务端之间强制证书校验。
- **静态加密**：`students.name`、`teachers.phone` 使用应用层字段加密（AES-256-GCM，密钥由 KMS 管理）。`phone_hash`（HMAC）用于登录查询，避免解密全表扫描。
- **对象存储**：作业照片使用私有桶 + 短时效预签名 URL（5 分钟），禁止公开访问。
- **LLM 调用前脱敏**（PRIV-09）：送入模型的图片仅为题面裁切区域；若原图边缘含学生姓名/班级信息，预处理阶段的裁切已自然去除；文本请求中不含任何学生标识。
- **审计日志**：对学生归因明细的每一次下钻查询（FR-DEC-04）记录操作者、时间、范围，保留 1 年。
- **生命周期**：作业照片默认 1 学年后自动删除（PRIV-05），由定时任务执行，删除对象存储文件与数据库记录。

### 10.3 内容安全

- 宠物昵称：敏感词过滤 + 教师审核开关（FR-PET-02），未通过审核前显示默认名。
- 学生补充说明（≤30 字）：同样过滤，且仅教师可见，不上大屏。

---

## 11. 部署与运维

### 11.1 部署形态

**形态 A — SaaS 多租户（默认）**

```
云负载均衡 → Nginx (2) → FastAPI (4–8, 按 CPU 自动伸缩)
                       → Celery Worker (解析队列 2–4，GPU 可选)
              PostgreSQL 主从（主 1 + 只读从 1）
              Redis 主从 / Sentinel
              对象存储（OSS/S3）
```

**形态 B — 私有化（PRIV-10）**

单机 Docker Compose 全家桶（PG + Redis + MinIO + API + Worker），最低配置 8C16G + 500G。LLM 能力二选一：

- 走公网调用商用 API（需学校允许出网）
- 部署本地开源多模态模型（需 GPU，效果需单独评测，可能只支持降级模式）

私有化形态必须在 M5 之前完成一次完整演练，因为很多公立学校对数据出校有硬性要求。

### 11.2 可观测性

| 类别 | 指标 |
| --- | --- |
| 业务 | 每日作业解析量/成功率、教师校对修改率（模型质量的核心先行指标）、班级参与率、讲评命中率 |
| 管线 | 各阶段耗时 P50/P95、LLM 调用次数/token/成本（按租户归集）、缓存命中率 |
| 大屏 | 在线设备数、心跳丢失、outbox 积压量、崩溃重启次数、投票操作耗时分布 |
| 系统 | API 延迟、错误率、DB 连接池、队列积压、Redis 内存 |

告警重点在**上课时段**：7:00–18:00 内 API 错误率 > 1% 或大屏心跳丢失 > 10% 立即告警。课堂事故的影响远大于夜间故障。

### 11.3 发布策略

- 大屏应用版本更新**不在上课时段推送**，更新窗口限定 19:00–6:00，且必须支持回滚。一个坏版本推到 50 个教室就是 50 起教学事故。
- 后端蓝绿部署；数据库迁移遵循「先加后用、先停用后删」的双阶段，保证新旧版本可共存。
- LLM prompt 与模型版本作为配置项灰度，先在 1 个班验证再全量。

---

## 12. 测试策略

| 层级 | 范围 | 关键点 |
| --- | --- | --- |
| 单元测试 | 优先级算法、归因权重计算、前置链查询、幂等与冲突解决逻辑 | 算法必须有确定性测试用例，覆盖极端分布（全员选同一题、无人投票、并列同分） |
| 集成测试 | 解析管线各阶段、API 契约、RLS 租户隔离 | **RLS 隔离必须有专门测试**：伪造 tenant_id 访问他校数据必须失败 |
| 端到端 | PRD 的四个场景 A/B/C/D 全流程 | 每个里程碑的验收标准 |
| 解析质量评测 | 固定黄金集（≥200 道真实作业题，覆盖三个年级、印刷体与手写体） | 切题准确率、公式识别可用率、知识点标注准确率；每次模型/prompt 变更强制回归 |
| 离线容错 | 断网/弱网/丢包/崩溃恢复 | 用 Chrome DevTools 网络限速 + 真机断网演练；outbox 数据零丢失是硬性要求 |
| 性能 | 40 只宠物同屏帧率、50 班并发投票、单张作业解析耗时 | 在**真实教室一体机**上测帧率，开发机上的数据无参考价值 |
| 可用性测试 | 真实学生完成投票的耗时与困惑点 | M3 前必须做，20 秒目标不是可以拍脑袋的 |

**教师校对修改率**是最重要的长期质量指标——它直接来自真实使用，不需要额外标注成本，且教师的每次修改都自动成为下一轮评测的黄金集样本。

---

## 13. 待定技术决策

| # | 问题 | 影响 | 建议决策时点 |
| --- | --- | --- | --- |
| T1 | 多模态模型具体选型与是否需要私有部署 | 成本、私有化可行性、解析质量 | M0 结束前 |
| T2 | 手写体作业的识别质量是否达标；不达标时降级方案的教师接受度 | 模块二的可行性 | M0 |
| T3 | 大屏外壳技术：Android 原生壳 vs Windows Electron vs 纯看板浏览器 | 崩溃恢复与自动更新能力 | M3 前，取决于试点学校的一体机型号 |
| T4 | 宠物美术资源：Spine 骨骼动画 vs 序列帧 | 包体大小、低端设备性能、美术成本 | M3 前 |
| T5 | 是否引入 ClickHouse 做行为分析 | 目前 PG 足够；数据量增长后再评估 | M5 后 |
| T6 | 学生移动端形态：微信小程序 vs PWA | 小程序有分发优势但受平台限制；PWA 无需审核 | P1 排期时 |
| T7 | L1 母版的知识点编码体系是自建还是对接已有标准 | 跨系统互通性 | M1 前 |

---

## 附录：关键约束速查

工程实现中最容易被"优化"掉、但绝不能妥协的几条：

1. **识别结果未经教师确认，不得对学生可见**（FR-HW-11）。数学题一个符号错误就毁掉整道题。
2. **题目在大屏上默认显示原图裁切**（§3.3），识别文本只用于检索与标注。
3. **知识点抽取与标注必须"从清单中选"，不自由生成**（§4、§5），否则图谱迅速劣化。
4. **大屏断网必须可完整投票**（§8），学校网络不稳定是常态而非异常。
5. **大屏公开视图不显示学生姓名**（FR-VOTE-10），这是产品能拿到真实数据的前提。
6. **宠物能量不与答题正确率挂钩**（FR-PET-05），否则弱生宠物永远长不大，产品对最需要帮助的学生失去意义。
7. **讲评优先级的排序由确定性算法计算，LLM 只负责生成解释文案**（§7.2），保证可解释、可复现。
8. **大屏设备 token 权限最小化**（§10.1），它是物理上最不可控的终端。
