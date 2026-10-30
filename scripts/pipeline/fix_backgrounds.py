"""去掉生成帧里"没被绿幕抠掉"的纯色矩形背景。

有些视频帧的背景不是绿色（黑 / 粉 / 黄绿 / 灰），早期的绿幕抠图对它们完全无效，
整块背景被留成了不透明。这里按"矩形背景"处理：

1. 不透明区域的包围盒四角如果都是同一种颜色 → 认定是矩形背景；
2. 以该颜色做容差掩码，从四角泛洪，只清除与边缘连通的背景区域
   （角色内部的同色描边不会被误伤）；
3. 羽化边缘，并按背景色做去色（un-matte），避免边缘带背景色。

用法：
    python fix_backgrounds.py probe
    python fix_backgrounds.py run
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "pet/assets/characters/mimi/expansion_v1"
TOL = 34.0          # 与背景色的距离阈值
FEATHER = 0.7


def detect(a: np.ndarray):
    h, w = a.shape[:2]
    alpha = a[..., 3]
    opaque = alpha > 200
    if opaque.sum() < 0.08 * h * w:
        return None
    ys, xs = np.where(opaque)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    if y1 - y0 < 40 or x1 - x0 < 40:
        return None
    corners = [(y0 + 2, x0 + 2), (y0 + 2, x1 - 2), (y1 - 2, x0 + 2), (y1 - 2, x1 - 2)]
    if any(a[y, x, 3] <= 200 for y, x in corners):
        return None
    cols = np.array([a[y, x, :3] for y, x in corners]).astype(np.int16)
    med = np.median(cols, axis=0)
    if float(np.abs(cols - med).mean()) > 12:
        return None
    fill = opaque.sum() / ((y1 - y0 + 1) * (x1 - x0 + 1))
    if fill < 0.9:
        return None
    return tuple(med.round().astype(int)), (x0, y0, x1, y1)


def remove_background(image: Image.Image) -> Image.Image | None:
    a = np.array(image.convert("RGBA")).astype(np.int16)
    found = detect(a)
    if found is None:
        return None
    color, (x0, y0, x1, y1) = found
    rgb = a[..., :3].astype(np.float32)
    alpha = a[..., 3].astype(np.float32)

    dist = np.sqrt(((rgb - np.array(color, dtype=np.float32)) ** 2).sum(axis=2))
    near = dist < TOL

    # 只清与矩形边缘连通的背景，保护角色内部的同色描边
    mask = Image.fromarray((near * 255).astype(np.uint8), "L")
    work = mask.copy()
    # 背景可能带一圈描边，单纯从四角取种子会采到描边导致泛洪不启动；
    # 沿四条边各取若干点，任意一个落在背景色上就能把整块泛洪出来。
    seeds = []
    for t in (0.02, 0.1, 0.25, 0.5, 0.75, 0.9, 0.98):
        seeds.append((int(x0 + (x1 - x0) * t), y0 + 3))
        seeds.append((int(x0 + (x1 - x0) * t), y1 - 3))
        seeds.append((x0 + 3, int(y0 + (y1 - y0) * t)))
        seeds.append((x1 - 3, int(y0 + (y1 - y0) * t)))
    for seed in seeds:
        if work.getpixel(seed) == 255:
            ImageDraw.floodfill(work, seed, 128)
    background = np.array(work) == 128

    new_alpha = alpha.copy()
    new_alpha[background] = 0.0
    # 羽化：先转成 0/255 再用高斯模糊得到抗锯齿边
    alpha_img = Image.fromarray(new_alpha.astype(np.uint8), "L")
    alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(FEATHER))
    new_alpha = np.array(alpha_img).astype(np.float32)

    # un-matte：去掉半透明像素里混入的背景色
    bg = np.array(color, dtype=np.float32)
    a_ratio = np.clip(new_alpha[..., None] / 255.0, 0.0, 1.0)
    unmatted = (rgb - (1.0 - a_ratio) * bg) / np.maximum(a_ratio, 0.08)
    rgb = np.where(a_ratio > 0.02, unmatted, rgb)
    rgb = np.clip(rgb, 0, 255)

    out = np.dstack([rgb.astype(np.uint8), np.clip(new_alpha, 0, 255).astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


def repair_residual(path_str: str) -> bool:
    """修补"背景已去掉、但四周还留一圈 1–2px 描边"的帧。"""
    try:
        image = Image.open(path_str).convert("RGBA")
        a = np.array(image).astype(np.int16)
        alpha = a[..., 3]
        ys, xs = np.where(alpha > 8)
        if len(xs) == 0:
            return False
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
        if x1 - x0 < 150 or (alpha[y0, x0:x1 + 1] > 8).mean() < 0.8:
            return False
        row = a[y0, x0:x1 + 1]
        row = row[row[:, 3] > 8][:, :3]
        if len(row) == 0:
            return False
        bg = np.median(row, axis=0).astype(np.float32)

        rgb = a[..., :3].astype(np.float32)
        dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
        near = dist < 70
        mask = Image.fromarray((near * 255).astype(np.uint8), "L")
        work = mask.copy()
        seeds = []
        for t in (0.02, 0.1, 0.3, 0.5, 0.7, 0.9, 0.98):
            seeds += [(int(x0 + (x1 - x0) * t), y0 + 1),
                      (int(x0 + (x1 - x0) * t), y1 - 1),
                      (x0 + 1, int(y0 + (y1 - y0) * t)),
                      (x1 - 1, int(y0 + (y1 - y0) * t))]
        for seed in seeds:
            if 0 <= seed[0] < mask.width and 0 <= seed[1] < mask.height and work.getpixel(seed) == 255:
                ImageDraw.floodfill(work, seed, 128)
        background = np.array(work) == 128
        # 膨胀 2px，把残留的描边一起吃掉
        grown = Image.fromarray((background * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(5))
        background = np.array(grown) > 128

        new_alpha = alpha.astype(np.float32)
        new_alpha[background] = 0
        alpha_img = Image.fromarray(np.clip(new_alpha, 0, 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(0.8))
        new_alpha = np.array(alpha_img).astype(np.float32)

        ratio = np.clip(new_alpha[..., None] / 255.0, 0.0, 1.0)
        unmatted = (rgb - (1.0 - ratio) * bg) / np.maximum(ratio, 0.08)
        rgb = np.where(ratio > 0.02, unmatted, rgb)
        out = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8),
                         np.clip(new_alpha, 0, 255).astype(np.uint8)])
        temp = Path(path_str + ".tmp")
        try:
            Image.fromarray(out, "RGBA").save(temp, "WEBP", quality=80, method=4)
            os.replace(temp, path_str)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        return True
    except Exception:  # noqa: BLE001
        return False


def repair_all() -> None:
    fixed = 0
    for f in iter_frames():
        if repair_residual(f):
            fixed += 1
    print(f"修补残框 {fixed} 帧")


def iter_frames():
    return sorted(str(p) for p in ASSETS.rglob("*.webp"))


def fix_one(path_str: str):
    try:
        image = Image.open(path_str).convert("RGBA")
        fixed = remove_background(image)
        if fixed is None:
            return path_str, False
        temp = Path(path_str + ".tmp")
        try:
            fixed.save(temp, "WEBP", quality=80, method=4)
            os.replace(temp, path_str)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        return path_str, True
    except Exception:  # noqa: BLE001
        return path_str, False


def probe() -> None:
    hits = []
    for f in iter_frames():
        a = np.array(Image.open(f).convert("RGBA"))
        r = detect(a)
        if r:
            hits.append((f, r[0]))
    print(f"检测到 {len(hits)} 帧带矩形背景")
    for f, c in hits[:8]:
        print(" ", Path(f).relative_to(ASSETS), c)


def run(workers: int = 6) -> None:
    files = iter_frames()
    started = time.time()
    done = fixed_n = 0
    print(f"{len(files)} 帧，{workers} 进程", flush=True)
    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fix_one, f) for f in files]
        for fut in as_completed(futures):
            _p, ok = fut.result()
            done += 1
            fixed_n += 1 if ok else 0
            if done % 3000 == 0:
                rate = done / max(time.time() - started, 1)
                print(f"  {done}/{len(files)} | 修复 {fixed_n} | {rate:.0f} 帧/秒", flush=True)
    print(f"完成 {done} 帧，修复 {fixed_n} 帧")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "repair":
        repair_all()
    elif len(sys.argv) > 1 and sys.argv[1] == "run":
        run(int(sys.argv[2]) if len(sys.argv) > 2 else 6)
    else:
        probe()
