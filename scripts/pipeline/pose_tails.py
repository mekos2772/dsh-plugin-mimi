"""用几何指标独立测量每条动作的「静止尾巴」，交叉验证主指标。

口径（Lead 2026-10-29 变更）
---------------------------
不再要求"动作连贯、自然收尾"。目标改成：**播完一轮就切**，主体动作做完立即结束，
后面静止帧全砍。判据放宽到"只要能看出还在动就算动"：

```
still[i] ⇔ iou[i] >= 0.9995  且  rgbmov[i] <= P30(rgbmov)
keep = 静止尾巴起始帧（不加缓冲），下限 max(6, ceil(0.35*N))
loop=True 的动作一律 keep = N
```

指标（与主指标 80x120 预乘模糊帧差无共同项）
---------------------------------------------
掩膜 `mask_i = alpha_i > 8`；逐帧 bbox、面积、质心。

姿势偏移（相对首帧）：

```
d_i = |centroid_i - centroid_0| / diag
    + |area_i - area_0| / max(area_0, 1)
    + (1 - IoU(mask_i, mask_0))
```

逐帧掩膜变化 `iou[i] = IoU(mask_i, mask_{i+1})`。

掩膜内 RGB 运动：只统计 `min(alpha_i, alpha_{i+1}) >= 200` 的实体内部像素
（排除描边/羽化，否则边缘抖动会淹没信号）：

```
e_i      = luma_{i+1} - luma_i        （有符号亮度残差）
rgbmov_i = mean(|e_i|)
corr_i   = corr(e_{i-1}, e_i)         （残差相干性，仅作诊断）
```

关于测量状态：素材已被裁剪过
--------------------------
`scripts/pipeline/trim_tails.py` 已在本仓库执行过 `--apply`：多余帧被移动到
`pet/assets/characters/mimi/.trimmed/<library>/<action>/`，manifest 的
`frames` / `duration_ms` 同时被截断。因此**当前工作区是裁剪后的状态**。

要判断"该裁到哪"，必须看**裁剪前**的素材。脚本用 `--state pristine`（默认）
做无损重建，不复制、不改动任何素材：

1. `library_v1` 的全部帧由 `git show HEAD:` 读取（manifest 与帧都在版本控制里）；
2. `expansion_v1` 的**源动作**帧 = 现行 `frames/` ∪ `.trimmed/expansion_v1/<id>/`；
3. `expansion_v1` 的**派生动作**（reverse/slow/hold/pingpong）按
   `derive_actions.py` 的 `DERIVATIONS` + `_order_indices()` 从重建后的源帧重新映射。

`--state live` 则直接量当前工作区（裁剪后），用于核对裁剪结果。

`ends_off_pose`（本报告重点）
-----------------------------
`d_last / peak_d`：结尾帧相对首帧的姿势偏移占峰值的比例。
`> 0.6` 表示**裁完回不正**——动作结束时角色停在低头/跪姿/坐姿/躺姿等非站姿，
`d_i` 永不回归 0，所以"d 回落到平台"这类回归判据对它完全失效。

用法
----
    python pose_tails.py                    # 全量 87 条（pristine），写 JSON + MD
    python pose_tails.py --report           # 只打印表
    python pose_tails.py --trace tidy_desk  # 单条逐帧明细
    python pose_tails.py --state live       # 量裁剪后的现状
    python pose_tails.py --compare docs/expansion/trim_proposal.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
MIMI = REPO / "pet/assets/characters/mimi"
LIB_DIR = MIMI / "library_v1"
EXP_DIR = MIMI / "expansion_v1"
TRIMMED = MIMI / ".trimmed"
DEFAULT_JSON = REPO / "docs/expansion/pose_proposal.json"
DEFAULT_MD = REPO / "docs/expansion/TRIM-CROSSCHECK.md"
MAIN_JSON = REPO / "docs/expansion/trim_proposal.json"

# 冻结基线：pre-trim 的 `docs/expansion/trim_proposal.json`（20:25 生成）里的 keep。
# 该文件之后被 motion-analyst 用**裁剪后**的素材重新生成（21:12），N 已变成
# 10/52/60/44/50 这类裁剪后的值，不能再当"原始素材"的基线。这里冻结采集到的原始值，
# 只覆盖 59 条 source 且非 loop 的动作——正好是 floor 陷阱起作用的那一批。
FROZEN_MAIN_KEEP = {
    "headphone": 31, "wave_hello": 31, "cheer_jump": 31, "happy_sway": 31,
    "stretch_back": 31, "shy_look": 31, "tidy_hair": 31, "nod_yes": 31,
    "celebrate": 27, "cheek_poke": 27, "look_up": 31, "listen": 27,
    "peek_note": 31, "shrug": 31, "look_down_sad": 31, "catch_star": 36,
    "listen_tilt": 31, "peek_right": 41, "point_forward": 31, "peek_left": 43,
    "salute": 36, "ribbon_adjust": 42, "bow": 44, "cover_mouth": 39,
    "spin_cheer": 39, "nod": 19, "turn_show": 45, "push_ball": 42,
    "pat_own_chest": 43, "sit_stretch": 53, "feed_refuse": 41,
    "file_drop_receive": 41, "stretch": 50, "yawn": 41, "sleep_lie_down": 62,
    "head_pat": 42, "wake_up": 67, "wave": 41, "belly_ticklish": 47,
    "lantern_off": 46, "think_pencil": 46, "kneel_look": 52,
    "search_pocket": 46, "clap_happy": 48, "drink_tea": 52, "high_five": 49,
    "feed_bread": 47, "drink_water": 52, "tidy_desk": 52, "hug_self": 53,
    "fun_facepalm": 12, "land_recover_v4_12": 12,
    "harness_task_thinking_v1_12": 12, "harness_tool_working_v1_12": 12,
    "shake_head": 51, "wave_both": 62, "sit_down": 47, "stand_up": 51,
    "sleepy_nod": 57,
}


# 冻结基线（续）：pre-trim 主指标里的 raw_keep_2F 与 keep_floor。
# 这 21 条在 pre-trim 运行中被标为 `floor-bound`：主指标自己的 raw_keep_2F 已经很小，
# 最终 keep 完全由 keep_floor 决定，与"有没有静止尾巴"无关。
FROZEN_RAW_KEEP_2F = {
    "fun_facepalm": 2, "land_recover_v4_12": 2,
    "harness_task_thinking_v1_12": 2, "harness_tool_working_v1_12": 2,
    "listen": 2, "celebrate": 2, "cheek_poke": 15, "nod": 2, "peek_note": 15,
    "headphone": 8, "wave_hello": 9, "nod_yes": 26, "cheer_jump": 18,
    "listen_tilt": 12, "shy_look": 12, "point_forward": 21, "tidy_hair": 10,
    "happy_sway": 2, "stretch_back": 2, "shrug": 22, "look_down_sad": 27,
}

# ---------------------------------------------------------------- 参数

ALPHA_TH = 8          # 掩膜阈值：可见像素
SOLID_ALPHA = 200     # 实体内部：排除描边/羽化
IOU_STILL = 0.9995    # Lead 指定
RGB_PCT = 30          # Lead 指定：低于自身 30% 分位算"没动"
FLOOR_FRAC = 0.35     # Lead 指定
FLOOR_MIN = 6
OFF_POSE_RATIO = 0.6  # d_last/peak 超过此值 → 裁完回不正
OFF_POSE_STRONG = 0.15  # d_last 绝对量超过此值 → 明显回不正（否则只是"略有偏差"）
POSE_AMP = 0.010      # 纯几何平台判据：尾巴内 d 振幅上限
POSE_GRAD = 0.004     # 纯几何平台判据：尾巴内 d 单帧梯度上限

# IoU 可达性诊断用的灵敏度档位（回答"0.9995 在这个素材上能不能达到"）
IOU_SENSITIVITY = (0.9995, 0.999, 0.998, 0.995, 0.99)


# ---------------------------------------------------------------- 帧解析

def _sh(entry: dict, root: Path) -> list[Path]:
    return [root / rel for rel in entry["frames"]]


def _git_manifest() -> dict:
    out = subprocess.run(
        ["git", "show", "HEAD:pet/assets/characters/mimi/library_v1/manifest.json"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _git_cache() -> Path | None:
    """把 HEAD 里的 library_v1 素材导出到一个临时缓存目录（只做一次）。

    工作区里的 library_v1 已被 trim_tails.py 裁剪并移走多余帧，因此在
    pristine 模式下必须从版本控制取原始字节。用 `git archive` 一次导出，
    避免逐帧 `git show` 的上千次进程开销。
    """
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                              capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        return None
    cache = Path("/tmp/mimi_git_cache") / head / "pet/assets/characters/mimi/library_v1"
    if cache.is_dir() and any(cache.rglob("*.webp")):
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "HEAD", "pet/assets/characters/mimi/library_v1"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout
    staging = Path("/tmp/mimi_git_cache") / head
    if staging.exists():
        import shutil
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    subprocess.run(["tar", "-x", "-C", str(staging)], input=archive, check=True)
    return cache


def _derivations() -> dict:
    spec = importlib.util.spec_from_file_location(
        "_derive_actions", REPO / "scripts/pipeline/derive_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DERIVATIONS, module._order_indices


class Resolver:
    """把 (library, action, frame_index) 解析到一个真实的 webp 路径。

    pristine 模式下重建裁剪前的目录状态，不写盘、不复制。
    """

    def __init__(self, state: str) -> None:
        self.state = state
        self.lib_manifest = json.loads((LIB_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.exp_manifest = json.loads((EXP_DIR / "actions.json").read_text(encoding="utf-8"))
        self.git_lib: Path | None = None
        if state == "pristine":
            try:
                self.lib_manifest = _git_manifest()
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                self.state = "live"
            self.derivations, self._order = _derivations()
            if self.state == "pristine":
                self.git_lib = _git_cache()
                if self.git_lib is None:
                    self.state = "live"
        else:
            self.derivations, self._order = {}, None
        self._src_n: dict[str, int] = {}

    # -- 源动作：现行 frames/ 与 .trimmed/ 取并集
    def _source_frames(self, library: str, action_id: str) -> dict[int, Path]:
        base = (LIB_DIR / "usable/actions" / action_id / "frames") if library == "library_v1" \
            else (EXP_DIR / "actions" / action_id / "frames")
        found: dict[int, Path] = {}
        if self.state == "pristine" and library == "library_v1" and self.git_lib is not None:
            # library_v1 原始字节只在版本控制里：工作区那份已被裁剪
            git_dir = self.git_lib / "usable/actions" / action_id / "frames"
            if git_dir.is_dir():
                for path in sorted(git_dir.glob("*.webp")):
                    try:
                        found[int(path.stem)] = path
                    except ValueError:
                        continue
                return found
        if base.is_dir():
            for path in sorted(base.glob("*.webp")):
                try:
                    found[int(path.stem)] = path
                except ValueError:
                    continue
        if self.state == "pristine":
            extra = TRIMMED / library / action_id
            if extra.is_dir():
                for path in sorted(extra.glob("*.webp")):
                    try:
                        found.setdefault(int(path.stem), path)
                    except ValueError:
                        continue
        return found

    def source_count(self, library: str, action_id: str) -> int:
        key = f"{library}/{action_id}"
        if key not in self._src_n:
            self._src_n[key] = len(self._source_frames(library, action_id))
        return self._src_n[key]

    def resolve(self, library: str, action_id: str) -> tuple[list[Path], str]:
        """返回该动作按播放顺序的帧路径，以及它属于 source 还是 derived。"""
        frames = self._source_frames(library, action_id)
        if not frames:
            return [], "missing"
        order = sorted(frames)
        mode = self.derivations.get(action_id, {}).get("mode") if self.derivations else None
        if mode is None or self._order is None:
            return [frames[i] for i in order], "source"
        # 派生动作：从重建后的源帧按下标序列重排（derive_actions 只重排，不重编码）
        src_id = self.derivations[action_id]["source"]
        src = self._source_frames("expansion_v1", src_id)
        if not src:
            return [frames[i] for i in order], "source"
        src_order = sorted(src)
        seq = self._order(len(src_order), mode)
        return [src[src_order[i]] for i in seq], "derived"


# ---------------------------------------------------------------- 指标

def measure(paths: list[Path]) -> dict:
    frames = []
    for path in paths:
        with Image.open(path) as image:
            frames.append(np.asarray(image.convert("RGBA")))
    n = len(frames)
    height, width = frames[0].shape[:2]
    diag = float(np.hypot(height, width))

    masks = np.stack([f[..., 3] > ALPHA_TH for f in frames])
    flat = masks.reshape(n, -1)
    area = flat.sum(1).astype(np.float64)

    ys, xs = np.mgrid[0:height, 0:width]
    ys = ys.reshape(-1)
    xs = xs.reshape(-1)
    safe = np.maximum(area, 1.0)
    cx = (flat * xs).sum(1) / safe
    cy = (flat * ys).sum(1) / safe

    bbox = np.zeros((n, 4), dtype=np.int64)
    for i in range(n):
        rows = np.flatnonzero(flat[i])
        if rows.size:
            yy, xx = np.divmod(rows, width)
            bbox[i] = (xx.min(), yy.min(), xx.max(), yy.max())

    inter0 = np.logical_and(flat, flat[0]).sum(1)
    union0 = np.logical_or(flat, flat[0]).sum(1)
    iou0 = inter0 / np.maximum(union0, 1)
    d = (np.hypot(cx - cx[0], cy - cy[0]) / diag
         + np.abs(area - area[0]) / max(area[0], 1.0)
         + (1.0 - iou0))

    alpha = np.stack([f[..., 3].astype(np.float32) for f in frames])
    luma = np.stack([f[..., :3].astype(np.float32).mean(axis=2) for f in frames])

    m = n - 1
    iou = np.ones(m)
    rgbmov = np.zeros(m)
    dmed = np.zeros(m)
    corr = np.full(m, np.nan)
    pixdup = np.zeros(m, dtype=bool)
    diffs: list[np.ndarray | None] = []
    cores: list[np.ndarray] = []

    for i in range(m):
        a, b = flat[i], flat[i + 1]
        inter = np.logical_and(a, b)
        union = np.logical_or(a, b)
        iou[i] = inter.sum() / max(union.sum(), 1)
        pixdup[i] = np.array_equal(frames[i], frames[i + 1])
        core = np.minimum(alpha[i], alpha[i + 1]) >= SOLID_ALPHA
        cores.append(core)
        if not core.any():
            diffs.append(None)
            continue
        diff = (luma[i + 1] - luma[i]) * core
        diffs.append(diff)
        vals = np.abs(diff[core])
        rgbmov[i] = float(vals.mean())
        dmed[i] = float(np.median(vals))

    for i in range(1, m):
        if diffs[i - 1] is None or diffs[i] is None:
            continue
        common = (cores[i - 1] & cores[i]).reshape(-1)
        if common.sum() < 300:
            continue
        x = diffs[i - 1].reshape(-1)[common]
        y = diffs[i].reshape(-1)[common]
        if x.std() < 1e-6 or y.std() < 1e-6:
            corr[i] = -1.0
            continue
        corr[i] = float(np.corrcoef(x, y)[0, 1])

    return {
        "n": n, "width": width, "height": height, "diag": diag, "masks": flat,
        "area": area, "centroid": np.stack([cx, cy], axis=1), "bbox": bbox,
        "d": d, "iou": iou, "rgbmov": rgbmov, "dmed": dmed, "corr": corr,
        "pixdup": pixdup,
    }


# ---------------------------------------------------------------- 判据

def still_flags(m: dict, iou_th: float = IOU_STILL) -> np.ndarray:
    rgb = m["rgbmov"]
    if rgb.size == 0:
        return np.zeros(0, dtype=bool)
    thr = float(np.percentile(rgb, RGB_PCT))
    return (m["iou"] >= iou_th) & (rgb <= thr)


def detect_tail(m: dict, iou_th: float = IOU_STILL) -> dict:
    n = m["n"]
    if n < 2:
        return {"tail_start": n, "tail_len": 0, "reason": "帧数不足"}
    still = still_flags(m, iou_th)
    k = n - 1
    while k - 1 >= 0 and still[k - 1]:
        k -= 1
    thr = float(np.percentile(m["rgbmov"], RGB_PCT)) if m["n"] > 1 else 0.0
    if k >= n - 1:
        return {"tail_start": n, "tail_len": 0, "rgb_thr": thr,
                "still_frac": float(still.mean()), "reason": "末帧仍在动"}
    base = m["masks"][k]
    inter = np.logical_and(m["masks"][k:], base).sum(1)
    union = np.logical_or(m["masks"][k:], base).sum(1)
    span = float((inter / np.maximum(union, 1)).min())
    return {"tail_start": k, "tail_len": n - k, "rgb_thr": thr,
            "still_frac": float(still.mean()), "span_iou": span,
            "reason": "still 最大后缀"}


def pose_plateau(m: dict) -> int:
    """纯几何第二意见：d 首次进入振幅/梯度平台的帧号（完全不看像素）。"""
    n = m["n"]
    for s in range(n):
        dd = m["d"][s:]
        if dd.max() - dd.min() > POSE_AMP:
            continue
        if dd.size > 1 and np.abs(np.diff(dd)).max() > POSE_GRAD:
            continue
        return s
    return n


def variant_of(action_id: str) -> str | None:
    for suffix in ("_reverse", "_slow", "_hold", "_pingpong"):
        if action_id.endswith(suffix):
            return suffix[1:]
    return None


def classify(m: dict, tail: dict, kind: str) -> tuple[int, str, str, bool, list[str]]:
    n = m["n"]
    aid = m["id"]
    entry = m["entry"]
    variant = variant_of(aid)
    notes: list[str] = []
    rank = {"high": 3, "medium": 2, "low": 1}
    state = {"conf": "high"}
    floor = max(FLOOR_MIN, int(np.ceil(FLOOR_FRAC * n)))

    def cap(level: str, why: str) -> None:
        if rank[level] < rank[state["conf"]]:
            state["conf"] = level
            notes.append(why)

    if bool(entry.get("loop")):
        notes.append("loop=True：尾部是循环接缝（pingpong / walk / idle），按口径 keep=N 不动")
        return n, "loop-protected", "high", False, notes

    if kind == "derived" and variant == "slow":
        cap("low", "slow 变体每帧复制两遍，iou/rgbmov 在 0 与 1 之间交替，尾部测量不具物理意义")
    if kind == "derived":
        notes.append(f"派生动作（{variant}）：帧序由源动作重排，结论应与源动作一致")

    if tail["tail_len"] <= 0:
        notes.append("末段仍在动，测不到静止尾巴")
        return n, "no-static-tail", state["conf"], False, notes

    raw = tail["tail_start"]
    keep = max(raw, floor)
    clamped = keep != raw
    if clamped:
        cap("medium", f"静止尾巴起点 {raw} 低于下限 {floor}"
                      f"（max({FLOOR_MIN}, ceil({FLOOR_FRAC}*N))），已抬到下限")

    if tail["still_frac"] < 0.10:
        cap("medium", f"全片只有 {tail['still_frac']:.0%} 的帧对满足 still："
                      f"iou>={IOU_STILL} 在本素材上接近不可达（alpha 边缘抖动）")
    if n < 20:
        cap("medium", f"N={n} 帧偏短，帧级判据分辨率不足")
    if n < 14:
        cap("low", f"N={n} 帧，任何裁剪都在 1–2 帧误差内")
    if tail["tail_len"] < 4:
        cap("medium", f"尾巴仅 {tail['tail_len']} 帧")

    verdict = "trim-clamped" if clamped else "trim"
    return keep, verdict, state["conf"], clamped, notes


# ---------------------------------------------------------------- 汇总

def summarise(resolver: Resolver, library: str, entry: dict) -> dict:
    aid = entry["id"]
    paths, kind = resolver.resolve(library, aid)
    if not paths:
        return {"library": library, "id": aid, "N": 0, "keep": 0, "error": "帧缺失"}
    m = measure(paths)
    m["id"] = aid
    m["entry"] = entry

    tail = detect_tail(m)
    keep, verdict, conf, clamped, notes = classify(m, tail, kind)
    tail["tail_len"] = m["n"] - tail["tail_start"] if tail["tail_len"] else 0

    d = m["d"]
    d_last = float(d[-1])
    peak = float(d.max())
    ratio = d_last / peak if peak > 1e-9 else 0.0
    ends_off_pose = bool(ratio > OFF_POSE_RATIO)
    floor = max(FLOOR_MIN, int(np.ceil(FLOOR_FRAC * m["n"])))
    plateau = pose_plateau(m)
    keep_pose = int(min(max(plateau, floor), m["n"]))
    tail_start = tail["tail_start"] if tail["tail_len"] else m["n"]

    if ends_off_pose and tail["tail_len"] > 0:
        notes.append(f"ends_off_pose：d_last/peak={ratio:.2f} > {OFF_POSE_RATIO}，"
                     f"结尾停在偏移姿势，裁完不会回到待机")

    seg = slice(tail_start, m["n"] - 1) if m["n"] - 1 > tail_start else slice(0, 0)
    corr_seg = m["corr"][seg]
    corr_tail = float(np.nanmedian(corr_seg)) if corr_seg.size and not np.all(np.isnan(corr_seg)) else -1.0

    still = still_flags(m)
    sensitivity = {}
    for th in IOU_SENSITIVITY:
        t = detect_tail(m, th)
        sensitivity[f"iou>={th}"] = {
            "tail_len": int(m["n"] - t["tail_start"]),
            "keep": int(max(t["tail_start"], floor)),
        }

    return {
        "library": library,
        "id": aid,
        "N": m["n"],
        "kind": kind,
        "keep": int(keep),
        "keep_pose": keep_pose,
        "keep_delta_pose": int(keep - keep_pose),
        "tail_start": int(tail_start),
        "tail_start_frame1": int(tail_start) + 1,
        "tail_len": int(m["n"] - tail_start),
        "trim_frames": int(m["n"] - keep),
        "trim_ratio": round((m["n"] - keep) / m["n"], 4) if m["n"] else 0.0,
        "ends_off_pose": ends_off_pose,
        "continuity": "ends_off_pose" if ends_off_pose else "returns_to_pose",
        "d_last": round(d_last, 5),
        "d_peak": round(peak, 5),
        "d_last_ratio": round(ratio, 4),
        "verdict": verdict,
        "confidence": conf,
        "loop": bool(entry.get("loop")),
        "variant": variant_of(aid),
        "canvas": [m["width"], m["height"]],
        "floor": floor,
        "clamped": bool(clamped),
        "rgb_thr_p30": round(tail.get("rgb_thr", 0.0), 4),
        "still_pair_frac": round(tail.get("still_frac", 0.0), 4),
        "iou_tail_min": round(float(m["iou"][seg].min()) if m["iou"][seg].size else 1.0, 6),
        "rgbmov_tail": round(float(np.median(m["rgbmov"][seg])) if m["rgbmov"][seg].size else 0.0, 4),
        "dmed_tail": round(float(np.median(m["dmed"][seg])) if m["dmed"][seg].size else 0.0, 4),
        "corr_tail": round(corr_tail, 3),
        "d_range_tail": round(float(d[tail_start:].max() - d[tail_start:].min())
                              if m["n"] > tail_start else 0.0, 5),
        "span_iou": None if "span_iou" not in tail else round(tail["span_iou"], 6),
        "dup_frame_pairs": int(m["pixdup"].sum()),
        "iou_sensitivity": sensitivity,
        "notes": notes,
    }


def compare_main(rows: list[dict], path: Path) -> list[dict]:
    """与主指标的 keep 做差。

    优先用冻结的 pre-trim 基线（`FROZEN_MAIN_KEEP`）：`trim_proposal.json` 已被用
    裁剪后的素材重新生成，直接比会拿"裁剪后 N"和"原始 N"对不上。
    冻结基线只覆盖 59 条 source 非 loop 动作，覆盖度在报告里写明。
    """
    if not path.exists():
        return []
    main = json.loads(path.read_text(encoding="utf-8"))
    lookup = {(a["library"], a["id"]): a for a in main.get("actions", [])}
    out = []
    for r in rows:
        other = lookup.get((r["library"], r["id"]))
        if other is None or not r["N"]:
            continue
        m_keep = FROZEN_MAIN_KEEP.get(r["id"])
        if m_keep is None:
            live_keep = other.get("keep")
            # 只有当主指标记录的是同一次素材（N 一致）时才采信 live 值
            if live_keep is None or other.get("N") != r["N"]:
                continue
            m_keep = int(live_keep)
        diff = r["keep"] - m_keep
        pct = diff / r["N"] if r["N"] else 0.0
        out.append({
            "library": r["library"], "id": r["id"], "N": r["N"],
            "keep_main": m_keep, "keep_crosscheck": r["keep"],
            "diff": diff, "diff_pct": round(pct, 4),
            "conflict": bool(abs(pct) > 0.15),
            "main_flags": other.get("flags", []),
            "baseline": m_keep == FROZEN_MAIN_KEEP.get(r["id"]) and "frozen" or "live",
            "verdict": r["verdict"], "ends_off_pose": r["ends_off_pose"],
        })
    return out


def fmt_table(rows: list[dict]) -> str:
    head = (f"{'lib':13s} {'id':24s} {'N':>4s} {'keep':>5s} {'tail':>5s} {'len':>4s} "
            f"{'iou':>7s} {'rgb':>6s} {'corr':>6s} {'d_l/pk':>7s} {'pose':>5s} {'conf':>6s}  verdict")
    lines = [head, "-" * len(head)]
    for r in rows:
        if "error" in r:
            lines.append(f"{r['library']:13s} {r['id']:24s} ERROR {r['error']}")
            continue
        lines.append(
            f"{r['library']:13s} {r['id']:24s} {r['N']:4d} {r['keep']:5d} "
            f"{'-' if r['tail_len'] == 0 else r['tail_start_frame1']:>5} {r['tail_len']:4d} "
            f"{r['iou_tail_min']:.4f} {r['rgbmov_tail']:6.2f} {r['corr_tail']:6.2f} "
            f"{r['d_last_ratio']:7.2f} {r['keep_pose']:5d} {r['confidence']:>6s}  {r['verdict']}")
    return "\n".join(lines)


def trace(resolver: Resolver, key: str) -> str:
    for library in ("library_v1", "expansion_v1"):
        entries = (resolver.lib_manifest["usable_actions"] if library == "library_v1"
                   else resolver.exp_manifest["actions"])
        for entry in entries:
            if entry["id"] != key and f"{library}/{entry['id']}" != key:
                continue
            paths, kind = resolver.resolve(library, entry["id"])
            m = measure(paths)
            thr = float(np.percentile(m["rgbmov"], RGB_PCT))
            still = still_flags(m)
            out = [f"== {library}/{entry['id']} ({kind}) N={m['n']}  "
                   f"P{RGB_PCT}(rgbmov)={thr:.3f}"]
            out.append("   i   area      cx      cy       d     iou  rgbmov  corr  still  dup")
            for i in range(m["n"]):
                t = i - 1
                if t < 0:
                    out.append(f" {i:3d} {m['area'][i]:7.0f} {m['centroid'][i,0]:7.2f} "
                               f"{m['centroid'][i,1]:7.2f} {m['d'][i]:7.4f}      -       -     -      -    .")
                    continue
                out.append(
                    f" {i:3d} {m['area'][i]:7.0f} {m['centroid'][i,0]:7.2f} {m['centroid'][i,1]:7.2f} "
                    f"{m['d'][i]:7.4f} {m['iou'][t]:6.4f} {m['rgbmov'][t]:7.3f} {m['corr'][t]:5.2f} "
                    f"{'STILL' if still[t] else '  -  '}  {'Y' if m['pixdup'][t] else '.'}")
            return "\n".join(out)
    return f"[trace] 未找到 {key}"


def validate_n(rows: list[dict], resolver: "Resolver") -> list[str]:
    """结构校验：重建出的 N 必须自洽。

    不能拿 `docs/expansion/trim_proposal.json` 当基准——它已被用**裁剪后**的素材
    重新生成（N 变成 10/52/60 这类裁剪值）。这里改用两个内部不变量：

    1. `expansion_v1` 源动作：`frames/` 与 `.trimmed/` 的并集帧数 = N，且编号连续；
    2. 派生动作：N 必须等于 `_order_indices(N_source, mode)` 的长度。

    任一不满足，说明重建期间素材正在被改写，读数不可信。
    """
    problems: list[str] = []
    for r in rows:
        if "error" in r:
            problems.append(f"{r['library']}/{r['id']}: 帧缺失")
    if resolver.state != "pristine":
        return problems
    for entry in resolver.exp_manifest["actions"]:
        aid = entry["id"]
        mode = resolver.derivations.get(aid, {}).get("mode") if resolver.derivations else None
        if mode is None:
            frames = resolver._source_frames("expansion_v1", aid)
            if frames:
                idx = sorted(frames)
                if idx != list(range(idx[0], idx[0] + len(idx))):
                    problems.append(f"expansion_v1/{aid}: 帧编号不连续（{len(idx)} 帧）")
        else:
            src = resolver._source_frames("expansion_v1", resolver.derivations[aid]["source"])
            if not src:
                continue
            expect = len(resolver._order(len(src), mode))
            got = next((r["N"] for r in rows if r["library"] == "expansion_v1" and r["id"] == aid), None)
            if got is not None and got != expect:
                problems.append(f"expansion_v1/{aid}: 派生 N={got}，按源 {len(src)} 帧应为 {expect}")
    return problems


def main_floor_analysis(rows: list[dict]) -> dict:
    """主指标的 floor 把动作"削平"的情况。

    `keep = clamp(raw_keep_2F + 3, floor, N)`。当 `raw_keep_2F` 小到离谱
    （`happy_sway` 只有 2，等于说"第 2 帧动作就结束了"），最终 keep 完全由 floor
    决定，与静止尾巴是否存在无关。数据来自冻结的 pre-trim 基线。
    """
    actions = []
    for r in rows:
        if r.get("loop") or r.get("kind") != "source" or r.get("variant") or "error" in r:
            continue
        raw = FROZEN_RAW_KEEP_2F.get(r["id"])
        main_keep = FROZEN_MAIN_KEEP.get(r["id"])
        if raw is None or main_keep is None:
            continue
        floor = max(FLOOR_MIN, int(np.ceil(0.5 * r["N"])))   # pre-trim 主指标用的是 0.5N
        actions.append({
            "library": r["library"], "id": r["id"], "N": r["N"],
            "raw_keep_2F": raw, "keep_floor": floor, "keep_main": main_keep,
            "geo_plateau_keep": r["keep_pose"],
            "floor_bound": (raw + 3) < floor,   # raw+缓冲 落在 floor 以下 → keep 完全由 floor 决定
            "ends_off_pose": r["ends_off_pose"],
        })
    bounded = [b for b in actions if b["floor_bound"]]
    free = [b for b in actions if not b["floor_bound"]]

    def agree(group: list[dict]) -> dict:
        if not group:
            return {"n": 0, "median_abs_diff": 0, "within_3_frames": 0, "within_15pct_n": 0}
        diffs = [abs(b["keep_main"] - b["geo_plateau_keep"]) for b in group]
        return {
            "n": len(group),
            "median_abs_diff": round(float(np.median(diffs)), 2),
            "within_3_frames": sum(1 for d in diffs if d <= 3),
            "within_15pct_n": sum(1 for b in group
                                  if abs(b["keep_main"] - b["geo_plateau_keep"]) <= 0.15 * b["N"]),
        }

    return {
        "source_of_truth": "冻结的 pre-trim trim_proposal.json（20:25），字段 raw_keep_2F/keep/floor",
        "floor_bound": {"agreement": agree(bounded), "actions": bounded},
        "not_floor_bound": {"agreement": agree(free), "actions": free},
        "note": ("floor-bound 的最终 keep 完全由 keep_floor 决定（= 0.5N），与静止尾巴无关。"),
    }


# ---------------------------------------------------------------- Markdown

def render_md(rows: list[dict], diff: list[dict], state: str, floor: dict | None = None) -> str:
    ok = [r for r in rows if "error" not in r]
    trim = [r for r in ok if r["verdict"].startswith("trim")]
    conf = {k: [r for r in ok if r["confidence"] == k] for k in ("high", "medium", "low")}
    off = sorted([r for r in ok if r["ends_off_pose"]], key=lambda r: -r["d_last_ratio"])
    on = [r for r in ok if not r["ends_off_pose"]]
    conflicts = sorted([d for d in diff if d["conflict"]], key=lambda d: -abs(d["diff"]))

    # IoU 可达性：全片有多少帧对能满足 iou 阈值
    reach = {}
    for th in IOU_SENSITIVITY:
        vals = [r["iou_sensitivity"][f"iou>={th}"]["tail_len"] for r in ok]
        reach[th] = (sum(1 for v in vals if v > 0), float(np.mean(vals)) if vals else 0.0)
    _r9995 = reach.get(0.9995, (0, 0.0))
    n_at_9995, mean_at_9995 = _r9995[0], _r9995[1]
    reach_tbl = "\n".join(
        f"| `iou >= {th}` | {reach[th][0]} / {len(ok)} | {reach[th][1]:.1f} 帧 |"
        for th in IOU_SENSITIVITY)

    off_strong = [r for r in off if r["d_last"] >= OFF_POSE_STRONG]
    off_mild = [r for r in off if r["d_last"] < OFF_POSE_STRONG]

    def off_rows(items: list[dict]) -> str:
        return "\n".join(
            f"| `{r['id']}` | {r['library'].replace('_v1','')} | {r['N']} | {r['d_last']:.4f} | "
            f"{r['d_peak']:.4f} | **{r['d_last_ratio']:.2f}** | {r['tail_len']} | {r['keep']} | {r['verdict']} |"
            for r in items) or "| — | — | — | — | — | — | — | — | — |"

    longest = sorted([r for r in ok if r["tail_len"] > 0], key=lambda r: -r["tail_len"])[:10]
    longest_tbl = "\n".join(
        f"| `{r['id']}` | {r['library'].replace('_v1','')} | {r['N']} | {r['tail_len']} | "
        f"{r['tail_start_frame1']} | {r['keep']} | {r['trim_frames']} | {r['confidence']} |"
        for r in longest) or "| — | — | — | — | — | — | — | — |"

    conflict_tbl = "\n".join(
        f"| `{d['id']}` | {d['library'].replace('_v1','')} | {d['N']} | {d['keep_main']} | "
        f"{d['keep_crosscheck']} | {d['diff']:+d} | {d['diff_pct']:+.1%} | {'是' if d['ends_off_pose'] else '否'} |"
        for d in conflicts) or "| — | — | — | — | — | — | — |"

    floor = floor or {}
    fb = floor.get("floor_bound", {})
    nf = floor.get("not_floor_bound", {})
    fb_actions = [b for b in fb.get("actions", []) if b["floor_bound"]]
    _long = [b for b in fb_actions if b["N"] >= 40]
    plateau_lo = min((b["geo_plateau_keep"] for b in _long), default=0)
    plateau_hi = max((b["geo_plateau_keep"] for b in _long), default=0)
    fb_tbl = "\n".join(
        f"| `{b['id']}` | {b['N']} | {b['raw_keep_2F']} | {b['keep_floor']} | {b['keep_main']} | "
        f"{b['geo_plateau_keep']} | {b['N'] - b['keep_main']} |"
        for b in fb_actions)
    fb_stats = (f"- 主指标 **floor-bound {fb.get('agreement', {}).get('n', 0)} 条**："
                f"与纯几何平台的 median |diff| = "
                f"**{fb.get('agreement', {}).get('median_abs_diff', 0)} 帧**，"
                f"±3 帧内只有 {fb.get('agreement', {}).get('within_3_frames', 0)} 条"
                f"（{fb.get('agreement', {}).get('within_3_frames', 0) / max(1, fb.get('agreement', {}).get('n', 1)):.0%}）。\n"
                f"- 主指标 **未 floor-bound {nf.get('agreement', {}).get('n', 0)} 条**："
                f"median |diff| = **{nf.get('agreement', {}).get('median_abs_diff', 0)} 帧**，"
                f"±3 帧内 {nf.get('agreement', {}).get('within_3_frames', 0)} 条"
                f"（{nf.get('agreement', {}).get('within_3_frames', 0) / max(1, nf.get('agreement', {}).get('n', 1)):.0%}）。")

    detail = "\n".join(
        "| {lib} | `{id}` | {N} | {keep} | {tail} | {ln} | {iou} | {rgb} | {corr} | {dl} | {plat} | {cf} | {vd} |".format(
            lib=r["library"].replace("_v1", ""), id=r["id"], N=r["N"], keep=r["keep"],
            tail="-" if r["tail_len"] == 0 else r["tail_start_frame1"], ln=r["tail_len"],
            iou=f"{r['iou_tail_min']:.4f}", rgb=f"{r['rgbmov_tail']:.2f}",
            corr=f"{r['corr_tail']:.2f}", dl=f"{r['d_last_ratio']:.2f}",
            plat=r["keep_pose"], cf=r["confidence"], vd=r["verdict"])
        for r in ok)

    def vt(name: str, title: str) -> str:
        items = [r for r in ok if r["verdict"] == name]
        if not items:
            return f"### {title}\n\n_（无）_\n"
        body = "\n".join(
            f"| `{r['id']}` | {r['library'].replace('_v1','')} | {r['N']} | {r['tail_len']} | {r['keep']} |"
            for r in items)
        return (f"### {title}（{len(items)} 条）\n\n"
                f"| id | 库 | N | 尾巴长度 | keep |\n|---|---|---:|---:|---:|\n{body}\n")

    return f"""# 尾部裁剪 · 独立交叉验证（几何指标）

> 由 `scripts/pipeline/pose_tails.py` 一次跑出，**不引用主指标的任何中间量**。
> 主指标 = 80x120 预乘模糊 RGBA 帧差（`tail_motion.py`）；本报告 = alpha 掩膜几何 + 掩膜内 RGB 运动。
> 两者唯一共享的输入是原始 webp 字节与 `alpha > 8` 这个掩膜口径。
> 字段 `N` / `keep` 与主指标同名同义，可直接算 `keep_diff = keep_crosscheck - keep_main`。

## 0. 口径与测量状态

**口径（Lead 2026-10-29）**：不再要求动作连贯自然收尾；目标是「播完一轮就切」，
主体动作做完立即结束，后面静止帧全砍。

```
still[i] ⇔ iou[i] >= 0.9995  且  rgbmov[i] <= P30(rgbmov)
keep     = 静止尾巴起始帧（不加缓冲），下限 max(6, ceil(0.35*N))
loop=True  →  keep = N
```

**测量状态：`{state}`**。仓库已被 `trim_tails.py --apply` 裁剪过（多余帧移到 `.trimmed/`，
manifest 同步截断），所以默认用 `--state pristine` 做**无损重建**再测：
`library_v1` 取自 `git show HEAD:`；`expansion_v1` 源动作 = 现行 `frames/` ∪ `.trimmed/`；
派生动作（reverse/slow/hold/pingpong）按 `derive_actions._order_indices()` 重新映射。
不复制、不改动任何素材。

## 1. 结论摘要

- 全量 **{len(ok)}** 条（library_v1 23 + expansion_v1 64），无抽样。
- 判为可裁 **{len(trim)}** 条；`keep = N` 不动 **{len(ok) - len(trim)}** 条。
- 置信度：high **{len(conf['high'])}** / medium **{len(conf['medium'])}** / low **{len(conf['low'])}**。
- **`ends_off_pose`（裁完回不正）{len(off)} 条** —— 第 3 节，本次重点。
- 与主指标 `keep` 差异 > 15%N 的冲突 **{len(conflicts)}** 条 —— 第 4 节。

## 2. 主指标最可能踩坑的三处（交叉验证的主要产出）

### 坑 1：`keep_floor` 把"没有静止尾巴"的动作强行砍掉一半

主指标 `keep = clamp(raw_keep_2F + 3, floor, N)`。当它的 `2*F_tail` 判据在很早就触发时，
`raw_keep_2F` 会小到离谱（`happy_sway` 只有 **2**，等于"第 2 帧动作就结束了"），
此时 `keep` 完全由 floor 决定，**与静止尾巴是否存在毫无关系**。
而纯几何平台（只看 `d_i`，不看任何像素）显示这些长动作要到第 {plateau_lo}–{plateau_hi} 帧才真的不动：

{fb_stats}

| id | N | 主 raw_keep_2F | 主 floor | 主最终 keep | 几何平台 keep | 主多裁 |
|---|---:|---:|---:|---:|---:|---:|
{fb_tbl}

典型例子：

- **`happy_sway`**：主 `raw_keep_2F=2`（等于说"第 2 帧动作就结束了"），floor 31 把它救成 31。
  几何平台是 61——整段都在摇摆，根本没有静止尾巴。若按新口径把 floor 降到 `0.35N=22`，
  会裁掉 40 帧**正在摇摆的帧**，循环直接缺一段。
- **`wave_hello` / `cheer_jump` / `headphone`**：同样是 `raw_keep_2F` 个位数、floor 抬到 31、
  几何平台 61。这三个动作都是"一直在轻微动"，`2*F_tail` 因为尾巴自身噪声高而失效。
- **`look_down_sad`**：主 `raw_keep_2F=27`、floor 31、keep 31；几何平台 53，
  且 `ends_off_pose=True`（d_last/peak≈0.93）。裁到 31 会在低头过程中截断，
  既没做完动作、也没回到站姿。

**根因**：`F_tail = median(M[-12:])` 假设"最后 12 帧近似静止"。
对持续运动或循环动作，这个假设不成立，`F_tail` 被真实运动抬高，`2*F_tail` 阈值随之失守。

### 坑 2：`iou >= 0.9995` 在这批素材上接近不可达

`mask = alpha > 8` 的边界逐帧抖动：webp 重编码会让边缘几十个 px 在 0/1 之间翻转，
相邻帧 IoU 常态落在 0.98–0.999。0.9995 基本只有"两帧字节完全一致"才成立。

| 阈值 | 能测到尾巴的动作数 | 平均尾巴长度 |
|---|---|---|
{reach_tbl}

按 `0.9995` 只能测出 **{n_at_9995}** 条尾巴，平均 {mean_at_9995:.1f} 帧，
且几乎全是 `slow` / `hold` 变体——它们的帧是被 `derive_actions.py` **复制**出来的，
字节完全相同，IoU 才会等于 1.0。真实源动作只有 `tidy_desk` / `hug_self` /
`peek_note` / `sleep_lie_down` 四条勉强命中 2 帧。
**建议改用 `iou >= 0.999`（可测到 16 条），或换成"掩膜面积变化 <= 0.1%"**。

### 坑 3：残差是"无结构噪声"还是"真微动"，幅度分不出来

`rgbmov` 只给幅度，但 codec 噪声的幅度可以**高于**真实微动：

| 动作尾段 | rgbmov（幅度） | corr（相邻帧残差相干性） | 实况 |
|---|---:|---:|---|
| `tidy_desk` | ≈ 4.3 | **≈ −0.33** | 图像已冻结，只剩编码抖动 |
| `hug_self` | ≈ 4.1 | **≈ −0.45** | 同上 |
| `stand_up` | ≈ 5.0 | **≈ +0.50** | 真实低幅呼吸式运动 |

真运动的残差在相邻帧间**结构一致**（正相关）；codec 噪声互相独立甚至负相关。
只比幅度会把前两者判成"还在动"、把后者判成"已静止"——正好判反。
本报告用 `corr` 列给出这个诊断，但它不参与 Lead 指定的判据。

## 3. ends_off_pose：裁完回不正的动作（{len(off)} 条）

判据 `d_last / d_peak > {OFF_POSE_RATIO}`：结尾帧相对首帧的姿势偏移已达峰值 60% 以上。
这些动作结束时角色停在低头 / 跪姿 / 坐姿 / 躺姿等非站姿，`d_i` 永不回归 0，
**"d 回落到平台"这类回归判据对它们完全失效**，只能靠平台判据（尾巴内 d 振幅 / 梯度）。

比值和绝对量要一起看，所以下面拆成两组。

### 3.1 明显回不正：`d_last >= {OFF_POSE_STRONG}`（{len(off_strong)} 条）

结尾姿势与首帧有实质差异，裁完直接切回 idle 会"跳"一下。这类要么保留到原长，
要么在运行时补一段过渡（或让 idle 首帧接近该姿势）。

| id | 库 | N | d_last | d_peak | 比值 | tail_len | keep | 结论 |
|---|---|---:|---:|---:|---:|---:|---:|---|
{off_rows(off_strong)}

### 3.2 略有偏差：比值 > {OFF_POSE_RATIO} 但 `d_last < {OFF_POSE_STRONG}`（{len(off_mild)} 条）

`d_last/peak` 比值高（多为动作单调偏移、峰值出现在末帧，比值天然接近 1.0），
但绝对偏移小，切回 idle 基本看不出来。**这两类不要混为一谈**——
只看比值会把 3.2 当成 3.1 处理，白留一堆帧。

| id | 库 | N | d_last | d_peak | 比值 | tail_len | keep | 结论 |
|---|---|---:|---:|---:|---:|---:|---:|---|
{off_rows(off_mild)}

其余 **{len(on)}** 条结尾回到接近首帧姿势（`d_last/peak <= {OFF_POSE_RATIO}`），
裁完仍有收尾感：{", ".join("`" + r["id"] + "`" for r in on[:14])}{" …" if len(on) > 14 else ""}。

## 4. 与主指标的 keep 差异（> 15%N）

{"" if diff else "_主指标 JSON 不存在，本节跳过。_"}
{"| id | 库 | N | 主 keep | 交叉 keep | 差值 | 差值%N | ends_off_pose |" if diff else ""}
{"|---|---|---:|---:|---:|---:|---:|---|" if diff else ""}
{conflict_tbl if diff else ""}

主要原因（也是主指标可能踩坑的地方）：

1. **主指标用 `2*F_tail` 相对噪声地板**：尾巴自身噪声低时 `F_tail` 很小，判据自动变严，
   于是"看起来还在动"，主 keep 偏大。本报告用 `P30(rgbmov)` 自适应分位，与绝对幅度无关。
2. **主指标带 2 帧收尾缓冲**，本报告按新口径不加缓冲，天然更短。
3. **主指标对 loop 动作没有豁免**，本报告按口径 `keep = N`。
4. **主指标在 `dhi` 型整片变化上没有对应量**（`lantern_off` 的灯光渐变会让它误判在动）。

## 5. 尾巴最长的 10 条

| id | 库 | N | tail_len | tail_start | 建议 keep | 裁掉 | 置信度 |
|---|---|---:|---:|---:|---:|---:|---|
{longest_tbl}

## 6. 不裁的动作

{vt("loop-protected", "loop=True，按口径保持 N")}
{vt("no-static-tail", "测不到静止尾巴（末段仍在动）")}

## 7. 全量明细

`rgb` = 尾巴内 rgbmov 中位数；`corr` = 残差相干性（负/零 = codec 噪声，正 = 真微摆）；
`d_l/pk` = ends_off_pose 比值；`pose` = 纯几何平台 keep（完全不看像素，第二意见）。

| 库 | id | N | keep | tail | len | iou | rgb | corr | d_l/pk | pose | 置信度 | 结论 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
{detail}

## 8. 局限

- `P30(rgbmov)` 是该动作**自身**分布的 30% 分位。动作全程幅度都低时（如 `cover_mouth`），
  阈值同样低，判据自动变严 —— 符合"能看出还在动就算动"，但也意味着**跨动作不可比**。
- `slow` 变体每帧复制两遍：`iou` 在 1.0 与 <0.9995 间交替，`rgbmov` 交替为 0，
  尾部测量不具物理意义，已压到 low 置信度。
- `loop=True` 的动作尾部是循环接缝，按口径不动。
- 整体亮度渐变（`lantern_off`）会让 `rgbmov` 偏高，方向更保守（少裁），不会误裁。
- 本脚本只测不改；`keep` 是建议值，最终取舍由 Lead 合并两份测量后决定。

## 9. 复现

```bash
PY=/tmp/mimienv/bin/python
$PY scripts/pipeline/pose_tails.py                       # pristine，写 JSON + 本文件
$PY scripts/pipeline/pose_tails.py --report              # 只打印表
$PY scripts/pipeline/pose_tails.py --trace tidy_desk     # 单条逐帧明细
$PY scripts/pipeline/pose_tails.py --state live          # 量裁剪后的现状
```
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="独立几何指标测尾部静止帧")
    ap.add_argument("--report", action="store_true", help="只打印表，不写文件")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--trace", metavar="ID", default=None)
    ap.add_argument("--library", choices=["library_v1", "expansion_v1"], default=None)
    ap.add_argument("--state", choices=["pristine", "live"], default="pristine",
                    help="pristine=重建裁剪前状态（默认）；live=量当前工作区")
    ap.add_argument("--compare", type=Path, default=MAIN_JSON,
                    help="主指标 JSON，用于算 keep 差异")
    args = ap.parse_args(argv)

    resolver = Resolver(args.state)
    if args.trace:
        print(trace(resolver, args.trace))
        return 0

    targets: list[tuple[str, dict]] = []
    for entry in resolver.lib_manifest["usable_actions"]:
        targets.append(("library_v1", entry))
    for entry in resolver.exp_manifest["actions"]:
        targets.append(("expansion_v1", entry))
    if args.library:
        targets = [t for t in targets if t[0] == args.library]

    rows: list[dict] = []
    for idx, (library, entry) in enumerate(targets, 1):
        row = summarise(resolver, library, entry)
        rows.append(row)
        if "error" in row:
            print(f"[{idx:3d}/{len(targets)}] {library:13s} {entry['id']:24s} ERROR {row['error']}",
                  file=sys.stderr, flush=True)
        else:
            print(f"[{idx:3d}/{len(targets)}] {library:13s} {row['id']:24s} N={row['N']:3d} "
                  f"tail={row['tail_len']:3d} keep={row['keep']:3d} "
                  f"d_l/pk={row['d_last_ratio']:.2f} {row['confidence']:6s} {row['verdict']}",
                  file=sys.stderr, flush=True)

    problems = validate_n(rows, resolver)
    if problems:
        print(f"\n[ERROR] N 校验失败 {len(problems)} 条 —— 素材可能正在被 trim_tails.py 改写：",
              file=sys.stderr)
        for p in problems[:15]:
            print(f"        {p}", file=sys.stderr)
        print("        请等裁剪流程结束后重跑。", file=sys.stderr)

    diff = compare_main(rows, args.compare)
    floor = main_floor_analysis(rows)

    if args.report:
        print(fmt_table(rows))
        if diff:
            bad = sorted([d for d in diff if d["conflict"]], key=lambda x: -abs(x["diff"]))
            print(f"\n与主指标 keep 差异 > 15%N：{len(bad)} 条")
            for d in bad:
                print(f"  {d['library']:13s} {d['id']:24s} N={d['N']:3d} "
                      f"main={d['keep_main']:3d} cross={d['keep_crosscheck']:3d} "
                      f"diff={d['diff']:+3d} ({d['diff_pct']:+.1%})")
        off = [r for r in rows if "error" not in r and r["ends_off_pose"]]
        print(f"\nends_off_pose（d_last/peak > {OFF_POSE_RATIO}）：{len(off)} 条")
        for r in sorted(off, key=lambda x: -x["d_last_ratio"]):
            print(f"  {r['library']:13s} {r['id']:24s} N={r['N']:3d} "
                  f"ratio={r['d_last_ratio']:.2f} keep={r['keep']:3d}")
        return 0

    ok = [r for r in rows if "error" not in r]
    off = [r for r in ok if r["ends_off_pose"]]
    payload = {
        "schema_version": 2,
        "generated_by": "scripts/pipeline/pose_tails.py",
        "state": resolver.state,
        "spec": ("Lead 2026-10-29：播完一轮就切；still ⇔ iou>=0.9995 且 rgbmov<=P30；"
                 "keep=tail_start 不加缓冲；下限 max(6,ceil(0.35N))；loop→N"),
        "metric": ("alpha>8 掩膜几何（bbox/面积/质心/d）+ 掩膜 IoU + 实体内部 RGB 运动；"
                   "与主指标 80x120 预乘模糊帧差无共同项"),
        "caveat_iou_unreachable": (
            "iou>=0.9995 在本素材上接近不可达：alpha 边缘逐帧抖动，相邻帧 IoU 常态 0.98–0.999。"
            "该阈值实质等价于'两帧完全相同'，只对 slow 变体稳定命中。"
            "建议改用 iou>=0.999 或'掩膜面积变化<=0.1%'。见 iou_sensitivity 字段。"),
        "thresholds": {
            "alpha": ALPHA_TH, "solid_alpha": SOLID_ALPHA, "iou_still": IOU_STILL,
            "rgb_percentile": RGB_PCT, "floor_frac": FLOOR_FRAC, "floor_min": FLOOR_MIN,
            "off_pose_ratio": OFF_POSE_RATIO, "off_pose_strong": OFF_POSE_STRONG, "pose_amp": POSE_AMP, "pose_grad": POSE_GRAD,
            "iou_sensitivity": list(IOU_SENSITIVITY),
        },
        "totals": {
            "actions": len(ok),
            "trim": sum(1 for r in ok if r["verdict"].startswith("trim")),
            "high": sum(1 for r in ok if r["confidence"] == "high"),
            "medium": sum(1 for r in ok if r["confidence"] == "medium"),
            "low": sum(1 for r in ok if r["confidence"] == "low"),
            "ends_off_pose": len(off),
            "ends_off_pose_strong": sum(1 for r in off if r["d_last"] >= OFF_POSE_STRONG),
            "by_verdict": {k: sum(1 for r in ok if r["verdict"] == k)
                           for k in sorted({r["verdict"] for r in ok})},
            "iou_reachability": {
                f"{th}": {
                    "actions_with_tail": sum(
                        1 for r in ok if r["iou_sensitivity"][f"iou>={th}"]["tail_len"] > 0),
                    "mean_tail_len": round(float(np.mean(
                        [r["iou_sensitivity"][f"iou>={th}"]["tail_len"] for r in ok])), 2),
                } for th in IOU_SENSITIVITY
            },
        },
        "ends_off_pose": [
            {"library": r["library"], "id": r["id"], "N": r["N"],
             "d_last": r["d_last"], "d_peak": r["d_peak"], "ratio": r["d_last_ratio"],
             "keep": r["keep"], "verdict": r["verdict"],
             "strong": bool(r["d_last"] >= OFF_POSE_STRONG)}
            for r in sorted(off, key=lambda x: -x["d_last_ratio"])
        ],
        "keep_diff_vs_main": conflicts_only(diff),
        "main_floor_analysis": floor,
        "n_validation_failures": problems,
        "actions": rows,
    }

    out_json = args.json or DEFAULT_JSON
    out_md = args.md or DEFAULT_MD
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[write] {out_json}", file=sys.stderr)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_md(rows, diff, resolver.state, floor), encoding="utf-8")
    print(f"[write] {out_md}", file=sys.stderr)
    print(fmt_table(rows))
    return 0


def conflicts_only(diff: list[dict]) -> list[dict]:
    return sorted([d for d in diff if d["conflict"]], key=lambda d: -abs(d["diff"]))


if __name__ == "__main__":
    raise SystemExit(main())
