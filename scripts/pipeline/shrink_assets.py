"""把扩展素材整体降采样，进一步缩小体积。

素材是 512×768，但桌宠默认显示只有 256×384（最大 320×480，缩放上限 640 高）。
512 宽相当于 2 倍超采样，对 Q 版平涂画面是浪费。

这里统一降到 320×480（默认显示 256×384 仍有 1.25 倍超采样），并按 q78 重编码。
manifest 里的 canvas / ground_pivot 同步改写，否则注册比例会错。

用法：
    python shrink_assets.py probe
    python shrink_assets.py run [--workers N]
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "pet/assets/characters/mimi/expansion_v1"
TARGET = (320, 480)
QUALITY = 78
METHOD = 4
#: 与 512×768 的 0.5 / 0.96875 比例保持一致
PIVOT = (160, 465)


def shrink_one(path_str: str) -> tuple[str, int, int, bool]:
    path = Path(path_str)
    try:
        original_size = path.stat().st_size
        image = Image.open(path).convert("RGBA")
        if image.size == TARGET:
            return path_str, original_size, original_size, False
        resized = image.resize(TARGET, Image.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, "WEBP", quality=QUALITY, method=METHOD)
        data = buffer.getvalue()
        if len(data) >= original_size:
            return path_str, original_size, original_size, False
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
        return path_str, original_size, len(data), True
    except Exception:  # noqa: BLE001
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return path_str, size, size, False


def iter_frames():
    return sorted(str(p) for p in ASSETS.rglob("*.webp"))


def probe(sample: int = 8) -> None:
    files = iter_frames()
    picked = files[:: max(1, len(files) // sample)][:sample]
    before = after = 0
    for path_str in picked:
        path = Path(path_str)
        before += path.stat().st_size
        image = Image.open(path).convert("RGBA")
        resized = image.resize(TARGET, Image.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, "WEBP", quality=QUALITY, method=METHOD)
        after += min(len(buffer.getvalue()), path.stat().st_size)
    total = sum(Path(f).stat().st_size for f in files)
    print(f"抽样 {len(picked)} 帧")
    print(f"  {before/1024/len(picked):.1f} KB → {after/1024/len(picked):.1f} KB / 帧")
    print(f"  预计 {total/1024/1024:.0f} MB → {total*after/before/1024/1024:.0f} MB")


def update_manifests() -> None:
    """把 canvas / ground_pivot 改成新尺寸。"""
    for name in ("actions.json", "presets.json"):
        path = ASSETS / name
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("actions") or data.get("presets") or []
        for entry in entries:
            entry["canvas"] = list(TARGET)
            entry["ground_pivot"] = list(PIVOT)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: 更新 {len(entries)} 条 canvas/pivot")


def run(workers: int | None = None) -> None:
    files = iter_frames()
    workers = workers or min(8, (os.cpu_count() or 4))
    started = time.time()
    done = changed = 0
    before_total = after_total = 0
    print(f"{len(files)} 帧，{workers} 进程", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(shrink_one, f) for f in files]
        for future in as_completed(futures):
            _path, before, after, ok = future.result()
            before_total += before
            after_total += after
            done += 1
            changed += 1 if ok else 0
            if done % 2000 == 0:
                rate = done / max(time.time() - started, 1)
                print(f"  {done}/{len(files)} | 已改 {changed} | {rate:.0f} 帧/秒"
                      f" | 剩余 {(len(files)-done)/max(rate,0.1)/60:.1f} 分钟", flush=True)
    print(f"\n完成 {done} 帧，改写 {changed} 帧")
    print(f"体积 {before_total/1024/1024:.0f} MB → {after_total/1024/1024:.0f} MB"
          f"（-{(1-after_total/max(1,before_total))*100:.0f}%）")
    update_manifests()


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "probe":
        probe(); return 0
    if argv[1] == "run":
        workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else None
        run(workers); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
