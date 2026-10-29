// @ts-check
/* MathMate 教室大屏 —— 学生点自己的宠物,选出想听讲解的题目并给出归因。
 *
 * 免构建的原生 ES 模块:首个切片优先保证「能跑起来、能逐条验证」。
 * DESIGN.md 规划的正式实现是 React + Vite + PixiJS;API 契约已固定,迁移是机械工作。
 *
 * 未实现(有意留白,见 README):
 *   FR-VOTE-08 多点触控分区(同屏 2–3 人并行)
 *   FR-VOTE-11 手机端并行通道
 */

import { petSVG } from "/shared/pet.js";

const API = "/api/v1";

/* PRD §3.3.3 —— 归因是产品的核心资产,固定七类,顺序按「教学紧迫度」从高到低 */
const REASONS = [
  { code: "NO_IDEA",       label: "完全没思路",           color: "var(--r-no-idea)" },
  { code: "STUCK_MIDWAY",  label: "有点思路，中间卡住了",  color: "var(--r-stuck)" },
  { code: "WRONG_UNKNOWN", label: "做错了，不知道错在哪",  color: "var(--r-wrong)" },
  { code: "SLOW",          label: "会做，但太慢太麻烦",    color: "var(--r-slow)" },
  { code: "WANT_BETTER",   label: "做对了，想看更好的方法", color: "var(--r-better)" },
  { code: "WANT_EXTEND",   label: "想看这类题的变式拓展",  color: "var(--r-extend)" },
  { code: "CARELESS",      label: "就是算错了（粗心）",    color: "var(--r-careless)" },
];
const REASON_BY = Object.fromEntries(REASONS.map((r) => [r.code, r]));

const $ = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ------------------------------------------------------------------ 极简 LaTeX
 * 题干来自数据集,含 $$...$$ 包裹的公式。不引外部库(大屏可能离线),
 * 只处理初中数学实际出现的那些结构。
 */
const SYM = {
  "\\times": "×", "\\div": "÷", "\\pm": "±", "\\cdot": "·",
  "\\le": "≤", "\\leq": "≤", "\\ge": "≥", "\\geq": "≥", "\\ne": "≠", "\\neq": "≠",
  "\\approx": "≈", "\\sim": "∽", "\\cong": "≌", "\\circ": "°", "\\angle": "∠",
  "\\triangle": "△", "\\parallel": "∥", "\\perp": "⊥", "\\odot": "⊙",
  "\\alpha": "α", "\\beta": "β", "\\theta": "θ", "\\pi": "π", "\\Delta": "Δ",
  "\\Rightarrow": "⇒", "\\rightarrow": "→", "\\to": "→", "\\because": "∵", "\\therefore": "∴",
  "\\infty": "∞", "\\ldots": "…", "\\cdots": "⋯", "\\dots": "…", "\\in": "∈",
  "\\textless": "<", "\\textgreater": ">", "\\%": "%", "\\{": "{", "\\}": "}",
  "\\left": "", "\\right": "", "\\displaystyle": "", "\\,": " ", "\\;": " ", "\\!": "",
  "\\quad": "  ", "\\qquad": "    ",
};

function tex(src) {
  let out = "", i = 0;
  const readCmd = () => {
    const s = i; i++;
    while (i < src.length && /[a-zA-Z]/.test(src[i])) i++;
    if (i === s + 1) i++;
    return src.slice(s, i);
  };
  const readGroup = () => {
    while (src[i] === " ") i++;
    if (src[i] === "{") {
      let d = 1; const s = ++i;
      while (i < src.length && d > 0) { if (src[i] === "{") d++; else if (src[i] === "}") d--; i++; }
      return src.slice(s, i - 1);
    }
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
      } else if (cmd === "\\sqrt") {
        out += `<span class="sqrt">√<span class="rr">${tex(readGroup())}</span></span>`;
      } else if (cmd === "\\text" || cmd === "\\mathrm") {
        out += esc(readGroup());
      } else if (cmd in SYM) out += SYM[cmd];
      else out += esc(cmd.slice(1));
    } else if (c === "^" || c === "_") {
      i++;
      const inner = tex(readGroup());
      if (inner) out += c === "^" ? `<sup>${inner}</sup>` : `<sub>${inner}</sub>`;
    } else if (c === "{" || c === "}" || c === "&") i++;
    else if (c === "~") { out += " "; i++; }
    else { out += esc(c); i++; }
  }
  return out;
}

const plain = (s) => esc(String(s)
  .replace(/\\(?:emph|textbf|textit|text|mathrm|uline)\{([^{}]*)\}/g, "$1")
  .replace(/\\(?:hfill|break|newline|par)\b/g, " ")
  .replace(/\\\\/g, " ").replace(/\\[a-zA-Z]+\s?/g, "")
  .replace(/``/g, "“").replace(/''/g, "”"));

const math = (s) => !s ? "" : String(s)
  .split(/(\$\$[\s\S]*?\$\$|\$[^$\n]*\$)/g)
  .map((p) => {
    if (p.length >= 4 && p.startsWith("$$") && p.endsWith("$$")) return `<span class="math">${tex(p.slice(2, -2))}</span>`;
    if (p.length >= 2 && p[0] === "$" && p.endsWith("$")) return `<span class="math">${tex(p.slice(1, -1))}</span>`;
    return plain(p);
  }).join("");

/* ------------------------------------------------------------------ 宠物形象
 * 绘制在 /shared/pet.js(与教师端共用):24 种动物的形象来自 Noto Animated
 * Emoji(CC BY 4.0,见 /shared/pets/CREDITS.md),色相柔光、表情状态与等级
 * 配饰仍由 hue / idle / cheer / level 参数化叠加。
 * 网格里一律用静态帧,只有被点中放大的那一只传 alive:true 换成动图。
 */

const STAR = `<svg class="star" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
  <path d="M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.3 5.9 20.6l1.4-6.8L2.2 9.1l6.9-.8z"/></svg>`;

/* ------------------------------------------------------------------ 离线队列
 * NFR-AV-02:断网时必须能走完整个投票流程,提交先落 IndexedDB,恢复后补传。
 */
const DB_NAME = "mathmate-screen";
let _db = null;

function idb() {
  if (_db) return Promise.resolve(_db);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("outbox")) db.createObjectStore("outbox", { keyPath: "key" });
    };
    req.onsuccess = () => { _db = req.result; resolve(_db); };
    req.onerror = () => reject(req.error);
  });
}

async function outboxPut(item) {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction("outbox", "readwrite");
    tx.objectStore("outbox").put(item);
    tx.oncomplete = () => res(true);
    tx.onerror = () => rej(tx.error);
  });
}

async function outboxAll() {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction("outbox", "readonly");
    const r = tx.objectStore("outbox").getAll();
    r.onsuccess = () => res(r.result || []);
    r.onerror = () => rej(r.error);
  });
}

async function outboxDel(key) {
  const db = await idb();
  return new Promise((res) => {
    const tx = db.transaction("outbox", "readwrite");
    tx.objectStore("outbox").delete(key);
    tx.oncomplete = () => res(true);
  });
}

/* ------------------------------------------------------------------ 状态 */
const S = {
  // 班级来自 device_token,不再由 URL 参数决定 —— 令牌是唯一权威,
  // 用 ?class= 指到别的班只会被服务端 403 挡回来。
  classId: null,
  session: null,
  pets: [],
  problems: [],
  submitted: new Set(),
  layout: { rows: 6, cols: 7 },
  className: "",
  assignmentTitle: "",
  // 当前操作的学生
  cur: null,          // pet 对象
  picks: new Map(),   // problem_id -> reason_code
  pinBuf: "",
  pendingPick: null,  // 正在选归因的题
  online: true,
  ws: null,
  idleTimer: null,
};

const screens = ["s-pair", "s-garden", "s-confirm", "s-pick", "s-done", "s-idle"];
function show(id) {
  screens.forEach((s) => { $(s).hidden = s !== id; });
  // 花园在 display:none 时 clientHeight 为 0,缩放系数算不出来。必须等它真正
  // 可见的这一刻再算 —— renderGarden() 里那次调用发生在 show() 之前,量到的是 0。
  if (id === "s-garden" && S.pets.length) fitGarden();
  if (id !== "s-garden" && id !== "s-idle") armIdle(); else disarmIdle();
}

/* 学生走开时自动回到花园,避免占着屏幕 */
function armIdle() {
  disarmIdle();
  S.idleTimer = setTimeout(() => { resetFlow(); show("s-garden"); }, 60000);
}
function disarmIdle() { if (S.idleTimer) { clearTimeout(S.idleTimer); S.idleTimer = null; } }
["pointerdown", "keydown"].forEach((e) =>
  document.addEventListener(e, () => { if (S.idleTimer) armIdle(); }, true));

function setNet(online) {
  S.online = online;
  const el = $("net");
  el.classList.toggle("off", !online);
  $("net-txt").textContent = online ? "在线" : "离线 · 数据将稍后同步";
}

/* ------------------------------------------------------------------ 网络 */
/* 大屏凭证:长效 device_token(DESIGN §10.1)。
 * 存 localStorage —— 这台设备本来就是「配对一次,用一学期」,每次开机重配
 * 会直接让产品在教室里没法用。令牌本身权限极窄,泄露的代价是可控的。
 */
const DEV = {
  get token() { return localStorage.getItem("mm.device") || ""; },
  set(t) { localStorage.setItem("mm.device", t); },
  clear() { localStorage.removeItem("mm.device"); },
};

async function api(path, opts) {
  const headers = { "Content-Type": "application/json" };
  if (DEV.token) headers["X-Device-Token"] = DEV.token;
  const r = await fetch(API + path, { ...opts, headers: { ...headers, ...(opts?.headers || {}) } });
  if (!r.ok) {
    let detail = null;
    try { detail = (await r.json()).detail; } catch { /* 非 JSON 错误体 */ }
    // 令牌被老师吊销、或换了班:回到配对页,而不是反复弹「连不上」
    if (r.status === 401) { DEV.clear(); showPairing("这块大屏需要重新配对"); }
    const err = new Error(detail?.message || `HTTP ${r.status}`);
    // @ts-ignore
    err.status = r.status; err.detail = detail;
    throw err;
  }
  return r.status === 204 ? null : r.json();
}

/* 班级由令牌决定,大屏不再读班级列表(那是教师端接口) */
async function resolveClass() {
  if (S.classId) return S.classId;
  const me = await api("/screens/me");
  S.classId = me.class_id;
  S.className = me.class_name;
  return S.classId;
}

async function findSession() {
  const s = await api(`/vote-sessions/active?class_id=${encodeURIComponent(S.classId)}`);
  return s || null;
}

async function loadSession(session) {
  S.session = session;
  const bs = await api(`/vote-sessions/${session.id}/bootstrap`);
  S.className = bs.class_name;
  S.assignmentTitle = bs.assignment_title;
  S.layout = bs.seat_layout || { rows: 6, cols: 7 };
  S.pets = bs.pets;
  S.problems = bs.problems;
  S.submitted = new Set(bs.submitted_student_ids);

  $("cls-name").textContent = bs.class_name;
  $("asg-title").textContent = `${bs.assignment_title} · 共 ${bs.problems.length} 题`;
  $("pk-max").textContent = String(session.max_choices);
  renderGarden();
  updateCounter();
  connectWS(session.id);
  show("s-garden");
  flushOutbox();
}

function connectWS(sessionId) {
  try { S.ws?.close(); } catch { /* 已关闭 */ }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  // 浏览器的 WebSocket 构造器不能自定义请求头,凭证只能走 query
  const ws = new WebSocket(
    `${proto}://${location.host}/ws/vote/${sessionId}?token=${encodeURIComponent(DEV.token)}`);
  S.ws = ws;
  ws.onopen = () => setNet(true);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "progress") {
      // 大屏只收进度数字,不收归因聚合(DESIGN §10.1):
      // 一块全班都看得见的屏幕不该显示「30 人不会第 7 题」。
      $("c-done").textContent = String(m.submitted_count);
      $("c-total").textContent = String(m.class_size);
    } else if (m.type === "session_closed") {
      S.session = null;
      resetFlow();
      showIdle("本次选题已结束", "老师已关闭投票窗口，感谢参与。");
      pollForSession();
    }
  };
  ws.onclose = () => { S.ws = null; };
  ws.onerror = () => { /* 断线由轮询与 outbox 兜底 */ };
}

/* ------------------------------------------------------------------ 渲染 */

/* 一格在 --k=1 时的标称尺寸(与 styles.css 里的常量对应)。
   BORDER 单列出来:2px 描边不参与缩放,6 排就是 24px 的固定开销。 */
const CELL_W = 108, CELL_H = 117, BORDER = 4, GAP_X = 14, GAP_Y = 10, PAD = 22;

/** 按行列数把整块座位表缩放到一屏之内。
 *
 * 大屏离最后一排 5–8 米,一旦出现滚动没人会去滚 —— 第一排的学生只会看到
 * 半截宠物然后放弃。所以这里宁可把格子缩小,也不允许座位表溢出。
 * 行列数取「座位表声明值」与「实际座位号」的较大者:seat_layout 可能过期。
 */
function fitGarden() {
  const g = $("garden");
  let rows = Math.max(1, S.layout.rows || 1);
  let cols = Math.max(1, S.layout.cols || 1);
  for (const p of S.pets) {
    rows = Math.max(rows, parseInt(p.seat_no.slice(0, 2), 10) || 1);
    cols = Math.max(cols, parseInt(p.seat_no.slice(2), 10) || 1);
  }
  const availH = g.clientHeight - PAD * 2;
  const availW = g.clientWidth - PAD * 2;
  if (availH <= 0 || availW <= 0) return;           // 还没布局(屏幕隐藏时)
  const k = Math.min(
    (availH - rows * BORDER) / (rows * CELL_H + (rows - 1) * GAP_Y),
    availW / (cols * CELL_W + (cols - 1) * GAP_X),
    1.6,                                            // 4K 上也不必让宠物大到失真
  );
  // 0.5 以下昵称就看不清了;真到那一步宁可让它溢出,由 safe center 保证从顶部开始
  const clamp = (v) => Math.max(0.5, Math.min(v, 1.6));
  g.style.gridTemplateColumns = `repeat(${cols}, auto)`;
  g.style.setProperty("--k", clamp(k).toFixed(3));

  // 上面的常量是估算(行高取整、描边不缩放),会残留几像素。用一次实测把误差收掉 ——
  // 这样以后调格子样式也不必回来同步常量,顶多多跑一次收敛。
  const over = g.scrollHeight - g.clientHeight;
  if (over > 0) {
    const content = g.scrollHeight - PAD * 2;
    g.style.setProperty("--k", clamp(k * (availH / content) * 0.998).toFixed(3));
  }
}

function renderGarden() {
  const g = $("garden");
  g.style.gridTemplateColumns = `repeat(${S.layout.cols || 7}, auto)`;
  g.textContent = "";
  const open = !!S.session;
  for (const p of S.pets) {
    const row = parseInt(p.seat_no.slice(0, 2), 10) || 1;
    const col = parseInt(p.seat_no.slice(2), 10) || 1;
    const done = S.submitted.has(p.student_id);
    const b = document.createElement("button");
    b.className = "pet-cell" + (done ? " done" : "");
    b.style.gridRow = String(row);
    b.style.gridColumn = String(col);
    b.dataset.sid = p.student_id;
    b.innerHTML =
      `<div class="pet-art${open ? "" : " idle"}${done ? " cheer" : ""}">` +
      petSVG(p.species, p.hue, { idle: !open, cheer: done, level: p.level }) +
      `${done ? STAR : ""}</div>` +
      `<span class="nick">${esc(p.nickname)}</span>` +
      `<span class="seat">${esc(p.seat_no)}</span>`;
    g.appendChild(b);
  }
  fitGarden();
}

/* 投影切换分辨率、一体机旋转、字体迟到导致的重排,都会改变可用高度。
   盯容器本身比盯 window.resize 可靠 —— 后者漏掉「窗口没变但布局变了」的情况。
   fitGarden 只改子元素尺寸,花园自身由 flex 决定,不会自激。 */
if (typeof ResizeObserver === "function") {
  new ResizeObserver(() => { if (S.pets.length) fitGarden(); }).observe($("garden"));
} else {
  addEventListener("resize", () => { if (S.pets.length) fitGarden(); });
}

function updateCounter() {
  $("c-done").textContent = String(S.submitted.size);
  $("c-total").textContent = String(S.pets.length);
}

function showIdle(big, small) {
  $("idle-big").textContent = big;
  $("idle-small").textContent = small;
  show("s-idle");
}

/* ------------------------------------------------------------------ 座位确认 */
$("garden").addEventListener("click", (ev) => {
  const cell = /** @type {HTMLElement} */ (ev.target).closest(".pet-cell");
  if (!cell || !S.session) return;
  const pet = S.pets.find((p) => p.student_id === cell.dataset.sid);
  if (!pet) return;
  S.cur = pet;
  S.pinBuf = "";
  S.picks.clear();
    // alive:被点中的这一只才加载动图。宠物在被认领的瞬间活过来,
    // 而网格里 40 只同时循环动画既吵又费电。
    $("cf-art").innerHTML = petSVG(pet.species, pet.hue, { level: pet.level, alive: true });
  $("cf-nick").textContent = pet.nickname;
  $("cf-err").textContent = S.submitted.has(pet.student_id) ? "你已经选过了，可以重新选一次覆盖之前的答案" : "";
  drawPin();
  show("s-confirm");
});

function drawPin(bad = false) {
  for (const i of [0, 1]) {
    const el = $("p" + i);
    el.textContent = S.pinBuf[i] || "";
    el.className = bad ? "bad" : (S.pinBuf[i] ? "filled" : "");
  }
}

(function buildKeypad() {
  const k = $("keypad");
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "←"].forEach((d) => {
    const b = document.createElement("button");
    b.textContent = d;
    if (!d) { b.style.visibility = "hidden"; b.disabled = true; }
    b.addEventListener("click", () => {
      if (d === "←") S.pinBuf = S.pinBuf.slice(0, -1);
      else if (d && S.pinBuf.length < 2) S.pinBuf += d;
      drawPin();
      if (S.pinBuf.length === 2) checkPin();
    });
    k.appendChild(b);
  });
})();

function checkPin() {
  // 这不是安全机制,是**防误触**机制:目标是防止手滑点到同桌的宠物。
  // 教室里同学互相看得见,社会约束足够;引入密码会让 20 秒的操作目标彻底不可能。
  const tail = S.cur.seat_no.slice(-2);
  if (S.pinBuf === tail) {
    $("cf-err").textContent = "";
    openPicker();
  } else {
    drawPin(true);
    $("cf-err").textContent = "座位号不对，再试一次";
    setTimeout(() => { S.pinBuf = ""; drawPin(); }, 420);
  }
}

$("cf-back").addEventListener("click", () => { resetFlow(); show("s-garden"); });

/* ------------------------------------------------------------------ 选题 */
function openPicker() {
  $("pk-art").innerHTML = petSVG(S.cur.species, S.cur.hue, { level: S.cur.level, alive: true });
  $("pk-nick").textContent = S.cur.nickname;
  renderWall();
  show("s-pick");
}

function renderWall() {
  const w = $("qwall");
  w.textContent = "";
  for (const p of S.problems) {
    const picked = S.picks.get(p.id);
    const b = document.createElement("button");
    b.className = "qcard" + (picked ? " on" : "");
    b.dataset.pid = p.id;
    const r = picked ? REASON_BY[picked] : null;
    b.innerHTML =
      `<div class="row1"><span class="no">${p.seq_no}</span>` +
      `<span class="kp">${esc(p.kp_name || "")}</span>` +
      `<span class="diff">${"★".repeat(p.difficulty)}</span></div>` +
      `<div class="stem">${math(p.stem_text)}</div>` +
      (r ? `<span class="picked"><span class="swatch" style="background:${r.color}"></span>${esc(r.label)}</span>` : "");
    w.appendChild(b);
  }
  $("pk-count").textContent = String(S.picks.size);
  /** @type {HTMLButtonElement} */ ($("pk-submit")).disabled = S.picks.size === 0;
}

$("qwall").addEventListener("click", (ev) => {
  const card = /** @type {HTMLElement} */ (ev.target).closest(".qcard");
  if (!card) return;
  const pid = card.dataset.pid;
  if (S.picks.has(pid)) { S.picks.delete(pid); renderWall(); return; }  // 再点一次取消
  if (S.picks.size >= (S.session?.max_choices ?? 3)) {
    // 上限是刻意的:强制取舍,否则全选会让信号失真
    const foot = $("pk-count");
    foot.animate([{ color: "var(--r-no-idea)" }, { color: "" }], { duration: 700 });
    return;
  }
  S.pendingPick = S.problems.find((p) => p.id === pid);
  openSheet();
});

/* ------------------------------------------------------------------ 归因 */
(function buildReasons() {
  const box = $("reasons");
  for (const r of REASONS) {
    const b = document.createElement("button");
    b.className = "reason";
    b.style.color = r.color;
    b.dataset.code = r.code;
    b.innerHTML = `<span class="bar"></span><span class="txt">${esc(r.label)}</span>`;
    box.appendChild(b);
  }
})();

function openSheet() {
  $("sh-title").textContent = `第 ${S.pendingPick.seq_no} 题 —— 为什么想听这题？`;
  $("sheet").hidden = false;
}
function closeSheet() { $("sheet").hidden = true; S.pendingPick = null; }

$("reasons").addEventListener("click", (ev) => {
  const b = /** @type {HTMLElement} */ (ev.target).closest(".reason");
  if (!b || !S.pendingPick) return;
  S.picks.set(S.pendingPick.id, b.dataset.code);
  closeSheet();
  renderWall();
});
$("sh-cancel").addEventListener("click", closeSheet);
$("sheet").addEventListener("click", (ev) => { if (ev.target === $("sheet")) closeSheet(); });

$("pk-cancel").addEventListener("click", () => { resetFlow(); show("s-garden"); });
$("pk-none").addEventListener("click", () => submit([]));
$("pk-submit").addEventListener("click", () =>
  submit([...S.picks].map(([problem_id, reason_code]) => ({ problem_id, reason_code, note: null }))));

/* ------------------------------------------------------------------ 提交 */
function newKey() {
  return (crypto.randomUUID?.() || String(Date.now()) + Math.random().toString(16).slice(2)).replace(/-/g, "");
}

async function submit(choices) {
  const student = S.cur;
  const payload = {
    idempotency_key: newKey(),
    student_id: student.student_id,
    client_ts: new Date().toISOString(),
    source: "bigscreen",
    choices,
  };
  const sessionId = S.session.id;

  // 乐观提交:先给学生反馈并释放屏幕,网络失败转入 outbox 后台重试。
  S.submitted.add(student.student_id);
  updateCounter();
  renderGarden();
  showDone(student, choices.length, null);

  try {
    const res = await api(`/vote-sessions/${sessionId}/submissions`, {
      method: "POST", body: JSON.stringify(payload),
    });
    setNet(true);
    if (res?.pet_energy != null) {
      const pet = S.pets.find((p) => p.student_id === student.student_id);
      if (pet) pet.energy = res.pet_energy;
      showDone(student, choices.length, res.pet_energy);
    }
    if (res?.submitted_count != null) {
      $("c-done").textContent = String(res.submitted_count);
      $("c-total").textContent = String(res.class_size);
    }
  } catch (e) {
    // 会话已关闭等业务错误不该无限重试
    // @ts-ignore
    if (e.status && e.status !== 0 && e.status < 500) {
      // @ts-ignore
      $("dn-sub").textContent = e.message || "提交未被接受";
      S.submitted.delete(student.student_id);
      updateCounter(); renderGarden();
    } else {
      await outboxPut({ key: payload.idempotency_key, sessionId, payload, tries: 0 });
      setNet(false);
    }
  }
}

function showDone(pet, n, energy) {
  $("dn-art").innerHTML = petSVG(pet.species, pet.hue,
                                 { cheer: true, level: pet.level ?? 1, alive: true });
  $("dn-title").textContent = n ? "收到啦！" : "好的，今天先这样";
  $("dn-sub").textContent = n
    ? `${pet.nickname} 帮你把 ${n} 道题告诉老师了`
    : `${pet.nickname} 告诉老师你今天没有特别想听的题`;
  const box = $("dn-energy");
  if (energy != null) { box.hidden = false; box.textContent = `能量 +10 · 当前 ${energy}`; }
  else box.hidden = true;
  show("s-done");
  setTimeout(() => { if (!$("s-done").hidden) { resetFlow(); show("s-garden"); } }, 3200);
}

function resetFlow() {
  S.cur = null; S.picks.clear(); S.pinBuf = ""; S.pendingPick = null;
  closeSheet();
}

/* ------------------------------------------------------------------ 补传 */
let flushing = false;
async function flushOutbox() {
  if (flushing) return;
  flushing = true;
  try {
    const items = await outboxAll();
    for (const it of items) {
      try {
        await api(`/vote-sessions/${it.sessionId}/submissions`, {
          method: "POST", body: JSON.stringify(it.payload),
        });
        await outboxDel(it.key);   // 服务端按 idempotency_key 去重,重复补传是安全的
        setNet(true);
      } catch (e) {
        // @ts-ignore  4xx 是业务拒绝,重试多少次都一样,丢弃避免堆积
        if (e.status && e.status < 500) await outboxDel(it.key);
        else { setNet(false); break; }
      }
    }
  } finally { flushing = false; }
}
setInterval(flushOutbox, 15000);
window.addEventListener("online", () => { setNet(true); flushOutbox(); });
window.addEventListener("offline", () => setNet(false));

/* ------------------------------------------------------------------ 配对 */
let pairBuf = "";

function showPairing(msg) {
  pairBuf = "";
  $("pr-err").textContent = msg || "";
  drawPairPin();
  show("s-pair");
}

function drawPairPin(bad = false) {
  for (let i = 0; i < 6; i++) {
    const el = $("q" + i);
    el.textContent = pairBuf[i] || "";
    el.className = bad ? "bad" : (pairBuf[i] ? "filled" : "");
  }
}

(function buildPairKeypad() {
  const k = $("pair-keypad");
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "←"].forEach((d) => {
    const b = document.createElement("button");
    b.textContent = d;
    if (!d) { b.style.visibility = "hidden"; b.disabled = true; }
    b.addEventListener("click", () => {
      if (d === "←") pairBuf = pairBuf.slice(0, -1);
      else if (d && pairBuf.length < 6) pairBuf += d;
      drawPairPin();
      if (pairBuf.length === 6) submitPairing();
    });
    k.appendChild(b);
  });
})();

async function submitPairing() {
  $("pr-err").textContent = "正在配对…";
  try {
    const r = await fetch(API + "/screens/pair", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: pairBuf }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d?.detail?.message || `配对失败（HTTP ${r.status}）`);
    DEV.set(d.device_token);
    S.classId = d.class_id;
    S.className = d.class_name;
    $("pr-err").textContent = "";
    $("cls-name").textContent = d.class_name;
    await start();
  } catch (e) {
    drawPairPin(true);
    $("pr-err").textContent = e.message;
    setTimeout(() => { pairBuf = ""; drawPairPin(); }, 900);
  }
}

/* ------------------------------------------------------------------ 轮询开窗 */
let pollTimer = null;
function pollForSession() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const s = await findSession();
      setNet(true);
      if (s) { clearInterval(pollTimer); pollTimer = null; await loadSession(s); }
    } catch { setNet(false); }
  }, 4000);
}

/* ------------------------------------------------------------------ 启动 */
async function start() {
  await resolveClass();
  $("cls-name").textContent = S.className || "MathMate";
  const s = await findSession();
  if (s) await loadSession(s);
  else {
    showIdle("现在没有进行中的选题", "等老师在教师端开启「投票窗口」后，这里会自动亮起。");
    pollForSession();
  }
}

(async function boot() {
  if (!DEV.token) return showPairing();
  try {
    await start();
  } catch (e) {
    // 401 已经在 api() 里转去配对页了,这里只处理网络类故障
    // @ts-ignore
    if (e?.status === 401) return;
    setNet(false);
    showIdle("连不上服务器", String(e?.message || e));
    pollForSession();
  }
})();
