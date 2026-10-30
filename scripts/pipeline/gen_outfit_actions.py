"""为每套预设造型生成配套动作（视频模型 → 帧）。

用法：
    python gen_outfit_actions.py list
    python gen_outfit_actions.py status
    python gen_outfit_actions.py queue [--tier a|b]
    python gen_outfit_actions.py one <outfit_id> <action_id>

输出：/tmp/mimi_work/out/outfit_actions/<outfit>/<action>/frames/*.webp
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mimi_gen import (  # noqa: E402
    IMG_DIR, OUT_DIR, VID_DIR, build_action_from_video, compose_first_frame,
    download_video, submit_video, wait_video,
)
from gen_outfit import PRESETS  # noqa: E402

NO_CAMERA = (
    "Fixed camera, no zoom, no pan, no rotation, no cuts. The character keeps the same "
    "size and position in frame, feet stay on the ground. Solid chroma green background, "
    "flat 2D anime illustration, clean cel shading. Keep the face, hairstyle, eye color, "
    "body proportions and this exact outfit identical to the first frame."
)

# tier a = 日常必用；tier b = 有余力再补
OUTFIT_ACTIONS: dict[str, dict] = {
    "walk_right": {
        "tier": "a", "name_zh": "向右走", "loop": True,
        "prompt": "She walks in place toward the right side of the frame, side view, "
                  "natural small steps, arms swinging gently, skirt swaying a little. "
                  "A clean looping walk cycle: the last pose flows back into the first.",
    },
    "head_pat": {
        "tier": "a", "name_zh": "摸头", "loop": False,
        "prompt": "An invisible hand pats the top of her head; she closes her eyes happily "
                  "and presses her head up slightly into the pat, then relaxes.",
    },
    "cheek_poke": {
        "tier": "a", "name_zh": "戳脸", "loop": False,
        "prompt": "Something pokes her cheek; she blinks in surprise, tilts her head away a "
                  "little, then puffs her cheek and looks at the camera.",
    },
    "celebrate": {
        "tier": "a", "name_zh": "开心庆祝", "loop": False,
        "prompt": "She celebrates happily: both arms raised, small hop, bright smile, then "
                  "settles back to standing.",
    },
    "land_recover": {
        "tier": "a", "name_zh": "落地恢复", "loop": False,
        "prompt": "She lands from a short drop: knees bend to absorb the landing, body dips "
                  "slightly, then she straightens up and returns to a normal standing pose.",
    },
    "wave": {
        "tier": "b", "name_zh": "挥手", "loop": False,
        "prompt": "She raises one hand and waves hello three times with a warm smile.",
    },
    "nod": {
        "tier": "b", "name_zh": "点头", "loop": False,
        "prompt": "She nods her head twice in clear, gentle agreement, keeping eye contact.",
    },
    "listen": {
        "tier": "b", "name_zh": "倾听", "loop": True,
        "prompt": "She stands attentively, tilting her head slightly to one side and blinking, "
                  "as if listening closely. Subtle looping idle-listening motion.",
    },
    "sit_idle": {
        "tier": "b", "name_zh": "坐姿待机", "loop": True,
        "prompt": "She is sitting on the ground with her legs folded to one side, hands resting "
                  "in her lap, breathing gently. Subtle looping seated idle.",
    },
}


OUTFIT_ACTIONS.update({
    "yawn": {
        "tier": "c", "name_zh": "打哈欠", "loop": False,
        "prompt": "She covers her mouth and yawns widely, eyes squeezing shut, then blinks "
                  "sleepily and rubs one eye.",
    },
    "shy": {
        "tier": "c", "name_zh": "害羞", "loop": False,
        "prompt": "She looks down shyly with pink cheeks and clasps her hands together in "
                  "front of her chest.",
    },
    "clap": {
        "tier": "c", "name_zh": "鼓掌", "loop": False,
        "prompt": "She claps her hands together three times happily with a bright smile.",
    },
    "look_up": {
        "tier": "c", "name_zh": "抬头看", "loop": False,
        "prompt": "She tilts her head up and looks toward the upper part of the frame with "
                  "curious eyes, blinks once.",
    },
    "tilt_head": {
        "tier": "c", "name_zh": "歪头", "loop": False,
        "prompt": "She tilts her head to one side with a questioning look, holds it, then "
                  "returns upright.",
    },
})

def out_dir(outfit_id: str, action_id: str) -> Path:
    return OUT_DIR / "outfit_actions" / outfit_id / action_id


def done(outfit_id: str, action_id: str) -> bool:
    d = out_dir(outfit_id, action_id)
    return d.is_dir() and len(list(d.glob("*.webp"))) >= 10


def run_one(outfit_id: str, action_id: str, *, force: bool = False) -> list[Path]:
    spec = OUTFIT_ACTIONS[action_id]
    if done(outfit_id, action_id) and not force:
        print(f"[skip] {outfit_id}/{action_id}", flush=True)
        return sorted(out_dir(outfit_id, action_id).glob("*.webp"))

    src = IMG_DIR / f"preset_{outfit_id}.png"
    if not src.is_file():
        raise FileNotFoundError(src)

    first = IMG_DIR / f"first_oa_{outfit_id}_{action_id}.png"
    if not first.is_file():
        compose_first_frame(src, first)

    mp4 = VID_DIR / f"oa_{outfit_id}_{action_id}.mp4"
    if not mp4.is_file():
        prompt = f"{spec['prompt']} {NO_CAMERA}"
        seed = (abs(hash(outfit_id)) + abs(hash(action_id))) % 100000
        jid = submit_video(first, prompt, seed=seed)
        print(f"[submit] {outfit_id}/{action_id} job={jid}", flush=True)
        state = wait_video(jid)
        print(f"[done] {outfit_id}/{action_id} {state.get('gen_seconds')}s", flush=True)
        download_video(jid, mp4)
    else:
        print(f"[cache] {outfit_id}/{action_id}", flush=True)

    frames = build_action_from_video(
        mp4, out_dir(outfit_id, action_id), step=2, loop=spec["loop"]
    )
    print(f"[frames] {outfit_id}/{action_id} -> {len(frames)} webp", flush=True)
    return frames


def jobs(tier: str | None = None) -> list[tuple[str, str]]:
    """按造型 × 动作展开；A 级先跑，保证先有能用的日常套装。"""
    wanted = [a for a, s in OUTFIT_ACTIONS.items() if tier is None or s["tier"] == tier]
    order = ["a", "b", "c"] if tier is None else [tier]
    out: list[tuple[str, str]] = []
    for t in order:
        for outfit_id in PRESETS:
            if not (IMG_DIR / f"preset_{outfit_id}.png").is_file():
                continue
            for action_id in wanted:
                if OUTFIT_ACTIONS[action_id]["tier"] != t:
                    continue
                out.append((outfit_id, action_id))
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "list":
        for k, v in OUTFIT_ACTIONS.items():
            print(f"{k:14} tier {v['tier']}  {v['name_zh']}")
        return 0
    cmd = argv[1]
    if cmd == "status":
        for outfit_id in PRESETS:
            done_ids = [a for a in OUTFIT_ACTIONS if done(outfit_id, a)]
            print(f"{outfit_id:16} {len(done_ids)}/{len(OUTFIT_ACTIONS)}: {' '.join(done_ids)}")
        return 0
    if cmd == "one":
        run_one(argv[2], argv[3], force="--force" in argv)
        return 0
    if cmd == "queue":
        tier = None
        if "--tier" in argv:
            tier = argv[argv.index("--tier") + 1]
        total = jobs(tier)
        print(f"[plan] {len(total)} videos", flush=True)
        for i, (outfit_id, action_id) in enumerate(total, 1):
            try:
                print(f"[{i}/{len(total)}]", end=" ", flush=True)
                run_one(outfit_id, action_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] {outfit_id}/{action_id}: {exc}", flush=True)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


# 追加：拖拽姿势（每套造型三档受力），B 级跑完后单独用
DRAG_TIERS = {
    "t00": "She hangs gently from the top of her head, arms relaxed at her sides, "
           "body hanging straight down with a slight sway.",
    "t05": "She is lifted quickly: arms swing back, hair and skirt trail upward, "
           "body tilted slightly, legs dangling with momentum.",
    "t10": "She is dragged at high speed: hair and skirt streaming strongly backward, "
           "arms trailing behind, body leaning into the motion, legs swept back.",
}


def run_drag(outfit_id: str, tier: str, *, force: bool = False) -> list[Path]:
    spec = DRAG_TIERS[tier]
    target = OUT_DIR / "outfit_drag" / outfit_id / tier
    if target.is_dir() and len(list(target.glob("*.webp"))) >= 6 and not force:
        print(f"[skip] drag {outfit_id}/{tier}", flush=True)
        return sorted(target.glob("*.webp"))
    src = IMG_DIR / f"preset_{outfit_id}.png"
    first = IMG_DIR / f"first_drag_{outfit_id}_{tier}.png"
    if not first.is_file():
        compose_first_frame(src, first, char_h=560)
    mp4 = VID_DIR / f"drag_{outfit_id}_{tier}.mp4"
    if not mp4.is_file():
        jid = submit_video(first, f"{spec} {NO_CAMERA}", seed=abs(hash(outfit_id + tier)) % 100000)
        print(f"[submit] drag {outfit_id}/{tier} job={jid}", flush=True)
        wait_video(jid)
        download_video(jid, mp4)
    frames = build_action_from_video(mp4, target, step=4, loop=False)
    print(f"[frames] drag {outfit_id}/{tier} -> {len(frames)}", flush=True)
    return frames
