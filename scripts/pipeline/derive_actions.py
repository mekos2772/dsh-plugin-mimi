"""从已有动作合成新动作：不调用视频模型，也不伪造身份。

能做的只有「重排已有帧」，所以只产出这几类可信的变体：
* reverse —— 反向播放（放下 → 拿起、松开 → 收拢）
* slow     —— 加倍时长（同一段动作放慢，适合"慢慢做"的语义）
* hold     —— 首帧停住 + 完整动作 + 尾帧停住（做之前先看一眼）
* pingpong —— 正放后倒放（摇摆、来回看）

**不做**的事情必须写清楚：不生成新的画姿、不改服装、不做变形插值。
需要全新姿势的动作仍要等视频服务恢复。

用法：
    python derive_actions.py list
    python derive_actions.py build
"""
from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "pet/assets/characters/mimi/expansion_v1"
FRAME_MS = 83

#: 源动作 → 派生变体。只挑语义上成立的重排：
#: 反向播放必须读起来仍像一个动作，否则不加。
DERIVATIONS: dict[str, dict] = {
    "tidy_desk_reverse": {
        "source": "tidy_desk", "mode": "reverse", "loop": False,
        "name_zh": "摆好小物", "desc": "整理动作的反向播放：从收好到摆出来。",
    },
    "wave_slow": {
        "source": "wave_hello", "mode": "slow", "loop": False,
        "name_zh": "慢慢挥手", "desc": "打招呼放慢一倍，用于安静场景。",
    },
    "nod_slow": {
        "source": "nod_yes", "mode": "slow", "loop": False,
        "name_zh": "慢慢点头", "desc": "点头放慢，用于认真回应。",
    },
    "listen_pingpong": {
        "source": "listen_tilt", "mode": "pingpong", "loop": True,
        "name_zh": "来回倾听", "desc": "侧头听的正反交替循环。",
    },
    "happy_sway_pingpong": {
        "source": "happy_sway", "mode": "pingpong", "loop": True,
        "name_zh": "来回摇摆", "desc": "开心摇摆的正反交替循环。",
    },
    "stretch_hold": {
        "source": "stretch", "mode": "hold", "loop": False,
        "name_zh": "伸懒腰（预备）", "desc": "先停一拍再伸展，收尾也留一拍。",
    },
    "drink_water_hold": {
        "source": "drink_water", "mode": "hold", "loop": False,
        "name_zh": "喝水（预备）", "desc": "先看一眼杯子再喝。",
    },
}

DERIVATIONS.update({
    "salute_hold": {
        "source": "salute", "mode": "hold", "loop": False,
        "name_zh": "敬礼（预备）", "desc": "先站定再敬礼，收尾留一拍。",
    },
    "shrug_pingpong": {
        "source": "shrug", "mode": "pingpong", "loop": True,
        "name_zh": "来回耸肩", "desc": "耸肩的正反交替，适合反复表示无奈。",
    },
    "look_up_slow": {
        "source": "look_up", "mode": "slow", "loop": False,
        "name_zh": "慢慢抬头", "desc": "抬头放慢，用于注意到某事。",
    },
    "cover_mouth_slow": {
        "source": "cover_mouth", "mode": "slow", "loop": False,
        "name_zh": "慢慢惊讶", "desc": "捂嘴惊讶放慢，观感更柔和。",
    },
    "pat_chest_hold": {
        "source": "pat_own_chest", "mode": "hold", "loop": False,
        "name_zh": "安抚（预备）", "desc": "先停一拍再拍拍胸口。",
    },
    "tidy_hair_pingpong": {
        "source": "tidy_hair", "mode": "pingpong", "loop": True,
        "name_zh": "反复撩发", "desc": "撩头发的正反交替循环。",
    },
    "spring_reverse": {
        "source": "cheer_jump", "mode": "reverse", "loop": False,
        "name_zh": "落下收势", "desc": "跳跃的反向播放：从空中回到站姿。",
    },
    "sit_stretch_hold": {
        "source": "sit_stretch", "mode": "hold", "loop": False,
        "name_zh": "坐着伸展（预备）", "desc": "坐好再伸展。",
    },
})
DERIVATIONS.update({
    "peek_left_reverse": {
        "source": "peek_left", "mode": "reverse", "loop": False,
        "name_zh": "缩回去", "desc": "探头的反向播放：收回画面内。",
    },
    "peek_right_reverse": {
        "source": "peek_right", "mode": "reverse", "loop": False,
        "name_zh": "缩回去（右）", "desc": "右侧探头的反向播放。",
    },
    "spin_cheer_slow": {
        "source": "spin_cheer", "mode": "slow", "loop": False,
        "name_zh": "慢慢转圈", "desc": "转圈放慢，用于安静的庆祝。",
    },
    "wave_both_slow": {
        "source": "wave_both", "mode": "slow", "loop": False,
        "name_zh": "慢慢挥双手", "desc": "双手挥动放慢。",
    },
    "hug_self_pingpong": {
        "source": "hug_self", "mode": "pingpong", "loop": True,
        "name_zh": "来回抱住自己", "desc": "抱自己的正反交替，像在轻轻发抖。",
    },
    "sleepy_nod_pingpong": {
        "source": "sleepy_nod", "mode": "pingpong", "loop": True,
        "name_zh": "反复打瞌睡", "desc": "打瞌睡的正反交替，适合久坐提醒。",
    },
    "drink_tea_hold": {
        "source": "drink_tea", "mode": "hold", "loop": False,
        "name_zh": "喝茶（预备）", "desc": "先端起杯子停一拍。",
    },
    "headphone_hold": {
        "source": "headphone", "mode": "hold", "loop": False,
        "name_zh": "戴耳机（预备）", "desc": "先看着耳机再戴上。",
    },
    "look_down_hold": {
        "source": "look_down_sad", "mode": "hold", "loop": False,
        "name_zh": "低头（预备）", "desc": "先停一拍再低头。",
    },
})

def _source_files(source_dir: Path) -> list[Path]:
    return sorted(source_dir.glob("*.webp"))


def _order_indices(count: int, mode: str) -> list[int]:
    """返回要引用的源帧下标序列；派生只重排，不重编码。"""
    if count <= 0:
        return []
    if mode == "reverse":
        return list(range(count - 1, -1, -1))
    if mode == "slow":
        out: list[int] = []
        for i in range(count):
            out.extend([i, i])
        return out
    if mode == "hold":
        # 只在动作**前面**留两帧预备（"先站定再动"），尾帧不留定格：
        # 用户要求"播完就切"，尾巴上的重复帧会被读成卡住。
        return [0] * 2 + list(range(count))
    if mode == "pingpong":
        return list(range(count)) + list(range(count - 2, 0, -1))
    return list(range(count))


def build_one(action_id: str, spec: dict) -> int:
    """用源动作的**原始文件**拼出新动作。

    关键：不解码、不重编码。帧内容完全一样，重编码只会更慢、更糊。
    """
    import json
    import shutil
    import tempfile

    source_dir = ASSETS / "actions" / spec["source"] / "frames"
    if not source_dir.is_dir():
        print(f"[skip] {action_id}: 源动作缺失 {spec['source']}")
        return 0
    sources = _source_files(source_dir)
    if not sources:
        print(f"[skip] {action_id}: 源目录为空")
        return 0

    order = _order_indices(len(sources), spec["mode"])
    # 去重：同一源帧只落一份文件，manifest 重复引用它
    used: dict[int, str] = {}
    names: list[str] = []
    action_dir = ASSETS / "actions" / action_id
    action_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{action_id}-", dir=str(ASSETS / "actions")))
    try:
        frames_dir = staging / "frames"
        frames_dir.mkdir()
        for index in order:
            if index not in used:
                name = f"{len(used) + 1:04d}.webp"
                shutil.copyfile(sources[index], frames_dir / name)
                used[index] = name
            names.append(used[index])
        (staging / "frame_order.json").write_text(
            json.dumps(names, ensure_ascii=False), encoding="utf-8"
        )
        old_frames = action_dir / "frames"
        if old_frames.exists():
            shutil.rmtree(old_frames)
        shutil.move(str(frames_dir), str(old_frames))
        shutil.move(str(staging / "frame_order.json"), str(action_dir / "frame_order.json"))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"[ok] {action_id}: {len(names)} 帧（{len(used)} 个唯一文件, {spec['mode']}）")
    return len(names)


def write_manifest() -> None:
    """把派生动作并入 actions.json；已有的 ID 不覆盖。"""
    manifest_path = ASSETS / "actions.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    # 派生条目每次都重写：去重后帧列表会变短，跳过就会留下失效的旧路径。
    data["actions"] = [
        entry for entry in data["actions"]
        if not (entry["id"] in DERIVATIONS and str(entry.get("source","")).startswith("derived"))
    ]
    existing = {entry["id"] for entry in data["actions"]}
    added = 0
    for action_id, spec in DERIVATIONS.items():
        if action_id in existing:
            continue
        action_dir = ASSETS / "actions" / action_id
        order_file = action_dir / "frame_order.json"
        if order_file.is_file():
            order = json.loads(order_file.read_text(encoding="utf-8"))
            frames = [action_dir / "frames" / name for name in order]
        else:
            frames = sorted((action_dir / "frames").glob("*.webp"))
        if len(frames) < 8:
            continue
        data["actions"].append({
            "id": action_id,
            "name_zh": spec["name_zh"],
            "description": spec["desc"],
            "status": "usable",
            "enabled": True,
            "kind": "full_frame",
            "canvas": [512, 768],
            "ground_pivot": [256, 744],
            "frames": [f"../expansion_v1/actions/{action_id}/frames/{p.name}" for p in frames],
            "duration_ms": [FRAME_MS] * len(frames),
            "loop": spec["loop"],
            "trigger": "interaction",
            "exit_state": "idle",
            "source": "derived/from_existing_frames",
            "registration_note": "frame reordering only; no new artwork",
            "runtime_codec": "webp_q95_alpha",
        })
        added += 1
    manifest_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"manifest: +{added} 条，共 {len(data['actions'])}")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "list":
        for action_id, spec in DERIVATIONS.items():
            print(f"{action_id:24} {spec['mode']:9} ← {spec['source']}")
        return 0
    if argv[1] == "build":
        only = None
        if "--only" in argv:
            only = set(argv[argv.index("--only") + 1].split(","))
        total = 0
        for action_id, spec in DERIVATIONS.items():
            if only is not None and action_id not in only:
                continue
            total += build_one(action_id, spec)
        print(f"合计 {total} 帧")
        write_manifest()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
