"""Mimi 素材产线：生图 / 生视频 / 抠像 / 对齐 / 导出 webp / 写 manifest。

所有外部调用都可重试，产物落盘后可跳过已完成任务，中断可续跑。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

CRED = Path.home() / ".dsh" / ".credentials.yaml"
IMG_BASE = "https://api.lordfa.top/v1"
IMG_MODEL = "gpt-image-2.5-sunburst"

VID_BASE = os.environ.get(
    "MIMI_VIDEO_BASE", "https://lobby-clicking-poker-absolutely.trycloudflare.com"
)
VID_TOK = os.environ.get("MIMI_VIDEO_TOK", "1667c0d059ba4c51")

WORK = Path(os.environ.get("MIMI_WORK", "/tmp/mimi_work"))
IMG_DIR = WORK / "img"
VID_DIR = WORK / "vid"
RAW_DIR = WORK / "frames"
OUT_DIR = WORK / "out"
for d in (IMG_DIR, VID_DIR, RAW_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

CANVAS = (512, 768)
PIVOT = (256, 744)
TARGET_CHAR_H = 425          # 与现有素材一致的角色包围盒高度
FRAME_MS = 83                # 24fps 抽隔帧


# --------------------------------------------------------------------- 凭据

def lordfa_key() -> str:
    raw = CRED.read_text(encoding="utf-8")
    m = re.search(r"^\s+LORDFA_API_KEY:\s*(\S+)\s*$", raw, re.M)
    if not m:
        raise RuntimeError("LORDFA_API_KEY not found")
    return m.group(1)


def img_headers() -> dict:
    return {"Authorization": f"Bearer {lordfa_key()}"}


def vid_headers() -> dict:
    return {"Authorization": f"Bearer {VID_TOK}"}


# --------------------------------------------------------------------- 生图

def edit_image(ref: Path, prompt: str, *, size: str = "1024x1536",
               transparent: bool = True, out: Path | None = None,
               retries: int = 3) -> Path:
    """以参考图改服装，保持角色一致性。返回 PNG 路径。"""
    data = {"model": IMG_MODEL, "prompt": prompt, "n": "1", "size": size}
    if transparent:
        data["background"] = "transparent"
        data["output_format"] = "png"
    last = None
    for attempt in range(retries):
        try:
            with ref.open("rb") as fh:
                r = requests.post(
                    f"{IMG_BASE}/images/edits", headers=img_headers(),
                    files={"image": (ref.name, fh, "image/png")},
                    data=data, timeout=300,
                )
            if r.status_code == 200:
                item = r.json()["data"][0]
                blob = base64.b64decode(item["b64_json"])
                dest = out or (IMG_DIR / f"{ref.stem}-edit-{int(time.time())}.png")
                dest.write_bytes(blob)
                return dest
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:  # noqa: BLE001 - 网络抖动，重试
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"image edit failed: {last}")


def compose_first_frame(char_png: Path, out: Path, *, bg=(0, 255, 0),
                        char_h: int = 600, canvas=(480, 640)) -> Path:
    """把透明角色图放到纯色底上，作为视频首帧。"""
    im = Image.open(char_png).convert("RGBA")
    a = np.array(im)
    ys, xs = np.where(a[..., 3] > 8)
    if len(xs) == 0:
        raise ValueError(f"empty sprite: {char_png}")
    im = im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    scale = char_h / im.height
    im = im.resize((max(1, int(im.width * scale)), char_h), Image.LANCZOS)
    W, H = canvas
    plate = Image.new("RGB", (W, H), bg)
    plate.paste(im, ((W - im.width) // 2, H - char_h - 22), im)
    plate.save(out)
    return out


# -------------------------------------------------------------------- 生视频

def submit_video(first_frame: Path, prompt: str, *, duration: float = 5.0,
                 aspect: str = "3:4", seed: int | None = None,
                 short_edge: int = 480, retries: int = 3) -> str:
    img = base64.b64encode(first_frame.read_bytes()).decode()
    payload = {
        "task": "fl2va",
        "prompt": prompt,
        "conditions": [{"type": "first_frame", "image_base64": img}],
        "target": {"short_edge": short_edge, "aspect_ratio": aspect,
                   "duration_seconds": duration},
        "quality": "turbo8",
    }
    if seed is not None:
        payload["seed"] = seed
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(f"{VID_BASE}/v1/videos", headers=vid_headers(),
                              json=payload, timeout=120)
            if r.status_code == 200:
                return r.json()["id"]
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"video submit failed: {last}")


def wait_video(job_id: str, *, timeout_s: float = 900, poll: float = 5.0) -> dict:
    start = time.time()
    while time.time() - start < timeout_s:
        r = requests.get(f"{VID_BASE}/v1/videos/{job_id}", headers=vid_headers(),
                         timeout=30)
        if r.status_code == 200:
            s = r.json()
            if s["status"] == "completed":
                return s
            if s["status"] == "failed":
                raise RuntimeError(f"video failed: {s.get('error')}")
        time.sleep(poll)
    raise TimeoutError(f"video {job_id} timed out")


def download_video(job_id: str, out: Path) -> Path:
    r = requests.get(f"{VID_BASE}/v1/videos/{job_id}/content",
                     headers=vid_headers(), timeout=300)
    r.raise_for_status()
    out.write_bytes(r.content)
    return out


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_frames(mp4: Path, out_dir: Path, *, step: int = 2) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(mp4),
         "-vf", f"select='not(mod(n\\,{step}))'", "-vsync", "0",
         str(out_dir / "f_%04d.png")],
        check=True,
    )
    return sorted(out_dir.glob("f_*.png"))


# ------------------------------------------------------------------ 抠像对齐

def chroma_alpha(im: Image.Image, bg=(0, 255, 0), tol: int = 60) -> Image.Image:
    """绿幕抠图；返回 RGBA。非绿像素保持原样。"""
    a = np.array(im.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    br, bgc, bb = bg
    dist = np.sqrt((r - br).astype(np.float64) ** 2 + (g - bgc) ** 2 + (b - bb) ** 2)
    is_bg = dist < tol
    # 绿边软过渡
    alpha = np.where(is_bg, 0, 255).astype(np.uint8)
    soft = (~is_bg) & (dist < tol * 2.2) & (g > r + 25) & (g > b + 25)
    alpha[soft] = 128
    out = np.dstack([a.astype(np.uint8), alpha])
    return Image.fromarray(out, "RGBA")


def bbox(im: Image.Image) -> tuple[int, int, int, int] | None:
    a = np.array(im)
    ys, xs = np.where(a[..., 3] > 8)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def register_frames(sprites: list[Image.Image], *, target_h: int = TARGET_CHAR_H,
                    canvas: tuple[int, int] = CANVAS,
                    pivot: tuple[int, int] = PIVOT) -> list[Image.Image]:
    """统一等比缩放 + 全局脚底/中心对齐。

    缩放和偏移只算一次（取各帧包围盒的中位数），因此动作本身的位移、
    抬手、前倾都会保留，只去掉整段视频的漂移。
    """
    good = [b for b in (bbox(s) for s in sprites) if b]
    if not good:
        raise ValueError("no visible sprite")
    med_h = float(np.median([b[3] - b[1] + 1 for b in good]))
    scale = target_h / med_h

    resized = []
    for s in sprites:
        w = max(1, int(round(s.width * scale)))
        h = max(1, int(round(s.height * scale)))
        resized.append(s.resize((w, h), Image.LANCZOS))

    good2 = [b for b in (bbox(s) for s in resized) if b]
    if not good2:
        raise ValueError("no visible sprite after scale")
    med_bottom = float(np.median([b[3] for b in good2]))
    med_cx = float(np.median([(b[0] + b[2]) / 2 for b in good2]))

    off_x = int(round(pivot[0] - med_cx))
    off_y = int(round(pivot[1] - med_bottom))

    W, H = canvas
    placed = []
    for s in resized:
        frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if bbox(s):
            frame.paste(s, (off_x, off_y), s)
        placed.append(frame)
    return placed


def save_webp(frames: list[Image.Image], out_dir: Path, *, quality: int = 95) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.webp"):
        old.unlink()
    paths = []
    for i, fr in enumerate(frames, 1):
        p = out_dir / f"{i:04d}.webp"
        fr.save(p, "WEBP", quality=quality, method=4, lossless=False)
        paths.append(p)
    return paths


def pingpong(frames: list[Image.Image]) -> list[Image.Image]:
    if len(frames) < 3:
        return frames
    return frames + frames[-2:0:-1]


def build_action_from_video(mp4: Path, out_dir: Path, *, step: int = 2,
                            loop: bool = False, target_h: int = TARGET_CHAR_H,
                            bg=(0, 255, 0)) -> list[Path]:
    raw = extract_frames(mp4, RAW_DIR / out_dir.name, step=step)
    if not raw:
        raise ValueError("no frames extracted")
    sprites = [chroma_alpha(Image.open(p), bg=bg) for p in raw]
    # 丢掉明显空帧
    sprites = [s for s in sprites if bbox(s)]
    placed = register_frames(sprites, target_h=target_h)
    if loop:
        placed = pingpong(placed)
    return save_webp(placed, out_dir)
