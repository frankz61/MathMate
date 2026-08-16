/* ------------------------------------------------------------------ 宠物形象 v6
 * 24 种萌宠(PRD FR-PET-01)。素材来自 **Noto Animated Emoji**(Google, CC BY 4.0),
 * 由 scripts/fetch_pets.py 下载并规格化,署名见 /shared/pets/CREDITS.md。
 *
 * v6 相对 v5(Fluent Emoji 3D 侧身像,MIT)的两处变化:
 *   1. 换成正面、大眼、亮色的形象 —— 侧身写实像在网格里认不出个性,
 *      而「宠物」这个设定的全部意义就是让学生认得出那是自己的。
 *   2. 每个物种同时有静态帧与动图:网格里 40 只同屏用静态(822 KB 全量),
 *      只有被点中/放大的那一只才加载动图。宠物在被点到的瞬间活过来,
 *      同屏 40 个循环动画则既费电又吵。
 *
 * petSVG(species, hue, opts)
 *   species: 见 SPECIES;不认识的名字回落到 fox
 *   hue:     色相(0-360),驱动身后柔光颜色 —— 同物种的不同学生靠它区分
 *   opts.idle:  瞌睡(飘 Z)
 *   opts.cheer: 庆祝(撒彩点)
 *   opts.level: 等级,≥2 加星徽,≥3 加金冠
 *   opts.alive: 用动图代替静态帧(仅用于单只放大展示)
 */

let _petUid = 0;

/* n 角星路径(以原点为中心,配合 <g transform> 使用) */
function _starPath(rO, rI, n = 5) {
  let d = "";
  for (let i = 0; i < n * 2; i++) {
    const r = i % 2 === 0 ? rO : rI;
    const a = -Math.PI / 2 + (i * Math.PI) / n;
    d += `${i === 0 ? "M" : "L"}${(r * Math.cos(a)).toFixed(2)} ${(r * Math.sin(a)).toFixed(2)}`;
  }
  return d + "Z";
}

const GOLD = "#ffd166";
const GOLD_DARK = "#d99a1e";

/* 物种清单与中文名。顺序即 seed 分配顺序,改动会让演示数据里学生的宠物换种。
 * 与 scripts/fetch_pets.py 的 ROSTER 必须一致 —— 那边是素材的唯一来源。 */
export const SPECIES_ZH = {
  fox: "狐狸", panda: "熊猫", penguin: "企鹅", owl: "猫头鹰",
  dog: "小狗", hedgehog: "刺猬", frog: "青蛙", turtle: "乌龟",
  unicorn: "独角兽", bear: "小熊", chick: "小鸡", dolphin: "海豚",
  tiger: "老虎", octopus: "章鱼", otter: "水獭", seal: "海豹",
  trex: "霸王龙", sauropod: "长颈龙", butterfly: "蝴蝶", chipmunk: "花栗鼠",
  pig: "小猪", rabbit: "兔子", flamingo: "火烈鸟", sloth: "树懒",
};
export const SPECIES = Object.keys(SPECIES_ZH);

/* 素材已由 fetch_pets.py 统一裁边并底部对齐到画布 248/256 处,
 * 所以这里是一组常量而不是每物种一份偏移表 —— 规格化放在生成期做,
 * 运行期就不用维护 24 组魔法数字。 */
const IMG_X = 10, IMG_Y = 6, IMG_SIZE = 100;

export function petSVG(species, hue, { idle = false, cheer = false, level = 1,
                                       alive = false } = {}) {
  const uid = `pg${++_petUid}`;
  const sp = SPECIES.includes(species) ? species : "fox";

  const defs = `
    <radialGradient id="${uid}a" cx=".5" cy=".5" r=".5">
      <stop offset="0" stop-color="hsl(${hue} 70% 62%)" stop-opacity=".16"/>
      <stop offset="1" stop-color="hsl(${hue} 70% 62%)" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="${uid}s" cx=".5" cy=".5" r=".5">
      <stop offset="0" stop-color="#101426" stop-opacity=".24"/><stop offset=".8" stop-color="#101426" stop-opacity=".13"/>
      <stop offset="1" stop-color="#101426" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="${uid}g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffe08a"/><stop offset="1" stop-color="#ffb02e"/>
    </linearGradient>`;

  const back = `
    <circle cx="60" cy="60" r="48" fill="url(#${uid}a)"/>
    <ellipse cx="60" cy="104" rx="30" ry="5.5" fill="url(#${uid}s)"/>`;

  /* 状态:瞌睡飘 Z / 庆祝撒彩点 */
  let state = "";
  if (idle) {
    state = `<g stroke="#9aa3c0" stroke-linecap="round" fill="none">
      <path d="M82 22 h9 l-9 9 h9" stroke-width="2.4">
        <animate attributeName="opacity" values="0;.9;0" dur="2.8s" repeatCount="indefinite"/></path>
      <path d="M95 12 h6.5 l-6.5 6.5 h6.5" stroke-width="1.9">
        <animate attributeName="opacity" values="0;.8;0" dur="2.8s" begin="1.2s" repeatCount="indefinite"/></path>
    </g>`;
  } else if (cheer) {
    state = `<g>
      <g transform="translate(22 28)"><circle r="4.6" fill="#fff" opacity=".3"/>
        <g><path d="${_starPath(5.6, 2.2, 4)}" fill="${GOLD}"/>
           <animateTransform attributeName="transform" type="scale" values="1;1.3;1" dur="1.5s" repeatCount="indefinite"/></g></g>
      <g transform="translate(96 24)"><circle r="4" fill="#fff" opacity=".3"/>
        <g><path d="${_starPath(4.6, 1.8, 4)}" fill="${GOLD}"/>
           <animateTransform attributeName="transform" type="scale" values="1;1.3;1" dur="1.5s" begin=".5s" repeatCount="indefinite"/></g></g>
      <g transform="translate(90 86)"><circle r="3.6" fill="#fff" opacity=".3"/>
        <g><path d="${_starPath(4.2, 1.7, 4)}" fill="${GOLD}"/>
           <animateTransform attributeName="transform" type="scale" values="1;1.3;1" dur="1.5s" begin="1s" repeatCount="indefinite"/></g></g>
    </g>`;
  }

  /* 等级配饰 */
  let flair = "";
  if (level >= 3) {
    flair = `
      <g transform="translate(60 12) rotate(-6)">
        <path d="M-13 8 L-13 -2 L-6 3 L0 -7 L6 3 L13 -2 L13 8 Z"
              fill="url(#${uid}g)" stroke="${GOLD_DARK}" stroke-width="1.4" stroke-linejoin="round"/>
        <rect x="-13" y="6.5" width="26" height="5.5" rx="2.2" fill="url(#${uid}g)" stroke="${GOLD_DARK}" stroke-width="1.2"/>
        <circle cx="-13" cy="-3" r="2" fill="url(#${uid}g)" stroke="${GOLD_DARK}" stroke-width="1"/>
        <circle cx="0" cy="-8" r="2.2" fill="url(#${uid}g)" stroke="${GOLD_DARK}" stroke-width="1"/>
        <circle cx="13" cy="-3" r="2" fill="url(#${uid}g)" stroke="${GOLD_DARK}" stroke-width="1"/>
        <circle cx="0" cy="9.2" r="1.8" fill="#ff6b81"/>
        <circle cx="-6.5" cy="9.2" r="1.2" fill="#7ec8ff"/>
        <circle cx="6.5" cy="9.2" r="1.2" fill="#7ec8ff"/>
      </g>`;
  } else if (level >= 2) {
    flair = `
      <g transform="translate(88 26)">
        <circle r="9" fill="#fff" opacity=".22"/>
        <path d="${_starPath(7.2, 3.1)}" fill="url(#${uid}g)" stroke="${GOLD_DARK}" stroke-width="1.2" stroke-linejoin="round"/>
        <path d="M-3.4 -1.2 L-1.4 -3.6" stroke="#fff" stroke-width="1.4" stroke-linecap="round" opacity=".85"/>
      </g>`;
  }

  const ext = alive ? "webp" : "png";
  const img = `<image href="/shared/pets/${sp}.${ext}" x="${IMG_X}" y="${IMG_Y}"`
    + ` width="${IMG_SIZE}" height="${IMG_SIZE}"/>`;

  return `<svg viewBox="0 0 120 120" aria-hidden="true">${defs}${back}${img}${state}${flair}</svg>`;
}

/* 供非模块环境兜底(目前两端均为 ES module) */
if (typeof window !== "undefined") window.petSVG = petSVG;
