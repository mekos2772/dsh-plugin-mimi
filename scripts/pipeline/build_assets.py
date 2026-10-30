"""把产线产物整理成运行时资产树 + manifest。

输出：
  pet/assets/characters/mimi/expansion_v1/
      presets.json                 预设造型目录
      presets/<id>/frames/*.webp   造型待机循环
      actions.json                 新动作目录
      actions/<id>/frames/*.webp   新动作帧

只复制已通过校验的产物；缺失的条目不会写进 manifest。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path("/Volumes/isci肥牛/zhuoc/dsh-plugin-mimi")
WORK = Path("/tmp/mimi_work")
OUT = WORK / "out"
DEST = REPO / "pet/assets/characters/mimi/expansion_v1"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_action import ACTIONS  # noqa: E402
from gen_outfit import PRESETS  # noqa: E402
from gen_outfit_actions import OUTFIT_ACTIONS  # noqa: E402

OUTFIT_ACTION_IDS = tuple(OUTFIT_ACTIONS)

CANVAS = (512, 768)
PIVOT = (256, 744)
TARGET_H = 425
FRAME_MS = 83


def sprite_ok(p: Path) -> bool:
    try:
        im = Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001
        return False
    a = np.array(im)
    if a.shape[:2] != (CANVAS[1], CANVAS[0]):
        return False
    ys, xs = np.where(a[..., 3] > 8)
    if len(xs) < 500:
        return False
    h = ys.max() - ys.min() + 1
    return 260 <= h <= 520


def copy_frames(src_dir: Path, dst_dir: Path) -> list[str]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for old in dst_dir.glob("*.webp"):
        old.unlink()
    names = []
    for i, p in enumerate(sorted(src_dir.glob("*.webp")), 1):
        if not sprite_ok(p):
            continue
        target = dst_dir / f"{i:04d}.webp"
        shutil.copy2(p, target)
        rel = dst_dir.relative_to(DEST).as_posix()
        names.append(f"../expansion_v1/{rel}/{target.name}")
    return names


def _mirror_into(src_dir: Path, dst_dir: Path) -> list[str]:
    """水平镜像一组帧；返回相对 library_v1 的路径。"""
    if not src_dir.is_dir():
        return []
    dst_dir.mkdir(parents=True, exist_ok=True)
    for old in dst_dir.glob("*.webp"):
        old.unlink()
    names: list[str] = []
    for i, p in enumerate(sorted(src_dir.glob("*.webp")), 1):
        im = Image.open(p).convert("RGBA").transpose(Image.FLIP_LEFT_RIGHT)
        target = dst_dir / f"{i:04d}.webp"
        im.save(target, "WEBP", quality=95, method=4)
        rel = dst_dir.relative_to(DEST).as_posix()
        names.append(f"../expansion_v1/{rel}/{target.name}")
    return names


def build_actions() -> dict:
    entries = []
    for aid, spec in ACTIONS.items():
        src = OUT / "actions" / aid
        if not src.is_dir() or not list(src.glob("*.webp")):
            continue
        frames = copy_frames(src, DEST / "actions" / aid / "frames")
        if len(frames) < 10:
            print(f"[skip] {aid}: only {len(frames)} valid frames")
            continue
        entries.append({
            "id": aid,
            "name_zh": spec["name_zh"],
            "description": spec["desc"],
            "status": "usable",
            "enabled": True,
            "kind": "full_frame",
            "canvas": list(CANVAS),
            "ground_pivot": list(PIVOT),
            "frames": frames,
            "duration_ms": [FRAME_MS] * len(frames),
            "loop": spec["loop"],
            "trigger": "interaction",
            "exit_state": "idle",
            "source": "generated/expansion_v1/video_model",
            "registration_note": "uniform scale from median bbox; ground median -> pivot",
            "runtime_codec": "webp_q95_alpha",
        })
    return {"schema_version": 1, "actions": entries}


def build_presets() -> dict:
    entries = []
    for pid, spec in PRESETS.items():
        src = OUT / "presets" / pid
        if not src.is_dir() or not list(src.glob("*.webp")):
            continue
        frames = copy_frames(src, DEST / "presets" / pid / "frames")
        if len(frames) < 10:
            print(f"[skip] preset {pid}: only {len(frames)} valid frames")
            continue
        # 每套造型自己的动作集；缺的动作不写进 map，运行时不会借用默认服装的帧。
        action_map = {}
        for action_id in OUTFIT_ACTION_IDS:
            a_src = OUT / "outfit_actions" / pid / action_id
            if not a_src.is_dir() or not list(a_src.glob("*.webp")):
                continue
            a_dir = DEST / "presets" / pid / "actions" / action_id / "frames"
            a_frames = copy_frames(a_src, a_dir)
            if len(a_frames) < 10:
                continue
            action_map[action_id] = {
                "frames": a_frames,
                "duration_ms": [FRAME_MS] * len(a_frames),
            }
        # 向左走：水平镜像向右走，不占视频额度。角色无单边不对称要素时成立。
        if "walk_right" in action_map and "walk_left" not in action_map:
            mirrored = _mirror_into(
                DEST / "presets" / pid / "actions" / "walk_right" / "frames",
                DEST / "presets" / pid / "actions" / "walk_left" / "frames",
            )
            if mirrored:
                action_map["walk_left"] = {
                    "frames": mirrored,
                    "duration_ms": [FRAME_MS] * len(mirrored),
                }

        # 拖拽姿势：每套造型三档受力图；缺档就整体不进 map。
        drag_map = {}
        for tier in ("t00", "t05", "t10"):
            d_src = OUT / "outfit_drag" / pid / tier
            if not d_src.is_dir() or not list(d_src.glob("*.webp")):
                continue
            d_dir = DEST / "presets" / pid / "drag" / tier / "frames"
            d_frames = copy_frames(d_src, d_dir)
            if not d_frames:
                continue
            drag_map[tier] = {
                "frames": d_frames,
                "duration_ms": [FRAME_MS] * len(d_frames),
            }

        # 必要动作齐全（走路 + 至少两种互动反馈）才升级为日常套装。
        required = {"walk_right", "head_pat", "cheek_poke", "celebrate"}
        daily = required.issubset(action_map)
        entries.append({
            "id": pid,
            "name_zh": spec["name_zh"],
            "description": spec["desc"],
            "mode": "daily" if daily else "display",
            "status": "usable",
            "enabled": True,
            "canvas": list(CANVAS),
            "ground_pivot": list(PIVOT),
            "frames": frames,
            "duration_ms": [FRAME_MS] * len(frames),
            "loop": True,
            "actions": action_map,
            "drag": drag_map,
            "source": "generated/expansion_v1/video_model",
            "runtime_codec": "webp_q95_alpha",
        })
    return {"schema_version": 1, "presets": entries}


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    actions = build_actions()
    presets = build_presets()
    (DEST / "actions.json").write_text(
        json.dumps(actions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DEST / "presets.json").write_text(
        json.dumps(presets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"actions: {len(actions['actions'])}")
    print(f"presets: {len(presets['presets'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
