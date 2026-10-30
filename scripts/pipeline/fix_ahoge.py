"""打开被"补针孔"闭运算封死的呆毛圈洞。

早期修绿边时用了形态学闭运算补轮廓针孔，副作用是把呆毛那圈 5–6px 的
透空区域一起焊死了，看起来像一块实心黑。

做法：在角色顶部 16% 的带状区域里，找"近纯黑"（亮度 <30）且完全不透明的
像素——那就是被填死的洞；做一次开运算去掉孤立点，把这些像素的 alpha 清零，
再轻微羽化。对本来就有洞的帧，黑像素数量少，自动跳过。

用法：python fix_ahoge.py [run]
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "pet/assets/characters/mimi/expansion_v1"
LUM_THRESHOLD = 30.0
MIN_BLOB_PX = 80


def open_hole(path_str: str) -> bool:
    try:
        image = Image.open(path_str).convert("RGBA")
        array = np.array(image).astype(np.int16)
        alpha = array[..., 3]
        ys, xs = np.where(alpha > 8)
        if len(ys) == 0:
            return False
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
        band = int((y1 - y0) * 0.16)
        if band < 8:
            return False
        area = np.zeros_like(alpha, dtype=bool)
        area[max(0, y0 - 2):y0 + band, x0:x1 + 1] = True

        rgb = array[..., :3]
        lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        blob = (lum < LUM_THRESHOLD) & area & (alpha > 200)
        if int(blob.sum()) < MIN_BLOB_PX:
            return False

        # 不做开运算：洞本身只有 2–3px 厚，3×3 腐蚀会把它整个抹掉。
        new_alpha = alpha.copy()
        new_alpha[blob] = 0
        smoothed = Image.fromarray(np.clip(new_alpha, 0, 255).astype(np.uint8), "L")
        smoothed = smoothed.filter(ImageFilter.GaussianBlur(0.6))

        out = np.dstack([rgb.astype(np.uint8), np.array(smoothed)])
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


def main() -> None:
    files = sorted(str(p) for p in ASSETS.rglob("*.webp"))
    if len(sys.argv) < 2 or sys.argv[1] != "run":
        hits = sum(1 for f in files[:400] if open_hole(f))
        print(f"前 400 帧中需要开洞: {hits}")
        return
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    started = time.time()
    done = changed = 0
    print(f"{len(files)} 帧，{workers} 进程", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(open_hole, f) for f in files]
        for fut in as_completed(futures):
            done += 1
            changed += 1 if fut.result() else 0
            if done % 2000 == 0:
                rate = done / max(time.time() - started, 1)
                print(f"  {done}/{len(files)} | 开洞 {changed} | {rate:.0f} 帧/秒"
                      f" | 剩余 {(len(files)-done)/max(rate,0.1)/60:.1f} 分钟", flush=True)
    print(f"完成 {done} 帧，开洞 {changed} 帧")


if __name__ == "__main__":
    main()
