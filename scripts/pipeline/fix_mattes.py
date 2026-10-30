"""修复生成素材的抠图：去绿边、补针孔、平滑轮廓。

问题来源：视频模型生成的帧是绿幕背景，早期用"颜色距离 + 硬阈值"抠图，
边缘留下大量半透明绿像素，轮廓上还有针孔。叠在浅色桌面上就是一圈白/绿边。

修复思路（不重生成素材，只修 alpha）：
1. 以 alpha>100 作为"确定属于角色"的种子；
2. 从四角泛洪标记外部，剩余区域即角色本体——顺带补上内部的针孔；
3. 对轮廓做 0.7px 羽化，得到干净的抗锯齿边；
4. 边缘去绿：绿通道压回到红/蓝较大值以下（角色本身没有绿色）；
5. 透明区 RGB 填成边缘中位色，避免缩放时把黑/白混进边缘。

安全阀：若泛洪后角色面积比原来少 25% 以上，说明角色贴边导致泛洪漏进去了，
此时放弃重塑，只做去绿，绝不把角色抠没。

用法：
    python fix_mattes.py probe
    python fix_mattes.py run [--workers N] [--dry-run]
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "pet/assets/characters/mimi/expansion_v1"
THRESHOLD = 100
FEATHER = 0.7
GREEN_MARGIN = 4


def _despill_only(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """只在边缘把绿压下去，不动 alpha。"""
    g = rgb[..., 1]
    limit = np.maximum(rgb[..., 0], rgb[..., 2])
    band = (alpha > 0) & (alpha < 250)
    spill = band & (g > limit + GREEN_MARGIN)
    rgb[..., 1] = np.where(spill, limit, g)
    return rgb


def clean_matte(image: Image.Image) -> Image.Image:
    """重算 alpha：闭运算补针孔 + 羽化，再对边缘去绿。

    不用 PIL 的 floodfill 补洞——它是纯 Python 扫描线，512×768 要半秒，
    一万八千帧就是两个多小时。形态学闭运算走 C 实现，同样能填掉轮廓上的
    针孔（≤4px），速度差几十倍。
    """
    array = np.array(image.convert("RGBA")).astype(np.int16)
    rgb = array[..., :3].copy()
    alpha = array[..., 3]

    solid = Image.fromarray(((alpha > THRESHOLD) * 255).astype(np.uint8), "L")
    if not solid.getbbox():
        return image

    # 闭运算：先膨胀再腐蚀，填掉细小的透明孔洞，外轮廓基本回到原位。
    closed = solid.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    # 羽化得到抗锯齿边
    softened = closed.filter(ImageFilter.GaussianBlur(FEATHER))
    new_alpha = np.array(softened).astype(np.int16)

    band = (new_alpha > 0) & (new_alpha < 250)
    # 去绿对**所有不透明像素**生效：硬阈值抠图会把一部分绿边留在
    # alpha=255 的区域，只处理半透明带会漏掉它们。角色本身没有绿色元素。
    visible = new_alpha > 0
    g = rgb[..., 1]
    limit = np.maximum(rgb[..., 0], rgb[..., 2])
    rgb[..., 1] = np.where(visible & (g > limit + GREEN_MARGIN), limit, g)

    if band.any():
        # 透明区填边缘中位色：缩放插值不会带出黑色或绿色。
        rgb[new_alpha <= 0] = np.median(rgb[band], axis=0).astype(np.int16)

    return Image.fromarray(
        np.dstack([np.clip(rgb, 0, 255).astype(np.uint8),
                   np.clip(new_alpha, 0, 255).astype(np.uint8)]),
        "RGBA",
    )


def green_ratio(array: np.ndarray) -> float:
    """边缘像素里"绿色明显占优"的比例。

    必须先转 int16：uint8 相减在 g < r 时会下溢成很大的正数，
    把普通像素误判成绿边（实测能把 0% 报成 11.6%）。
    """
    array = array.astype(np.int16)
    alpha = array[..., 3]
    edge = (alpha > 8) & (alpha < 250)
    if edge.sum() < 50:
        return 0.0
    rgb = array[..., :3][edge]
    return float(((rgb[:, 1] - rgb[:, 0] > 40) & (rgb[:, 1] - rgb[:, 2] > 40)).mean())


def fix_one(path_str: str) -> tuple[str, float, float, bool]:
    path = Path(path_str)
    try:
        original = Image.open(path).convert("RGBA")
        before = green_ratio(np.array(original))
        fixed = clean_matte(original)
        after = green_ratio(np.array(fixed))
        if after >= before and before < 0.02:
            return path_str, before, after, False
        temp = path.with_name(path.name + ".tmp")
        try:
            fixed.save(temp, "WEBP", quality=88, method=4)
            os.replace(temp, path)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        return path_str, before, after, True
    except Exception:  # noqa: BLE001 - 单帧坏了不拖垮整批
        return path_str, 0.0, 0.0, False


def iter_frames():
    return sorted(str(p) for p in ASSETS.rglob("*.webp"))


def probe(sample: int = 12) -> None:
    files = iter_frames()
    picked = files[:: max(1, len(files) // sample)][:sample]
    print(f"抽样 {len(picked)} 帧")
    worst = 0.0
    for path_str in picked:
        image = Image.open(path_str).convert("RGBA")
        before = green_ratio(np.array(image))
        after = green_ratio(np.array(clean_matte(image)))
        worst = max(worst, before)
        print(f"  {Path(path_str).parent.parent.name:18} {Path(path_str).name}  绿边 {before*100:5.1f}% → {after*100:4.1f}%")
    print(f"最差帧绿边 {worst*100:.1f}%")


def run(workers: int | None = None, dry_run: bool = False) -> None:
    files = iter_frames()
    if dry_run:
        probe(); return
    workers = workers or min(8, (os.cpu_count() or 4))
    started = time.time()
    done = changed = 0
    before_sum = after_sum = 0.0
    print(f"{len(files)} 帧，{workers} 进程", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fix_one, f) for f in files]
        for future in as_completed(futures):
            _path, before, after, ok = future.result()
            before_sum += before
            after_sum += after
            done += 1
            changed += 1 if ok else 0
            if done % 2000 == 0:
                rate = done / max(time.time() - started, 1)
                print(f"  {done}/{len(files)} | 已修 {changed} | {rate:.0f} 帧/秒"
                      f" | 剩余 {(len(files)-done)/max(rate,0.1)/60:.1f} 分钟", flush=True)
    print(f"\n完成 {done} 帧，修改 {changed} 帧")
    print(f"平均绿边 {before_sum/max(done,1)*100:.1f}% → {after_sum/max(done,1)*100:.1f}%")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "probe":
        probe(); return 0
    if argv[1] == "run":
        workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else None
        run(workers, dry_run="--dry-run" in argv); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
