# -*- coding: utf-8 -*-
"""抓取宠物形象素材并规格化。

    python scripts/fetch_pets.py            # 增量,已有的跳过
    python scripts/fetch_pets.py --force    # 全部重下

素材来源:**Noto Animated Emoji**(Google,CC BY 4.0)
    https://googlefonts.github.io/noto-emoji-animation/

为什么要有这个脚本而不是把图片"就那么放进去":第三方素材必须能说清
从哪来、什么协议、怎么再生成一遍。CC BY 要求署名,署名信息由本脚本
写进 apps/shared/pets/CREDITS.md,和图片同目录,不会走散。

产物(apps/shared/pets/):
    <name>.png    256×256 静态帧 —— 花名册/大屏网格用,40 只同屏也不卡
    <name>.webp   256×256 动图   —— 只在宠物被点中/放大时加载,让它"活过来"
    manifest.json 物种清单 + 体积,供 pet.js 与画廊读取

规格化做了两件原图没做的事:按内容自动裁边、统一底部对齐,
这样 pet.js 里不再需要每个物种一组魔法偏移量。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import httpx
from PIL import Image

_HAS_FFMPEG = shutil.which("ffmpeg") is not None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "apps", "shared", "pets")
SRC = "https://fonts.gstatic.com/s/e/notoemoji/latest/{cp}/512.webp"

CANVAS = 256          # 输出画布边长
FIT_W, FIT_H = 236, 224   # 主体最大占位
BOTTOM = 248          # 主体底边落在这条线上(与 pet.js 里的影子对齐)
ALPHA_MIN = 40        # 判定"有内容"的透明度阈值
TARGET_FPS = 15       # 动图抽帧目标帧率

# 罗盘:正面、大眼、轮廓好认、颜色互相区分得开。
# 刻意排除:lion(脸像面具)、peacock(太花)、snail(发闷)、crab/ladybug/bee
# (节肢动物,且是全套里最大的两个文件)、whale(与 dolphin 太像)。
ROSTER: dict[str, tuple[str, str]] = {
    # key:        (unicode 码点, 中文名)
    "fox":        ("1f98a", "狐狸"),
    "panda":      ("1f43c", "熊猫"),
    "penguin":    ("1f427", "企鹅"),
    "owl":        ("1f989", "猫头鹰"),
    "dog":        ("1f415", "小狗"),
    "hedgehog":   ("1f994", "刺猬"),
    "frog":       ("1f438", "青蛙"),
    "turtle":     ("1f422", "乌龟"),
    "unicorn":    ("1f984", "独角兽"),
    "bear":       ("1f43b", "小熊"),
    "chick":      ("1f425", "小鸡"),
    "dolphin":    ("1f42c", "海豚"),
    "tiger":      ("1f405", "老虎"),
    "octopus":    ("1f419", "章鱼"),
    "otter":      ("1f9a6", "水獭"),
    "seal":       ("1f9ad", "海豹"),
    "trex":       ("1f996", "霸王龙"),
    "sauropod":   ("1f995", "长颈龙"),
    "butterfly":  ("1f98b", "蝴蝶"),
    "chipmunk":   ("1f43f", "花栗鼠"),
    "pig":        ("1f416", "小猪"),
    "rabbit":     ("1f407", "兔子"),
    "flamingo":   ("1f9a9", "火烈鸟"),
    "sloth":      ("1f9a5", "树懒"),
}

CREDITS = """# 宠物形象素材来源

本目录下的 `*.png` / `*.webp` 由 `scripts/fetch_pets.py` 从
**Noto Animated Emoji**(Google)下载并规格化生成。

- 来源：https://googlefonts.github.io/noto-emoji-animation/
- 协议：**Creative Commons Attribution 4.0 International (CC BY 4.0)**
  https://creativecommons.org/licenses/by/4.0/
- 修改说明：按内容自动裁边、统一底部对齐、缩放至 256×256，动图重编码降低体积。

CC BY 4.0 **要求署名**。产品界面上的署名在教师控制台「学生与宠物」页
宠物墙下方（`apps/teacher/app.js` 的 `.credit`），另见 `apps/README.md`。
若本目录被单独复制走，请连同本文件一起带上。

前一版素材（Microsoft Fluent Emoji 3D，MIT 协议，无需署名）保留在
`apps/shared/pets-fluent3d/`。换回去需同时改两处物种清单：
`apps/shared/pet.js` 的 `SPECIES_ZH` 与 `apps/api/app/seed.py` 的 `SPECIES`。
"""


def best_frame(frames: list[Image.Image]) -> Image.Image:
    """挑主体最完整的一帧作静态图。

    动图开头常是淡入/缩放的空帧,直接取第 0 帧会拿到半透明的残影。
    用「不透明像素数最多」来选,比按 bbox 面积稳 —— bbox 会被一个飘出去的
    小装饰(星星、水花)撑大。
    """
    if not frames:
        raise ValueError("动图里没有可用帧")
    return max(frames, key=lambda f: sum(1 for a in f.getchannel("A").getdata()
                                         if a > ALPHA_MIN))


def content_box(frame: Image.Image):
    """主体的透明包围盒。"""
    return frame.getchannel("A").point(lambda a: 255 if a > ALPHA_MIN else 0).getbbox()


def union_box(frames: list[Image.Image]):
    """整段动画所有帧的并集包围盒。

    **必须用并集,不能逐帧各裁各的。** 逐帧裁边等于把每一帧都重新居中,
    结果是:① 动作本身被抹平 —— 兔子跳起来的位移变成原地不动;
    ② 帧间差分压缩失效 —— 背景每帧都在微移,编码器只能整帧重存,
    体积反而比原图还大。这两点当初都踩到了。
    """
    boxes = [b for b in (content_box(f) for f in frames) if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def place(frame: Image.Image, box) -> Image.Image:
    """按给定包围盒做裁剪+缩放+底部对齐。同一段动画的每一帧套同一个 box。"""
    if box:
        frame = frame.crop(box)
    k = min(FIT_W / frame.width, FIT_H / frame.height)
    frame = frame.resize((max(1, round(frame.width * k)), max(1, round(frame.height * k))),
                         Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.alpha_composite(frame, ((CANVAS - frame.width) // 2, BOTTOM - frame.height))
    return canvas


def normalize(frame: Image.Image) -> Image.Image:
    """单帧规格化(静态图用):按它自己的包围盒紧裁,主体能占满画布。"""
    return place(frame, content_box(frame))


def read_frames(im: Image.Image) -> tuple[list[Image.Image], list[int]]:
    frames, durations = [], []
    n = 0
    while True:
        try:
            im.seek(n)
        except EOFError:
            break
        frames.append(im.convert("RGBA"))
        durations.append(im.info.get("duration", 40))
        n += 1
    return frames, durations


def reencode_anim(frames: list[Image.Image], durations: list[int], path: str) -> None:
    """把 512px 动图重编码成 256px。

    原图 300KB–1.4MB,而我们最大只显示到约 240px。
    Pillow 与 ffmpeg 都编一遍取小的 —— 实测两者在这批素材上基本打平
    (企鹅 301/304 KB、老虎 496/495 KB、树懒 499/495 KB),但差几 KB 是白捡的,
    而且没装 ffmpeg 时自然就退回 Pillow,不需要单独写降级分支。
    """
    # 抽帧到约 TARGET_FPS。原图多是 30fps、上百帧,而这是一个装饰性循环,
    # 15fps 肉眼几乎无差,体积能省一半 —— 素材总量从 12MB 降到 6MB 上下。
    avg = sum(durations) / len(durations) or 40
    src_fps = 1000 / avg
    step = max(1, round(src_fps / TARGET_FPS))
    frames = frames[::step]
    # 丢帧后每个保留帧要吃掉它顶替掉的那几帧的时长,否则动画会按 step 倍速播放。
    # 只写 durations[::step] 会让 Pillow 那条路快一倍(ffmpeg 那条路由 -framerate
    # 定速,不受影响)—— 于是"播放速度取决于哪个编码器体积更小",必须补上。
    durations = [sum(durations[i:i + step]) or 40
                 for i in range(0, len(durations), step)][:len(frames)]
    # 不取整:33.3fps 抽一半是 16.67,round 成 17 会让整段偏几个百分点。ffmpeg 收小数。
    fps = max(1.0, min(50.0, src_fps / step))

    box = union_box(frames)
    placed = [place(f, box) for f in frames]

    with tempfile.TemporaryDirectory() as d:
        cands = []

        pil_out = os.path.join(d, "pil.webp")
        # method=6 只比 4 小 2–3%,却让整套重新生成从 3 分钟涨到 10 分钟以上
        placed[0].save(pil_out, "WEBP", save_all=True, append_images=placed[1:],
                       duration=durations, loop=0, quality=55, method=4)
        cands.append(pil_out)

        if _HAS_FFMPEG:
            seq = os.path.join(d, "seq")
            os.makedirs(seq, exist_ok=True)
            for i, f in enumerate(placed):
                f.save(os.path.join(seq, f"f{i:04d}.png"))
            ff_out = os.path.join(d, "ff.webp")
            r = subprocess.run(
                # -framerate 足够,不需要再补输出侧的 -r(实测两者输出逐字节相同)
                ["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{fps:.4f}",
                 "-i", os.path.join(seq, "f%04d.png"),
                 "-c:v", "libwebp_anim", "-lossless", "0", "-q:v", "55",
                 "-loop", "0", "-preset", "picture", ff_out],
                capture_output=True)
            if r.returncode == 0 and os.path.exists(ff_out) and os.path.getsize(ff_out) > 0:
                cands.append(ff_out)

        shutil.copyfile(min(cands, key=os.path.getsize), path)


def loop_ms(path_or_img) -> int:
    """动图一个循环的总时长。

    `im.load()` 不能省:Pillow 读 WebP 时,`info["duration"]` 是在解帧过程中
    才填进去的。只 seek 不 load 会读到上一帧的残值或默认值 40ms —— 我就是被
    这一点骗过一次,拿着"每帧 40ms"的假读数去追一个根本不存在的倍速 bug。
    """
    im = Image.open(path_or_img) if isinstance(path_or_img, str) else path_or_img
    total, n = 0, 0
    while True:
        try:
            im.seek(n)
        except EOFError:
            return total
        im.load()
        total += im.info.get("duration", 40)
        n += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已存在也重下")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    client = httpx.Client(timeout=120, follow_redirects=True)

    def one(item):
        name, (cp, zh) = item
        png = os.path.join(OUT, f"{name}.png")
        webp = os.path.join(OUT, f"{name}.webp")
        if not args.force and os.path.exists(png) and os.path.exists(webp):
            return name, zh, os.path.getsize(png), os.path.getsize(webp), "跳过"
        r = client.get(SRC.format(cp=cp))
        if r.status_code != 200:
            return name, zh, 0, 0, f"HTTP {r.status_code}"
        frames, durations = read_frames(Image.open(io.BytesIO(r.content)))
        normalize(best_frame(frames)).save(png, "PNG", optimize=True)
        reencode_anim(frames, durations, webp)
        # 抽帧后必须保持原速。这个坑踩过两次(Pillow 用 durations、ffmpeg 用 -r,
        # 漏掉任一个都会快一倍),所以把校验固化进流水线,而不是靠事后想起来量。
        src_ms, out_ms = sum(durations), loop_ms(webp)
        drift = (out_ms - src_ms) / src_ms if src_ms else 0.0
        note = f"{len(frames)} 帧"
        if abs(drift) > 0.05:
            note += f"  ⚠播放速度偏差 {drift:+.0%}"
        return name, zh, os.path.getsize(png), os.path.getsize(webp), note

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(one, ROSTER.items()))

    manifest, tp, tw, failed = [], 0, 0, []
    for name, zh, p, w, note in sorted(rows):
        if not p:
            failed.append(f"{name}({note})")
            continue
        manifest.append({"id": name, "name": zh,
                         "png": p, "webp": w, "cp": ROSTER[name][0]})
        tp += p
        tw += w
        print(f"  {name:11}{zh:5} 静态 {p // 1024:>3} KB  动图 {w // 1024:>4} KB   {note}")

    json.dump({
        "source": "Noto Animated Emoji (Google)",
        "source_url": "https://googlefonts.github.io/noto-emoji-animation/",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "canvas": CANVAS,
        "species": manifest,
    }, open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8"),
        ensure_ascii=False, indent=2)
    open(os.path.join(OUT, "CREDITS.md"), "w", encoding="utf-8").write(CREDITS)

    print(f"\n{len(manifest)} 个物种 → {OUT}")
    print(f"静态合计 {tp // 1024} KB（网格常驻） · 动图合计 {tw // 1024} KB（按需加载）")
    drifted = [n for n, _, p, _, note in rows if p and "⚠" in note]
    if drifted:
        print("播放速度不对:", ", ".join(drifted))
        return 1
    if failed:
        print("失败:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    # 只在直接运行时接管 stdout。放在模块顶层会让 import 本模块的脚本
    # 被二次包装,原来的 wrapper 被回收时会关掉底层 buffer。
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
