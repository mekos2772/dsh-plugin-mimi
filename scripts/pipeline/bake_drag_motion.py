"""把只有单帧的拖拽档位离线补成真正的动画序列。

背景：视频生成服务暂时不可用，但 `rain_coat` / `pajama` / `summer_dress` /
`sporty` 的每个拖拽档位只有 **1 张静图**（`maid_blue` 连档位都没有）。结果是
拖起来换一张图就没了，观感上"这个造型没有拖拽动作"。

这里不重画角色，只对已有姿势做**刚体动画**：绕头顶吊点摆动 + 轻微上下浮动 +
按档位放大幅度。因为角色是被"拎住头发"带走的，绕头顶旋转在物理上是对的，
也不会拉伸像素。

输出仍然是 256×384 的 webp 序列，manifest 里的帧数与时长同步更新，
所有下游（渲染、测试、压缩）都不需要知道这些帧是程序生成的。

用法：
    python bake_drag_motion.py probe
    python bake_drag_motion.py run [--frames 24] [--dry-run]
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "pet" / "assets" / "characters" / "mimi" / "expansion_v1"
MANIFEST = ASSETS / "presets.json"

#: 每档生成多少帧。12fps × 1 秒 ≈ 一个来回。
#: 帧数直接决定包体积（每帧约 15 KB），12 帧已足够看出摆动。
DEFAULT_FRAMES = 6
#: 帧时长（与素材其它动作一致，12fps）。
FRAME_MS = 83
#: 吊点（绕这里旋转）：头顶偏上一点。
ANCHOR_X_RATIO = 0.5
ANCHOR_Y_RATIO = 0.40
#: 各档位的摆动幅度（度）。t00 被静静拎着，t10 高速甩动。
TIER_SWING_DEG = {
    "t00": 2.2,
    "t01": 2.6,
    "t02": 3.0,
    "t03": 3.4,
    "t04": 3.8,
    "t05": 4.6,
    "t06": 5.2,
    "t07": 5.8,
    "t08": 6.4,
    "t09": 7.0,
    "t10": 7.8,
}
#: 上下浮动幅度（像素），随档位放大。
TIER_BOB_PX = 2.0


def _tier_amplitude(tier: str) -> tuple[float, float]:
    swing = TIER_SWING_DEG.get(tier, 4.0)
    index = int(tier[1:]) if tier[1:].isdigit() else 0
    bob = TIER_BOB_PX * (0.7 + index * 0.08)
    return swing, bob


def bake_tier(source: Image.Image, tier: str, frames: int) -> list[Image.Image]:
    """绕吊点摆动，生成 ``frames`` 张。首尾同相位，循环无缝。"""
    width, height = source.size
    anchor = (width * ANCHOR_X_RATIO, height * ANCHOR_Y_RATIO)
    swing, bob = _tier_amplitude(tier)
    out: list[Image.Image] = []
    for index in range(frames):
        phase = 2.0 * math.pi * index / frames
        angle = swing * math.sin(phase)
        dy = bob * math.cos(phase * 2.0)
        # 绕 anchor 旋转 + 轻微上下浮动；bicubic 减少锯齿。
        rotated = source.rotate(
            angle,
            resample=Image.BICUBIC,
            center=anchor,
            translate=(0, dy),
        )
        out.append(rotated)
    return out


def probe() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(f"{'造型':13s}{'档位':>4s}  每档帧数")
    for preset in data["presets"]:
        tiers = preset.get("drag") or {}
        counts = {t: len(v.get("frames", [])) for t, v in sorted(tiers.items())}
        static = [t for t, c in counts.items() if c <= 1]
        if not counts:
            print(f"{preset['id']:13s}{0:>4d}  没有拖拽档位")
        elif static:
            print(f"{preset['id']:13s}{len(counts):>4d}  需补帧: {static}")


def run(frames: int = DEFAULT_FRAMES, dry_run: bool = False) -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    total = 0
    for preset in data["presets"]:
        preset_id = preset["id"]
        tiers = preset.get("drag") or {}
        if not tiers:
            print(f"[skip] {preset_id}: 没有拖拽档位（渲染层用待机帧兜底）", flush=True)
            continue
        static = [t for t, v in tiers.items() if len(v.get("frames", [])) <= 1]
        if not static:
            continue
        for tier in sorted(static):
            entry = tiers[tier]
            src_rel = entry["frames"][0]
            src_path = ASSETS / src_rel
            target_dir = src_path.parent
            if dry_run:
                print(f"[dry] {preset_id}/{tier}: {src_path.name} × {frames}", flush=True)
                continue
            source = Image.open(src_path).convert("RGBA")
            baked = bake_tier(source, tier, frames)
            names: list[str] = []
            for index, image in enumerate(baked, start=1):
                name = f"{index:04d}.webp"
                image.save(target_dir / name, "WEBP", quality=88, method=6)
                names.append(name)
            # manifest：帧列表 + 逐帧时长
            base_dir = src_path.parent.relative_to(ASSETS).as_posix()
            entry["frames"] = [f"{base_dir}/{name}" for name in names]
            entry["duration_ms"] = [FRAME_MS] * len(names)
            total += len(names)
            print(f"[baked] {preset_id}/{tier}: {len(names)} 帧", flush=True)
    if dry_run:
        return
    MANIFEST.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n完成：新写入 {total} 帧，manifest 已更新")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "probe":
        probe()
        return 0
    if argv[1] == "run":
        frames = int(argv[argv.index("--frames") + 1]) if "--frames" in argv else DEFAULT_FRAMES
        run(frames, dry_run="--dry-run" in argv)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
