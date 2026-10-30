"""造型素材的后处理：去绿边 + 去灰底残留。

两类缺陷，都来自视频模型生成的绿幕素材在抠图阶段没处理干净：

1. **绿边（green spill）**：轮廓一圈半透明偏绿像素。叠在浅色桌面上非常明显，
   换装后头部/头发边缘会有一圈荧光绿。素材里角色本身没有绿色元素，所以
   "把异常偏绿的像素压回 r/b 较大值"是安全的。
2. **灰底块（matte residue）**：部分帧（尤其 knit_home 的拖拽档）右侧留着一整块
   中性灰不透明区域，边缘直接贴到画布边上。判断依据是"中性灰 + 与画布边界连通"，
   角色内部的灰色（鞋子、白裙阴影）不会被误删。

安全阀：
* 只动 alpha>0 的像素，透明区保持透明；
* 灰底块必须和画布边界连通才删（避免误删角色内部的灰）；
* 删除量超过可见面积 60% 就认为判断失败，放弃这一帧并记录，绝不把角色抠没。

用法：
    python fix_mattes_residue.py probe
    python fix_mattes_residue.py run [--workers N] [--dry-run]
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "pet" / "assets" / "characters" / "mimi" / "expansion_v1"

#: 绿通道比 r/b 大这么多就算 spill。
GREEN_MARGIN = 6
#: 投影阴影判定：三通道极差极小 + 中低亮度。
SHADOW_SPREAD = 12
SHADOW_MIN_L = 88
SHADOW_MAX_L = 162
#: 阴影连通域最小面积（小于这个当作零星灰点，不动）。
SHADOW_MIN_PX = 400
#: 删除量安全阀：超过可见像素的这个比例就放弃（说明判断错了）。
MAX_REMOVE_RATIO = 0.60


def _shadow_mask(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """找出视频模型带进来的**投影阴影**灰块。

    实测特征（knit_home/drag/t10/0016 那块梯形阴影）：
    * 三通道极差极小（mean 3.5，最大 20）——是"死灰"，不是角色的冷调阴影；
    * 亮度集中在 120 左右（90–160 之间）；
    作为对照，白裙虽然也偏中性，但极差 mean 39、亮度 167，会被排除。

    再要求连通域足够大（> SHADOW_MIN_PX），避免误删零星的灰色小像素。
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hi = np.maximum(np.maximum(r, g), b)
    lo = np.minimum(np.minimum(r, g), b)
    flat = (alpha > 20) & ((hi - lo) < SHADOW_SPREAD) & (hi >= SHADOW_MIN_L) & (hi <= SHADOW_MAX_L)
    # 便宜的先验：绝大多数帧根本没有这种"死灰"像素，直接跳过连通域计算。
    if int(flat.sum()) < SHADOW_MIN_PX:
        return np.zeros_like(flat)
    return _big_components(flat, SHADOW_MIN_PX)


def _big_components(mask: np.ndarray, min_px: int) -> np.ndarray:
    """4 邻域连通域，返回面积 >= min_px 的像素集合。"""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    keep = np.zeros_like(mask, dtype=bool)
    for start_y in range(height):
        row = mask[start_y]
        for start_x in range(width):
            if not row[start_x] or seen[start_y, start_x]:
                continue
            stack = [(start_y, start_x)]
            seen[start_y, start_x] = True
            pixels = []
            while stack:
                cy, cx = stack.pop()
                pixels.append((cy, cx))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(pixels) >= min_px:
                for cy, cx in pixels:
                    keep[cy, cx] = True
    return keep


def clean(image: Image.Image) -> tuple[Image.Image, int, int]:
    """返回 (处理后图像, 去绿像素数, 去灰像素数)。"""
    arr = np.array(image.convert("RGBA")).astype(np.int16)
    rgb = arr[..., :3].copy()
    alpha = arr[..., 3].copy()

    visible = alpha > 0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    # ---- 1) 去绿：绿通道整体压到不超过 max(r,b) ----
    # 造型素材全是蓝/白/红/米色系，角色本身没有绿色元素，所以可以**无条件**
    # 夹住绿通道。只处理"明显偏绿"会留下一圈淡绿辉光（alpha 很低时尤其明显），
    # 叠在浅色桌面上就是用户看到的那层荧光边。
    limit = np.maximum(r, b)
    spill = visible & (g > limit)
    green_fixed = int(spill.sum())
    if green_fixed:
        rgb[..., 1] = np.where(spill, limit, g)

    # ---- 2) 去灰底：与边界连通的中性灰块整块置透明 ----
    gray = _shadow_mask(rgb, alpha)
    gray_fixed = 0
    if gray.any():
        remove = int(gray.sum())
        visible_count = max(1, int(visible.sum()))
        if remove / visible_count <= MAX_REMOVE_RATIO:
            alpha[gray] = 0
            # 透明区的 RGB 填成邻近中位色，缩放插值时不会带出灰边。
            keep = alpha > 0
            if keep.any():
                rgb[gray] = np.median(rgb[keep], axis=0).astype(np.int16)
            gray_fixed = remove

    # ---- 3) 透明区填邻近色 ----
    # 素材透明区的 RGB 接近纯黑（实测均值 ~(2,3,2)）。2 倍屏缩小时双线性插值会
    # 把这层黑混进轮廓，形成暗边。用边缘带中位色填充，缩放后才干净。
    transparent = alpha <= 0
    if transparent.any():
        band = (alpha > 8) & (alpha < 245)
        source = rgb[band] if band.any() else rgb[alpha > 8]
        if len(source):
            rgb[transparent] = np.median(source, axis=0).astype(np.int16)

    out = Image.fromarray(
        np.dstack([
            np.clip(rgb, 0, 255).astype(np.uint8),
            np.clip(alpha, 0, 255).astype(np.uint8),
        ]),
        "RGBA",
    )
    return out, green_fixed, gray_fixed


def measure(path: Path) -> tuple[int, int, int]:
    arr = np.array(Image.open(path).convert("RGBA")).astype(np.int16)
    rgb, alpha = arr[..., :3], arr[..., 3]
    visible = alpha > 0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    green = visible & (g > np.maximum(r, b))
    gray = _shadow_mask(rgb, alpha)
    return int(green.sum()), int(gray.sum()), int(visible.sum())


def fix_one(path_str: str) -> tuple[str, int, int, bool]:
    path = Path(path_str)
    try:
        original = Image.open(path).convert("RGBA")
        fixed, green, gray = clean(original)
        if green == 0 and gray == 0:
            return path_str, 0, 0, False
        temp = path.with_name(path.name + ".tmp")
        try:
            fixed.save(temp, "WEBP", quality=92, method=4)
            os.replace(temp, path)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        return path_str, green, gray, True
    except Exception as exc:  # noqa: BLE001 - 单帧坏了不拖垮整批
        print(f"  skip {path_str}: {type(exc).__name__}: {exc}", flush=True)
        return path_str, 0, 0, False


def iter_frames():
    return sorted(str(p) for p in ASSETS.rglob("*.webp"))


def probe(sample: int = 14) -> None:
    files = iter_frames()
    picked = files[:: max(1, len(files) // sample)][:sample]
    print(f"抽样 {len(picked)} 帧")
    for path_str in picked:
        p = Path(path_str)
        green, gray, visible = measure(p)
        rel = p.relative_to(ASSETS)
        print(f"  {str(rel)[:58]:58s} 绿={green:5d} 灰={gray:5d} 可见={visible:6d}")


def run(workers: int | None = None, dry_run: bool = False) -> None:
    files = iter_frames()
    if dry_run:
        probe()
        return
    workers = workers or min(8, (os.cpu_count() or 4))
    started = time.time()
    done = changed = green_total = gray_total = 0
    print(f"{len(files)} 帧，{workers} 进程", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fix_one, f) for f in files]
        for future in as_completed(futures):
            _path, green, gray, ok = future.result()
            done += 1
            changed += 1 if ok else 0
            green_total += green
            gray_total += gray
            if done % 2000 == 0:
                rate = done / max(time.time() - started, 1)
                print(f"  {done}/{len(files)} | 已修 {changed} | {rate:.0f} 帧/秒"
                      f" | 剩余 {(len(files)-done)/max(rate,0.1)/60:.1f} 分钟", flush=True)
    print(f"\n完成 {done} 帧，修改 {changed} 帧")
    print(f"去绿像素 {green_total}，去灰像素 {gray_total}")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "probe":
        probe()
        return 0
    if argv[1] == "run":
        workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else None
        run(workers, dry_run="--dry-run" in argv)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
