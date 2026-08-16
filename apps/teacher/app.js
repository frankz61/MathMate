// @ts-check
/* MathMate 教师控制台:作业录入 / 讲评看板 / 学生与宠物 */

const API = "/api/v1";
const $ = (id) => /** @type {any} */ (document.getElementById(id));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const REASON = { NO_IDEA: "完全没思路", STUCK_MIDWAY: "卡住了", WRONG_UNKNOWN: "错不知因",
                 SLOW: "太慢", WANT_BETTER: "想看更好方法", WANT_EXTEND: "想看拓展", CARELESS: "粗心" };

/* ---------------- 极简 LaTeX(与大屏同一套规则) ---------------- */
const SYM = { "\\times":"×","\\div":"÷","\\pm":"±","\\cdot":"·","\\le":"≤","\\leq":"≤","\\ge":"≥","\\geq":"≥",
  "\\ne":"≠","\\neq":"≠","\\approx":"≈","\\sim":"∽","\\cong":"≌","\\circ":"°","\\angle":"∠","\\triangle":"△",
  "\\parallel":"∥","\\perp":"⊥","\\odot":"⊙","\\alpha":"α","\\beta":"β","\\theta":"θ","\\pi":"π","\\Delta":"Δ",
  "\\Rightarrow":"⇒","\\rightarrow":"→","\\to":"→","\\because":"∵","\\therefore":"∴","\\infty":"∞",
  "\\ldots":"…","\\cdots":"⋯","\\dots":"…","\\in":"∈","\\textless":"<","\\textgreater":">","\\%":"%",
  "\\{":"{","\\}":"}","\\left":"","\\right":"","\\displaystyle":"","\\,":" ","\\;":" ","\\!":"",
  "\\quad":"  ","\\qquad":"    ","\\begin":"","\\end":"","\\cases":"" };

function tex(src) {
  let out = "", i = 0;
  const readCmd = () => { const s = i; i++; while (i < src.length && /[a-zA-Z]/.test(src[i])) i++;
                          if (i === s + 1) i++; return src.slice(s, i); };
  const readGroup = () => {
    while (src[i] === " ") i++;
    if (src[i] === "{") { let d = 1; const s = ++i;
      while (i < src.length && d > 0) { if (src[i] === "{") d++; else if (src[i] === "}") d--; i++; }
      return src.slice(s, i - 1); }
    if (src[i] === "\\") return readCmd();
    return src[i++] ?? "";
  };
  while (i < src.length) {
    const c = src[i];
    if (c === "\\") {
      const cmd = readCmd();
      if (cmd === "\\frac" || cmd === "\\dfrac" || cmd === "\\tfrac") {
        const a = readGroup(), b = readGroup();
        out += `<span class="frac"><span class="fr-n">${tex(a)}</span><span class="fr-d">${tex(b)}</span></span>`;
      } else if (cmd === "\\sqrt") out += `<span class="sqrt">√<span class="rr">${tex(readGroup())}</span></span>`;
      else if (cmd === "\\text" || cmd === "\\mathrm") out += esc(readGroup());
      else if (cmd in SYM) out += SYM[cmd];
      else out += esc(cmd.slice(1));
    } else if (c === "^" || c === "_") {
      i++; const inner = tex(readGroup());
      if (inner) out += c === "^" ? `<sup>${inner}</sup>` : `<sub>${inner}</sub>`;
    } else if (c === "{" || c === "}" || c === "&") i++;
    else if (c === "~") { out += " "; i++; }
    else { out += esc(c); i++; }
  }
  return out;
}
const plain = (s) => esc(String(s).replace(/\\(?:emph|textbf|text|mathrm)\{([^{}]*)\}/g, "$1")
  .replace(/\\\\/g, " ").replace(/\\[a-zA-Z]+\s?/g, ""));
const math = (s) => !s ? "" : String(s).split(/(\$\$[\s\S]*?\$\$|\$[^$\n]*\$)/g).map((p) => {
  if (p.length >= 4 && p.startsWith("$$") && p.endsWith("$$")) return `<span class="math">${tex(p.slice(2,-2))}</span>`;
  if (p.length >= 2 && p[0] === "$" && p.endsWith("$")) return `<span class="math">${tex(p.slice(1,-1))}</span>`;
  return plain(p);
}).join("");

/* ---------------- 宠物形象(与大屏同一套) ---------------- */
function petSVG(species, hue) {
  const main = `hsl(${hue} 62% 60%)`, dark = `hsl(${hue} 55% 44%)`, light = `hsl(${hue} 70% 72%)`;
  const face = `<ellipse cx="41" cy="55" rx="5" ry="6" fill="#fff"/><ellipse cx="59" cy="55" rx="5" ry="6" fill="#fff"/>
    <circle cx="42" cy="56" r="2.6" fill="#1a1d28"/><circle cx="60" cy="56" r="2.6" fill="#1a1d28"/>
    <path d="M44 67 Q50 72 56 67" stroke="#1a1d28" stroke-width="2.2" fill="none" stroke-linecap="round"/>`;
  let body;
  switch (species) {
    case "sphere": body = `<circle cx="50" cy="55" r="34" fill="${main}"/>`; break;
    case "cone": body = `<ellipse cx="50" cy="80" rx="32" ry="9" fill="${dark}"/><path d="M50 16 L80 80 Q50 89 20 80 Z" fill="${main}"/>`; break;
    case "cylinder": body = `<path d="M20 32 h60 v46 a30 9 0 0 1 -60 0 Z" fill="${main}"/><ellipse cx="50" cy="32" rx="30" ry="9" fill="${light}"/>`; break;
    case "prism": body = `<path d="M50 18 L82 37 L82 73 L50 92 L18 73 L18 37 Z" fill="${main}"/><path d="M50 18 L82 37 L50 56 L18 37 Z" fill="${light}" opacity=".7"/>`; break;
    case "pyramid": body = `<path d="M50 16 L86 82 L50 82 Z" fill="${dark}"/><path d="M50 16 L14 82 L50 82 Z" fill="${main}"/>`; break;
    default: body = `<path d="M50 14 L84 32 L50 50 L16 32 Z" fill="${light}"/><path d="M16 32 L50 50 L50 88 L16 70 Z" fill="${main}"/><path d="M84 32 L50 50 L50 88 L84 70 Z" fill="${dark}"/>`;
  }
  return `<svg viewBox="0 0 100 100" aria-hidden="true">${body}${face}</svg>`;
}

/* ---------------- 基础 ---------------- */
async function api(p, o) {
  const r = await fetch(API + p, { headers: o?.body instanceof FormData ? {} : { "Content-Type": "application/json" }, ...o });
  if (!r.ok) { let d = null; try { d = (await r.json()).detail; } catch {}
                const e = new Error(d?.message || `HTTP ${r.status}`); /** @type{any} */(e).detail = d; throw e; }
  return r.status === 204 ? null : r.json();
}
let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg; t.className = "toast" + (isErr ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3600);
}

const S = { classes: [], classId: null, assignments: [], current: null, topics: [], sid: null, ws: null,
            expanded: null, llm: { enabled: false } };

/* ---------------- 标签页 ---------------- */
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => setTab(/** @type{any} */(b).dataset.tab)));

function setTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.setAttribute("aria-selected", String(/** @type{any} */(b).dataset.tab === name)));
  for (const t of ["author", "board", "roster"]) $("tab-" + t).hidden = t !== name;
  if (name === "board") loadBoardAssignments();
  if (name === "roster") loadRoster();
}

/* ---------------- 启动 ---------------- */
async function boot() {
  S.classes = await api("/classes");
  if (!S.classes.length) { $("editor").innerHTML = '<div class="empty">还没有班级数据</div>'; return; }
  $("cls").innerHTML = S.classes.map((c) => `<option value="${c.id}">${esc(c.name)}（${c.size} 人）</option>`).join("");
  S.classId = S.classes[0].id;
  S.topics = await api("/kg/topics");
  // 识别能力是否可用要如实告诉教师 —— 界面上写着「拍照识别」而实际只存图,
  // 会让人以为题目已经录进去了。探活失败也当作不可用,不猜。
  try { S.llm = await api("/llm/health"); } catch { S.llm = { enabled: false }; }
  await loadAssignments();
}
$("cls").addEventListener("change", async () => {
  S.classId = $("cls").value; S.current = null;
  await loadAssignments();
  if (!$("tab-board").hidden) loadBoardAssignments();
  if (!$("tab-roster").hidden) loadRoster();
});
const curClass = () => S.classes.find((c) => c.id === S.classId);

/* ================================================================ 作业录入 */
async function loadAssignments(selectId) {
  S.assignments = await api(`/assignments?class_id=${encodeURIComponent(S.classId)}`);
  $("asg-list").innerHTML = S.assignments.length
    ? S.assignments.map((a) => `<li data-id="${a.id}" class="${a.id === (selectId || S.current?.id) ? "on" : ""}">
        <div class="t">${esc(a.title)}</div>
        <div class="m">${esc(a.assign_date)} · ${a.problem_count} 题 · ${a.review_status === "published" ? "已发布" : "草稿"}</div></li>`).join("")
    : '<li class="m" style="padding:14px 12px;color:var(--ink-3)">暂无作业</li>';
  const pick = selectId || S.current?.id || S.assignments[0]?.id;
  if (pick) await openAssignment(pick); else $("editor").innerHTML = '<div class="empty">新建一份作业开始录入。</div>';
}

$("asg-list").addEventListener("click", (e) => {
  const li = /** @type{HTMLElement} */(e.target).closest("li[data-id]");
  if (li) openAssignment(/** @type{any} */(li).dataset.id);
});

$("new-asg").addEventListener("click", async () => {
  const title = prompt("作业名称", `今日作业 · ${new Date().toLocaleDateString("zh-CN")}`);
  if (!title) return;
  const a = await api("/assignments", { method: "POST", body: JSON.stringify({ class_id: S.classId, title }) });
  await loadAssignments(a.id);
  toast("已新建草稿");
});

async function openAssignment(id) {
  S.current = await api(`/assignments/${id}`);
  document.querySelectorAll("#asg-list li").forEach((li) =>
    li.classList.toggle("on", /** @type{any} */(li).dataset.id === id));
  renderEditor();
}

function topicOptions(selected) {
  return `<option value="">— 未关联 —</option>` + S.topics.map((t) =>
    `<option value="${t.id}" ${t.id === selected ? "selected" : ""}>${esc(t.grade)} ${esc(t.chapter)} · ${esc(t.name)}</option>`).join("");
}

function renderEditor() {
  const a = S.current;
  if (!a) return;
  const published = a.review_status === "published";
  const blocked = a.unconfirmed_count > 0;

  const head = `
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <div>
          <h3>${esc(a.title)}</h3>
          <p class="hint" style="margin:2px 0 0">${esc(a.assign_date)} · ${a.problem_count} 题 ·
            <span class="pill ${published ? "live" : ""}">${published ? "已发布" : "草稿"}</span></p>
        </div>
        <div class="row">
          ${published
            ? `<button id="unpub">撤回发布</button>`
            : `<button class="primary" id="pub" ${blocked || !a.problem_count ? "disabled" : ""}>发布到大屏</button>`}
          <button class="danger" id="del-asg" ${published ? "disabled" : ""}>删除作业</button>
        </div>
      </div>
      ${published ? `<div class="banner ok" style="margin:12px 0 0">已发布，学生可在大屏看到。如需修改请先撤回。</div>` : ""}
      ${!published && blocked ? `<div class="banner warn" style="margin:12px 0 0">
          还有 <b>${a.unconfirmed_count}</b> 道题未校对。识别结果未经逐题过目不得推给学生 ——
          数学题一个符号错误就毁掉整道题。${a.low_confidence_count ? `其中 <b>${a.low_confidence_count}</b> 道知识点置信度偏低，已标黄。` : ""}
        </div>` : ""}
    </div>`;

  const ai = S.llm.enabled;
  const importer = published ? "" : `
    <div class="card">
      <h3>录入题目</h3>
      <p class="hint">${ai
        ? `拍作业照片直接识别（含公式），或粘贴整段文本按题号切分。两条路都会自动挂知识点候选，<b>都必须逐题校对后才能发布</b>。<br>
           多页作业逐张上传即可，题目会依次追加。`
        : `照片识别服务未配置（缺 <code>MATHMATE_LLM_API_KEY</code>）：上传原图仅作对照留存，题目内容需粘贴文本或手工录入。`}</p>
      <div class="row" style="margin-bottom:10px">
        <label class="btn-file ${ai ? "primary" : ""}" id="img-label">
          ${ai ? "📷 拍照/上传作业识别" : "上传原图（仅留存）"}
          <input type="file" id="img" accept="image/*" hidden></label>
        ${ai ? `<label class="row" style="gap:6px"><input type="checkbox" id="img-replace" style="width:auto"> 覆盖现有题目</label>
                <span class="mini muted">整页识别约 15–30 秒</span>` : ""}
      </div>
      ${(a.source_images || []).length ? `<div class="thumbs">${a.source_images.map((im) =>
        `<a href="${esc(im.uri)}" target="_blank"><img src="${esc(im.uri)}" alt="${esc(im.name)}"></a>`).join("")}</div>` : ""}
      <textarea id="bulk" rows="6" placeholder="或粘贴文本：&#10;1. 下列各式中，是分式的是（  ）&#10;A. …  B. …  C. …  D. …&#10;&#10;2、解不等式 …"></textarea>
      <div class="row" style="margin-top:10px">
        <button class="primary" id="do-import">切分并导入</button>
        <label class="row" style="gap:6px"><input type="checkbox" id="replace" checked style="width:auto"> 覆盖现有题目</label>
        <span class="sp"></span>
        <button id="add-one">＋ 手动加一题</button>
      </div>
    </div>`;

  const list = a.problems.length ? `<div class="plist">${a.problems.map(problemCard).join("")}</div>`
    : `<div class="empty">还没有题目。</div>`;

  $("editor").innerHTML = head + importer + list;
  bindEditor();
}

function problemCard(p) {
  const cls = p.review_status === "auto" ? (p.low_confidence ? "low" : "") : "ok";
  const badge = p.review_status === "auto"
    ? `<span class="badge auto">待校对</span>`
    : `<span class="badge done">${p.review_status === "confirmed" ? "已确认" : "已修改"}</span>`;
  const SRC = { llm: "AI 选定", suggested: "检索推荐", manual: "教师指定" };
  const conf = p.kp_source === "llm_rejected"
    ? `<span class="conf low">AI 认为候选都不合适，请手选</span>`
    : p.kp_confidence == null ? ""
    : `<span class="conf ${p.low_confidence ? "low" : ""}">${SRC[p.kp_source] || "推荐"} · 置信度 ${p.kp_confidence.toFixed(2)}</span>`;
  const opts = p.options && Object.keys(p.options).length
    ? `<ul class="opts">${Object.entries(p.options).map(([k, v]) => `<li>${esc(k)}. ${math(v)}</li>`).join("")}</ul>` : "";
  return `<div class="prob ${cls}" data-pid="${p.id}">
    <div class="ph"><span class="no">${p.seq_no}</span>${badge}${conf}
      <span class="sp" style="flex:1"></span>
      <button class="mini" data-act="del">删除</button></div>
    <div class="stem">${math(p.stem_text)}</div>${opts}
    <div class="ctl">
      <select data-act="kp">${topicOptions(p.kp_id)}</select>
      <label class="muted" style="font-size:12px">难度
        <select data-act="diff" style="margin-left:4px">${[1,2,3,4,5].map((d) =>
          `<option value="${d}" ${d === p.difficulty ? "selected" : ""}>${d}</option>`).join("")}</select></label>
      ${S.llm.enabled ? `<button class="mini" data-act="resuggest" title="让 AI 在检索候选里重新挑一个">✨ AI 重推</button>` : ""}
      ${p.review_status === "auto" ? `<button class="mini primary" data-act="ok">确认无误</button>` : ""}
    </div></div>`;
}

function bindEditor() {
  $("pub")?.addEventListener("click", async () => {
    try { S.current = await api(`/assignments/${S.current.id}/publish`, { method: "POST" });
          toast("已发布到大屏"); await loadAssignments(S.current.id); }
    catch (e) { toast(e.message, true); }
  });
  $("unpub")?.addEventListener("click", async () => {
    try { S.current = await api(`/assignments/${S.current.id}/unpublish`, { method: "POST" });
          toast("已撤回"); await loadAssignments(S.current.id); }
    catch (e) { toast(e.message, true); }
  });
  $("del-asg")?.addEventListener("click", async () => {
    if (!confirm("删除这份作业及其全部题目？")) return;
    await api(`/assignments/${S.current.id}`, { method: "DELETE" });
    S.current = null; await loadAssignments(); toast("已删除");
  });
  $("do-import")?.addEventListener("click", async () => {
    const text = $("bulk").value.trim();
    if (!text) return toast("先粘贴题目文本", true);
    try {
      S.current = await api(`/assignments/${S.current.id}/import-text`,
        { method: "POST", body: JSON.stringify({ text, replace: $("replace").checked }) });
      renderEditor(); await loadAssignments(S.current.id);
      const note = S.current.annotation;
      toast(`已切分出 ${S.current.problem_count} 道题，请逐题校对`
            + (note?.message ? `（${note.message}）` : ""), !!note?.message);
    } catch (e) { toast(e.message, true); }
  });
  $("add-one")?.addEventListener("click", async () => {
    const stem = prompt("题干");
    if (!stem) return;
    await api(`/assignments/${S.current.id}/problems`, { method: "POST",
      body: JSON.stringify({ stem_text: stem, problem_type: "blank" }) });
    await openAssignment(S.current.id); await loadAssignments(S.current.id);
  });
  $("img")?.addEventListener("change", async (e) => {
    const input = /** @type{any} */(e.target);
    const f = input.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("recognize", String(S.llm.enabled));
    fd.append("replace", String(!!$("img-replace")?.checked));

    // 整页识别要十几秒。没有忙态的话教师会以为没反应,然后重复点。
    const label = $("img-label");
    if (label) { label.classList.add("busy"); label.textContent = S.llm.enabled ? "识别中…" : "上传中…"; }
    try {
      const r = await api(`/assignments/${S.current.id}/images`, { method: "POST", body: fd });
      await openAssignment(S.current.id);
      await loadAssignments(S.current.id);
      toast(r.recognition.message, r.recognition.status === "failed");
    } catch (err) {
      toast(err.message, true);
      renderEditor();             // 失败时也要把「识别中…」还原回按钮
    }
  });

  $("editor").querySelectorAll(".prob").forEach((el) => {
    const pid = /** @type{any} */(el).dataset.pid;
    const patch = async (body) => {
      try { await api(`/problems/${pid}`, { method: "PATCH", body: JSON.stringify(body) });
            await openAssignment(S.current.id); await loadAssignments(S.current.id); }
      catch (e) { toast(e.message, true); }
    };
    el.querySelector('[data-act="kp"]')?.addEventListener("change", (ev) =>
      patch({ kp_id: /** @type{any} */(ev.target).value || null }));
    el.querySelector('[data-act="diff"]')?.addEventListener("change", (ev) =>
      patch({ difficulty: +/** @type{any} */(ev.target).value }));
    el.querySelector('[data-act="ok"]')?.addEventListener("click", () => patch({ confirmed: true }));
    el.querySelector('[data-act="resuggest"]')?.addEventListener("click", async (ev) => {
      const btn = /** @type{any} */(ev.currentTarget);
      const p = S.current.problems.find((x) => x.id === pid);
      btn.disabled = true; btn.textContent = "思考中…";
      try {
        const sug = await api("/kg/suggest", { method: "POST", body: JSON.stringify({
          stem_text: p.stem_text, grade: null, use_llm: true }) });
        if (!sug.length) return toast("没有找到合适的知识点候选", true);
        // 走与手选完全相同的 PATCH 路径:落库后 kp_source 记为 manual。
        // 教师主动点了这个按钮 = 采纳,和自己在下拉框里选是一回事。
        const sel = el.querySelector('[data-act="kp"]');
        sel.value = sug[0].kp_id;
        sel.dispatchEvent(new Event("change"));
        toast(`AI 选了「${sug[0].kp_name}」` + (sug[0].reason ? `：${sug[0].reason}` : ""));
      } catch (e) { toast(e.message, true); }
      finally { btn.disabled = false; btn.textContent = "✨ AI 重推"; }
    });
    el.querySelector('[data-act="del"]')?.addEventListener("click", async () => {
      if (!confirm("删除这道题？")) return;
      await api(`/problems/${pid}`, { method: "DELETE" });
      await openAssignment(S.current.id); await loadAssignments(S.current.id);
    });
  });
}

/* ================================================================ 讲评看板 */
async function loadBoardAssignments() {
  const list = (await api(`/assignments?class_id=${encodeURIComponent(S.classId)}`))
    .filter((a) => a.review_status === "published");
  $("asg-for-vote").innerHTML = list.length
    ? list.map((a) => `<option value="${a.id}">${esc(a.title)}（${a.problem_count} 题）</option>`).join("")
    : `<option value="">— 还没有已发布的作业 —</option>`;
  $("open").disabled = !list.length;
  const active = await api(`/vote-sessions/active?class_id=${encodeURIComponent(S.classId)}`);
  if (active) attach(active.id); else detach();
}

$("open").addEventListener("click", async () => {
  try {
    const s = await api("/vote-sessions", { method: "POST",
      body: JSON.stringify({ class_id: S.classId, assignment_id: $("asg-for-vote").value }) });
    attach(s.id); toast("投票窗口已开启");
  } catch (e) { toast(e.message, true); }
});
$("close").addEventListener("click", async () => {
  if (!S.sid) return;
  await api(`/vote-sessions/${S.sid}/close`, { method: "POST" });
  $("status").textContent = "已关闭"; $("status").className = "pill";
  $("open").disabled = false; $("close").disabled = true;
  toast("已关闭窗口");
});

function attach(id) {
  S.sid = id;
  $("status").textContent = "进行中"; $("status").className = "pill live";
  $("open").disabled = true; $("close").disabled = false;
  refreshTally();
  try { S.ws?.close(); } catch {}
  S.ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/vote/${id}`);
  S.ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "tally_update") renderTally(m.tally);
    if (m.type === "session_closed") { $("status").textContent = "已关闭"; $("status").className = "pill";
                                       $("open").disabled = false; $("close").disabled = true; }
  };
}
function detach() {
  S.sid = null; try { S.ws?.close(); } catch {}
  $("status").textContent = "未开启"; $("status").className = "pill";
  $("close").disabled = true;
  $("wrap").innerHTML = '<div class="empty">开启投票窗口后，学生在大屏上的选择会实时出现在这里。</div>';
}
async function refreshTally() { if (S.sid) renderTally(await api(`/vote-sessions/${S.sid}/tally`)); }

function renderTally(t) {
  $("done").textContent = t.submitted_count; $("size").textContent = t.class_size;
  $("rate").textContent = Math.round(t.participation * 100) + "%";
  const rows = t.problems.filter((p) => p.vote_count > 0);
  if (!rows.length) { $("wrap").innerHTML = '<div class="empty">还没有学生提交。</div>'; return; }
  $("wrap").innerHTML = `<table><thead><tr><th style="width:52px">题号</th><th style="width:70px">人数</th>
    <th>知识点</th><th>归因分布</th></tr></thead><tbody>${rows.map((p, i) => `
    <tr class="${i === 0 ? "hot" : ""}" data-pid="${p.problem_id}">
      <td class="seq">${p.seq_no}</td><td class="cnt">${p.vote_count}</td>
      <td>${esc(p.kp_name || "")}</td>
      <td><div class="rs">${Object.entries(p.reasons).sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `<span class="${k === p.dominant_reason ? "dom" : ""}">${esc(REASON[k] || k)} ${v}</span>`).join("")}</div>
        <div class="names" id="n-${p.problem_id}" hidden></div></td></tr>`).join("")}</tbody></table>`;
  if (S.expanded) showNames(S.expanded);
}

$("wrap").addEventListener("click", (e) => {
  const tr = /** @type{HTMLElement} */(e.target).closest("tr[data-pid]");
  if (!tr) return;
  const pid = /** @type{any} */(tr).dataset.pid;
  S.expanded = S.expanded === pid ? null : pid;
  document.querySelectorAll(".names").forEach((n) => { /** @type{any} */(n).hidden = true; });
  if (S.expanded) showNames(S.expanded);
});

async function showNames(pid) {
  const box = document.getElementById("n-" + pid);
  if (!box) return;
  box.hidden = false; box.textContent = "加载中…";
  try {
    const list = await api(`/vote-sessions/${S.sid}/problems/${pid}/students`);
    box.innerHTML = list.length ? list.map((s) =>
      `${esc(s.name)} <span class="muted">${esc(s.seat_no)} · ${esc(REASON[s.reason_code] || s.reason_code)}</span>`).join(" ｜ ") : "无";
  } catch (e) { box.textContent = e.message; }
}

/* ================================================================ 学生与宠物 */
$("r-refresh").addEventListener("click", loadRoster);

async function loadRoster() {
  const d = await api(`/classes/${S.classId}/pets`);
  $("r-count").textContent = d.count;
  $("r-energy").textContent = d.avg_energy;
  $("r-subs").textContent = d.total_submissions;
  $("r-silent").innerHTML = d.never_submitted.length
    ? `<div class="banner warn">有 <b>${d.never_submitted.length}</b> 名学生从未提交过选题（座位 ${
        d.never_submitted.slice(0, 12).map(esc).join("、")}${d.never_submitted.length > 12 ? " …" : ""}）。
        建议私下了解，不要在班级公开。</div>`
    : `<div class="banner ok">全班都参与过选题。</div>`;

  const maxE = Math.max(100, ...d.pets.map((p) => p.energy));
  $("pet-grid").innerHTML = d.pets.map((p) => `
    <div class="pet-card ${p.submissions === 0 ? "zero" : ""}">
      ${petSVG(p.species, p.hue)}
      <div class="info">
        <div class="nm">${esc(p.nickname)}</div>
        <div class="sub">${esc(p.seat_no)} · ${esc(p.name)}</div>
        <div class="en">Lv.${p.level} · 能量 ${p.energy} · 提交 ${p.submissions} 次</div>
        <div class="bar-mini"><i style="width:${Math.round(p.energy / maxE * 100)}%"></i></div>
      </div></div>`).join("");
}

boot().catch((e) => { $("editor").innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
