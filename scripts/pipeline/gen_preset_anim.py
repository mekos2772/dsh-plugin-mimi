"""把预设造型图变成待机循环动画帧。

用法：
    python gen_preset_anim.py one knit_home
    python gen_preset_anim.py queue
    python gen_preset_anim.py status
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mimi_gen import (  # noqa: E402
    IMG_DIR, OUT_DIR, VID_DIR, build_action_from_video, compose_first_frame,
    download_video, submit_video, wait_video,
)
from gen_outfit import PRESETS  # noqa: E402

IDLE_PROMPT = (
    "The same chibi anime girl stands still facing the camera, idle breathing only. "
    "Very subtle up-and-down motion of chest and shoulders, one gentle blink, hair sways "
    "slightly. Feet stay planted on the ground, no walking, no camera movement, no zoom, "
    "no pan. Keep her face, hairstyle, eye color, this exact outfit, body proportions and "
    "art style identical to the first frame. Flat 2D illustration, clean line art, "
    "solid chroma green background."
)


def out_dir(pid: str) -> Path:
    return OUT_DIR / "presets" / pid


def done(pid: str) -> bool:
    d = out_dir(pid)
    return d.is_dir() and len(list(d.glob("*.webp"))) >= 10


def run_one(pid: str, *, force: bool = False) -> list[Path]:
    if done(pid) and not force:
        print(f"[skip] {pid}", flush=True)
        return sorted(out_dir(pid).glob("*.webp"))

    src = IMG_DIR / f"preset_{pid}.png"
    if not src.is_file():
        raise FileNotFoundError(src)

    first = IMG_DIR / f"first_preset_{pid}.png"
    if not first.is_file():
        compose_first_frame(src, first)

    mp4 = VID_DIR / f"preset_{pid}.mp4"
    if not mp4.is_file():
        jid = submit_video(first, IDLE_PROMPT, seed=abs(hash(pid)) % 100000)
        print(f"[submit] {pid} job={jid}", flush=True)
        state = wait_video(jid)
        print(f"[done] {pid} {state.get('gen_seconds')}s", flush=True)
        download_video(jid, mp4)
    else:
        print(f"[cache] {pid}", flush=True)

    frames = build_action_from_video(mp4, out_dir(pid), step=2, loop=True)
    print(f"[frames] {pid} -> {len(frames)} webp", flush=True)
    return frames


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "status":
        for k in PRESETS:
            print(f"{k:16} {'OK' if done(k) else '--'}")
        return 0
    cmd = argv[1]
    if cmd == "one":
        run_one(argv[2], force="--force" in argv)
        return 0
    if cmd == "queue":
        for k in PRESETS:
            if not (IMG_DIR / f"preset_{k}.png").is_file():
                print(f"[wait] {k} image not ready", flush=True)
                continue
            try:
                run_one(k)
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] {k}: {exc}", flush=True)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
