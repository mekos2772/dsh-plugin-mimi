"""把扩展素材压回与正式素材同一档的体积标准（多进程）。

正式素材实测：512×768 RGBA WebP，平均 40.8 KB/帧。
扩展素材在 q95 下平均 55–57 KB，因为视频模型生成的帧带压缩噪点，熵更高。

实测选型（20 帧抽样）：
    q88 method=4 → 42.0 KB/帧，像素误差 0.51/255
取 q88：体积最接近原标准，肉眼无差。

18k 帧单进程要 30+ 分钟，这里用进程池按 CPU 核数并行；
编码是纯 C 计算，进程比线程更能吃满多核。原文件编码失败或变大时保持不变。

用法：
    python compress_assets.py probe
    python compress_assets.py run [--workers N]
"""
from __future__ import annotations

import io
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "pet/assets/characters/mimi/expansion_v1"
QUALITY = 88
METHOD = 4


def encode_bytes(frame: Image.Image) -> bytes:
    buf = io.BytesIO()
    frame.save(buf, format="WEBP", quality=QUALITY, method=METHOD)
    return buf.getvalue()


def compress_one(path_str: str) -> tuple[str, int, int]:
    """返回 (路径, 原始字节, 新字节)。失败时新旧相同。"""
    path = Path(path_str)
    try:
        original = path.stat().st_size
        frame = Image.open(path).convert("RGBA")
        data = encode_bytes(frame)
        if len(data) < original and len(data) > 0:
            # 原子写：先写同目录临时文件再替换。直接 write_bytes 被中断
            # 会留下 0 字节文件，启动自检报"损坏"，那一帧永远画不出来。
            temp = path.with_name(path.name + ".tmp")
            try:
                temp.write_bytes(data)
                os.replace(temp, path)
            finally:
                if temp.exists():
                    try:
                        temp.unlink()
                    except OSError:
                        pass
            return path_str, original, len(data)
        return path_str, original, original
    except Exception:  # noqa: BLE001 - 单帧坏了不拖垮整批
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return path_str, size, size


def iter_frames():
    return sorted(str(p) for p in ASSETS.rglob("*.webp"))


def probe(sample: int = 20) -> None:
    files = iter_frames()
    picked = files[:: max(1, len(files) // sample)][:sample]
    before = after = 0
    errs = []
    for path_str in picked:
        path = Path(path_str)
        before += path.stat().st_size
        frame = Image.open(path).convert("RGBA")
        data = encode_bytes(frame)
        after += min(len(data), path.stat().st_size)
        decoded = np.array(Image.open(io.BytesIO(data)).convert("RGBA")).astype(np.int16)
        errs.append(float(np.abs(decoded - np.array(frame).astype(np.int16)).mean()))
    total = sum(Path(f).stat().st_size for f in files)
    print(f"抽样 {len(picked)} 帧")
    print(f"  {before/1024/len(picked):.1f} KB/帧 → {after/1024/len(picked):.1f} KB/帧")
    print(f"  预计 {total/1024/1024:.0f} MB → {total*after/before/1024/1024:.0f} MB")
    print(f"  平均像素误差 {np.mean(errs):.2f}/255")


def run(workers: int | None = None) -> None:
    files = iter_frames()
    workers = workers or min(10, (os.cpu_count() or 4))
    started = time.time()
    before_total = after_total = done = 0
    print(f"{len(files)} 帧，{workers} 进程", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(compress_one, f) for f in files]
        for future in as_completed(futures):
            _path, before, after = future.result()
            before_total += before
            after_total += after
            done += 1
            if done % 2000 == 0:
                elapsed = time.time() - started
                rate = done / max(elapsed, 1)
                print(f"  {done}/{len(files)} | 已省 {(before_total-after_total)/1024/1024:.0f} MB"
                      f" | {rate:.0f} 帧/秒 | 剩余 {(len(files)-done)/max(rate,0.1)/60:.1f} 分钟",
                      flush=True)
    print(f"\n完成 {len(files)} 帧")
    print(f"体积 {before_total/1024/1024:.0f} MB → {after_total/1024/1024:.0f} MB"
          f"（省 {(before_total-after_total)/1024/1024:.0f} MB,"
          f" {(1-after_total/max(1,before_total))*100:.0f}%）")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "probe":
        probe()
        return 0
    if argv[1] == "run":
        workers = None
        if "--workers" in argv:
            workers = int(argv[argv.index("--workers") + 1])
        run(workers)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
