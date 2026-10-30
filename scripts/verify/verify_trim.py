#!/usr/bin/env python
"""裁剪验证器：证明动作素材「裁掉尾巴」前后都没有坏，并量化尾巴有多长。

七项检查（对应 task-4）：
    1. manifest 结构：两套库每条动作 len(frames) == len(duration_ms)，id 唯一。
    2. 帧文件：全部存在、能解码、尺寸与声明 canvas / 素材库基准一致（不符只记警告）。
    3. 尾巴检查（核心）：still_tail_ratio = 最后 15% 帧的平均运动 ÷ 动作峰值运动，
       外加"尾部静止段"的帧数与秒数（含引擎加速后的实播秒数）。
    4. 时长表：one-shot / loop 总时长，标出 > 4.5s。
    5. 派生一致性：expansion 的 reverse/slow/hold/pingpong 与其源动作帧序是否
       仍等于 derive_actions._order_indices 复算的结果（只重排、不重编码 → 用内容哈希比对）。
    6. 引擎实播：构造 PetEngine，逐个 one-shot play 后用模拟时间推进到 Idle，
       断言 <= 1.5× 标称时长，并记录"运动结束 → Idle"之间还剩多少秒静止。
    7. 回归：跑 pet/mimi_app/tests 全套 + scripts/verify/acceptance_expansion.py。

用法：
    /tmp/mimienv/bin/python scripts/verify/verify_trim.py --label BASELINE
    /tmp/mimienv/bin/python scripts/verify/verify_trim.py --label AFTER

    --label BASELINE|AFTER   写进报告的小节名（同小节会被整段替换）
    --out PATH               报告路径，默认 docs/expansion/TRIM-VERIFY.md
    --json                   额外把机器可读结果打到 stdout（不落盘）
    --no-tests               跳过第 7 项（子进程跑测试较慢）
    --quiet                  不打印正文，只打印摘要

退出码：0 = 结构/解码/派生/引擎/回归全部通过且验收门通过；1 = 有硬失败。
BASELINE 的"尾巴偏长"属于被观测量，本身不算失败（--label AFTER 才算验收门）。

只读素材与 manifest；除报告文件外不写任何路径。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "pet" / "mimi_app" / "src"
PIPELINE = REPO / "scripts" / "pipeline"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PIL import Image, ImageFilter  # noqa: E402

# 尾巴指标（主指标，见 task-4 / docs/expansion/TRIM-PROPOSAL.md 的口径）：
# RGBA → RGB 预乘 alpha/255 → BOX 缩到 80x120 → GaussianBlur(sigma=1.6) → float32，
# 帧差 mean(|desc[i+1]-desc[i]|)，再对 5 帧取中值平滑。
DESC_SIZE = (80, 120)
BLUR_SIGMA = 1.6
SMOOTH_WINDOW = 5
TAIL_FRACTION = 0.15

#: "尾巴已经静到不可感知"的峰值比例（spec：低于动作峰值的一定比例）。
RATIO_STILL = 0.05
#: 静止判断阈值：平滑后帧差 <= max(STILL_PEAK_FRAC × 峰值, STILL_FLOOR)。
#: 10% 是肉眼判定"已经不动了"的常用分界；3% / 5% 作为敏感性对照一起输出，
#: 因为尾巴是生成模型留下的"近静止微抖"而非复制帧，阈值定死会漏掉真尾巴。
STILL_PEAK_FRAC = 0.10
STILL_FLOOR = 0.02
SENSITIVITY = (0.03, 0.05, 0.10)
#: 引擎侧判定：运动结束后还在静止帧上停留超过这么久 = 会被读成"卡住"。
STILL_PLAY_TOLERANCE_S = 0.5
SOFT_PLAY_LIMIT_S = 0.25

#: 素材库基准尺寸（manifest 未声明 canvas 时的隐含值，也是"该目录应当多大"）。
LIBRARY_CANVAS = (512, 768)
EXPANSION_CANVAS = (320, 480)

#: 一次性动作超过这个总时长就要单独点名（task-4 第 4 项）。
LONG_ACTION_S = 4.5
#: 引擎实播的容忍上限：<= 1.5× 标称。
RUNTIME_FACTOR = 1.5

LIB_MANIFEST = REPO / "pet/assets/characters/mimi/library_v1/manifest.json"
EXP_MANIFEST = REPO / "pet/assets/characters/mimi/expansion_v1/actions.json"
EXP_ACTIONS = EXP_MANIFEST.parent / "actions"

# 外置盘单文件 open 约 70-80ms，串行解码 5000+ 帧要十几分钟，所以读盘并行。
WORKERS = int(os.environ.get("VERIFY_TRIM_WORKERS", "16"))


# --------------------------------------------------------------------------- 基础工具


def hhmmss() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def lib_frame_paths(entry: dict) -> list[Path]:
    """按 action_library._abs_path 的同一套词法规则解析帧路径。"""
    from mimi_pet.action_library import _abs_path

    base = LIB_MANIFEST.parent
    return [_abs_path(base, value) for value in entry["frames"]]


#: 每个帧文件只读一遍：解码和内容哈希共用这一次 read。
FILE_HASHES: dict[Path, str] = {}


def digest_of(path: Path) -> str:
    cached = FILE_HASHES.get(path)
    if cached:
        return cached
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        digest = ""
    FILE_HASHES[path] = digest
    return digest


def _descriptor(path: Path) -> tuple[tuple[int, int], np.ndarray]:
    data = path.read_bytes()
    FILE_HASHES[path] = hashlib.sha256(data).hexdigest()
    with Image.open(io.BytesIO(data)) as im:
        size = im.size
        rgba = np.asarray(im.convert("RGBA"), dtype=np.float32)
    rgb = rgba[..., :3] * (rgba[..., 3:4] / 255.0)
    small = Image.fromarray(np.clip(rgb, 0.0, 255.0).astype(np.uint8))
    small = small.resize(DESC_SIZE, Image.BOX).filter(ImageFilter.GaussianBlur(BLUR_SIGMA))
    return size, np.asarray(small, dtype=np.float32)


def median_smooth(values: np.ndarray, window: int = SMOOTH_WINDOW) -> np.ndarray:
    """中值平滑（边缘按 edge 复制补齐），窗长必须为奇数。"""
    if len(values) == 0:
        return values
    half = window // 2
    padded = np.pad(values, half, mode="edge")
    return np.array(
        [float(np.median(padded[i : i + window])) for i in range(len(values))],
        dtype=np.float32,
    )


@dataclass
class FrameScan:
    """一次动作的帧扫描结果：尺寸、描述子、解码错误（一次解码同时喂给检查 2 和 3）。"""

    sizes: dict[Path, tuple[int, int]] = field(default_factory=dict)
    errors: dict[Path, str] = field(default_factory=dict)
    descs: list[np.ndarray | None] = field(default_factory=list)


def scan_frames(paths: list[Path]) -> FrameScan:
    scan = FrameScan()
    todo = [p for p in paths if p.is_file()]
    results: dict[Path, tuple[tuple[int, int] | None, np.ndarray | None, str | None]] = {}

    def one(path: Path):
        try:
            size, desc = _descriptor(path)
            return path, (size, desc, None)
        except Exception as exc:  # 损坏/截断/格式不符都归到这里
            return path, (None, None, f"{type(exc).__name__}: {exc}")

    if todo:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for path, payload in pool.map(one, todo):
                results[path] = payload

    for path in paths:
        if path not in results:
            scan.errors[path] = "missing"
            scan.descs.append(None)
            continue
        size, desc, err = results[path]
        if err is not None:
            scan.errors[path] = err
            scan.descs.append(None)
            continue
        scan.sizes[path] = size  # type: ignore[assignment]
        scan.descs.append(desc)
    return scan


def tail_metrics(durations: list[int], descs: list[np.ndarray | None]) -> dict:
    """尾巴指标：最后 15% 的平均运动 ÷ 峰值运动 + 尾部静止段长度。"""
    n = len(descs)
    if n < 2 or any(d is None for d in descs):
        return {"ok": False, "n": n}
    diffs = np.array(
        [float(np.mean(np.abs(descs[i + 1] - descs[i]))) for i in range(n - 1)],
        dtype=np.float32,
    )
    smooth = median_smooth(diffs)
    peak = float(smooth.max()) if len(smooth) else 0.0
    tail_count = min(max(1, math.ceil(TAIL_FRACTION * n)), len(smooth))
    tail_mean = float(smooth[-tail_count:].mean()) if len(smooth) else 0.0
    ratio = tail_mean / peak if peak > 1e-9 else 0.0

    still_thr = max(STILL_PEAK_FRAC * peak, STILL_FLOOR)
    runs: dict[float, int] = {}
    for frac in SENSITIVITY:
        thr = max(frac * peak, STILL_FLOOR)
        run = 0
        for value in smooth[::-1]:
            if value <= thr:
                run += 1
            else:
                break
        runs[frac] = run
    still_frames = runs.get(STILL_PEAK_FRAC, max(runs.values()) if runs else 0)
    still_seconds = sum(durations[n - still_frames :]) / 1000.0 if still_frames else 0.0
    still_start = n - still_frames  # 尾部静止段的第一帧（0 基）；帧差 smooth[i] 描述 i→i+1

    return {
        "ok": True,
        "n": n,
        "diffs": diffs,
        "smooth": smooth,
        "peak": peak,
        "tail_count": tail_count,
        "tail_mean": tail_mean,
        "ratio": ratio,
        "still_thr": still_thr,
        "still_frames": still_frames,
        "still_seconds": still_seconds,
        "still_start": still_start,
        "keep_to": n - still_frames,  # 1 基：裁到这一帧为止，后面 still_frames 帧可删
        "runs": runs,
    }


# --------------------------------------------------------------------------- 第 1 项


def check_manifest_structure(lib_raw: dict, exp_raw: dict) -> dict:
    problems: list[str] = []
    warnings: list[str] = []
    entries: list[tuple[str, dict]] = []

    for entry in lib_raw.get("usable_actions", []):
        entries.append(("library_v1", entry))
    for entry in exp_raw.get("actions", []):
        entries.append(("expansion_v1", entry))

    seen: dict[str, str] = {}
    dupes: list[str] = []
    collisions: list[str] = []
    checked = 0
    required = ("id", "frames", "duration_ms", "loop", "trigger", "exit_state")

    for where, entry in entries:
        ident = str(entry.get("id", "<no-id>"))
        checked += 1
        for key in required:
            if key not in entry:
                problems.append(f"{where}/{ident}: 缺字段 {key}")
        frames = entry.get("frames") or []
        durations = entry.get("duration_ms") or []
        if not isinstance(frames, list) or not isinstance(durations, list):
            problems.append(f"{where}/{ident}: frames/duration_ms 不是数组")
            continue
        if not frames:
            problems.append(f"{where}/{ident}: frames 为空")
        if len(frames) != len(durations):
            problems.append(
                f"{where}/{ident}: len(frames)={len(frames)} != len(duration_ms)={len(durations)}"
            )
        bad = [d for d in durations if not isinstance(d, (int, float)) or d <= 0]
        if bad:
            problems.append(f"{where}/{ident}: duration_ms 有非正数 {bad[:3]}")
        canvas = entry.get("canvas")
        pivot = entry.get("ground_pivot")
        if not (isinstance(canvas, list) and len(canvas) == 2):
            warnings.append(f"{where}/{ident}: canvas 未声明或不是二元组 {canvas!r}")
        if not (isinstance(pivot, list) and len(pivot) == 2):
            warnings.append(f"{where}/{ident}: ground_pivot 未声明或不是二元组 {pivot!r}")
        if entry.get("status") not in (None, "usable"):
            warnings.append(f"{where}/{ident}: status={entry.get('status')!r}")
        if entry.get("enabled") is not True:
            warnings.append(f"{where}/{ident}: enabled != true（引擎会跳过）")
        if ident in seen:
            dupes.append(ident)
            if seen[ident] != where:
                collisions.append(f"{ident}: {seen[ident]} vs {where}")
            else:
                problems.append(f"{where}/{ident}: id 重复")
        else:
            seen[ident] = where

    declared = lib_raw.get("runtime_action_count")
    enabled_lib = sum(
        1
        for e in lib_raw.get("usable_actions", [])
        if e.get("status") == "usable" and e.get("enabled") is True
    )
    if declared is not None and int(declared) != enabled_lib:
        warnings.append(
            f"runtime_action_count={declared} 与实际可用条目 {enabled_lib} 不一致"
        )

    # 真正跑一遍 loader：manifest 坏了它会抛，等于结构检查的端到端确认。
    loader_ids: list[str] = []
    expansion_ids: list[str] = []
    loader_error = ""
    try:
        from mimi_pet.action_library import ActionLibrary

        library = ActionLibrary(LIB_MANIFEST)
        loader_ids = [spec.id for spec in library.all()]
        expansion_ids = list(library.loaded_expansion)
    except Exception as exc:
        loader_error = f"{type(exc).__name__}: {exc}"
        problems.append(f"ActionLibrary 构造失败：{loader_error}")

    expected_total = enabled_lib + sum(
        1 for e in exp_raw.get("actions", []) if e.get("status") == "usable" and e.get("enabled") is True
    )
    if loader_ids and len(loader_ids) != expected_total:
        warnings.append(
            f"loader 载入 {len(loader_ids)} 条，manifest 声明可用 {expected_total} 条（可能有 id 冲突被跳过）"
        )

    return {
        "checked": checked,
        "problems": problems,
        "warnings": warnings,
        "dupes": dupes,
        "collisions": collisions,
        "loader_total": len(loader_ids),
        "loader_expansion": len(expansion_ids),
        "loader_error": loader_error,
        "ok": not problems,
    }


# --------------------------------------------------------------------------- 第 2、3 项


def check_frames_and_tail(entries: list[tuple[str, dict]], scans: dict[str, FrameScan]) -> dict:
    missing: list[str] = []
    undecodable: list[str] = []
    size_warn: list[str] = []
    size_info: list[str] = []
    frames_total = 0
    frames_ok = 0

    for where, entry in entries:
        ident = str(entry["id"])
        paths = lib_frame_paths(entry)
        expected = LIBRARY_CANVAS if where == "library_v1" else EXPANSION_CANVAS
        canvas = entry.get("canvas") or list(expected)
        canvas = (int(canvas[0]), int(canvas[1]))
        scan = scans[ident]
        bad_sizes: list[str] = []
        for path in paths:
            frames_total += 1
            if path not in scan.sizes and path not in scan.errors:
                missing.append(f"{ident}: {path}")
                continue
            if path in scan.errors:
                reason = scan.errors[path]
                if reason == "missing":
                    missing.append(f"{ident}: {path.relative_to(REPO)}")
                else:
                    undecodable.append(f"{ident}: {path.name} {reason}")
                continue
            frames_ok += 1
            size = scan.sizes[path]
            if tuple(size) != expected:
                bad_sizes.append(f"{path.name}={size[0]}x{size[1]}")
            if tuple(size) != canvas:
                size_info.append(
                    f"{ident}: 声明 canvas {canvas[0]}x{canvas[1]}，实测 {size[0]}x{size[1]}"
                )
        if bad_sizes:
            size_warn.append(
                f"{ident}: {len(bad_sizes)} 帧不是 {expected[0]}x{expected[1]}（例：{bad_sizes[0]}）"
            )

    return {
        "frames_total": frames_total,
        "frames_ok": frames_ok,
        "missing": missing,
        "undecodable": undecodable,
        "size_warn": size_warn,
        "size_info": size_info,
        "canvas_mismatch": len(size_info),
        "ok": not missing and not undecodable and not size_warn,
    }


# --------------------------------------------------------------------------- 第 4 项


def build_rows(entries, scans, metrics, engine_times) -> list[dict]:
    rows = []
    for where, entry in entries:
        ident = str(entry["id"])
        durations = list(entry["duration_ms"])
        nominal = sum(durations) / 1000.0
        play = min(nominal, 3000.0 / 1000.0) if not entry.get("loop") else nominal
        speed = (nominal / 3.0) if (not entry.get("loop") and nominal > 3.0) else 1.0
        metric = metrics[ident]
        runtime = engine_times.get(ident, {})
        row = {
            "id": ident,
            "where": where,
            "n": len(entry["frames"]),
            "durations": durations,
            "nominal_s": nominal,
            "engine_expected_s": play,
            "speed": speed,
            "loop": bool(entry.get("loop")),
            "source": entry.get("source", ""),
            "canvas": entry.get("canvas"),
            "engine_play_s": runtime.get("play_s"),
            "engine_status": runtime.get("status", ""),
            "idle_after_motion_s": runtime.get("idle_after_motion_s"),
        }
        if metric.get("ok"):
            still_s = float(metric["still_seconds"])
            row.update(
                {
                    "peak": float(metric["peak"]),
                    "tail_mean": float(metric["tail_mean"]),
                    "ratio": float(metric["ratio"]),
                    "still_frames": int(metric["still_frames"]),
                    "still_s": still_s,
                    "still_play_s": still_s / speed if speed else still_s,
                    "still_start": int(metric["still_start"]),
                    "keep_to": int(metric["keep_to"]),
                    "runs": metric["runs"],
                }
            )
        rows.append(row)
    return rows


def derive_mode(ident: str) -> str:
    """从 derive_actions.DERIVATIONS 里取派生模式（取不到就返回空串）。"""
    try:
        if str(PIPELINE) not in sys.path:
            sys.path.insert(0, str(PIPELINE))
        from derive_actions import DERIVATIONS

        return str(DERIVATIONS.get(ident, {}).get("mode", ""))
    except Exception:
        return ""


def classify(row: dict) -> str:
    if row.get("ratio") is None:
        return "UNKNOWN"
    if row["loop"]:
        return "INTENTIONAL(loop)"
    mode = derive_mode(row["id"])
    if mode in ("hold", "pingpong"):
        return f"INTENTIONAL({mode})"
    still_play = row.get("still_play_s") or 0.0
    if still_play > STILL_PLAY_TOLERANCE_S:
        return "FROZEN"
    if still_play > SOFT_PLAY_LIMIT_S:
        return "SOFT"
    return "OK"


# --------------------------------------------------------------------------- 第 5 项


def _structure_ratio(mode: str, hashes: list[str], n_source: int) -> tuple[float | None, str]:
    """不看源文件、只看派生帧自身的序列，验证它是某个源动作的合法 mode 重排。

    返回 (匹配率, 说明)：1.0 = 结构完全成立；<1 说明这条派生是分批拼出来的旧字节
    （源动作被重新渲染过，第二次构建只覆盖了一部分）；None = 该 mode 没有内部不变量。
    """
    if not hashes or any(not h for h in hashes):
        return None, "有帧读不到内容，无法结构自证"
    n = n_source
    if mode == "slow":
        if len(hashes) != 2 * n:
            return 0.0, f"slow 应为 {2 * n} 帧，实得 {len(hashes)}"
        matched = sum(1 for i in range(n) if hashes[2 * i] == hashes[2 * i + 1])
        return matched / n, f"成对相同的帧对 {matched}/{n}"
    if mode == "hold":
        if len(hashes) != n + 8:
            return 0.0, f"hold 应为 {n + 8} 帧，实得 {len(hashes)}"
        head = sum(1 for h in hashes[:4] if h == hashes[4]) / 4
        tail = sum(1 for h in hashes[-4:] if h == hashes[n + 3]) / 4
        return (head + tail) / 2, f"首段 {head:.2f} / 尾段 {tail:.2f} 与源首尾一致"
    if mode == "pingpong":
        if len(hashes) != 2 * n - 2:
            return 0.0, f"pingpong 应为 {2 * n - 2} 帧，实得 {len(hashes)}"
        pairs = n - 2
        matched = sum(1 for k in range(pairs) if hashes[n + k] == hashes[n - 2 - k])
        return matched / pairs if pairs else None, f"后半段镜相对称 {matched}/{pairs} 对"
    if mode == "reverse":
        if len(hashes) != n:
            return 0.0, f"reverse 应为 {n} 帧，实得 {len(hashes)}"
        return None, "reverse 没有内部不变量，只能靠源哈希定序"
    return None, f"未知 mode {mode}"


def check_derived(exp_raw: dict, scans: dict[str, FrameScan] | None = None) -> dict:
    """派生素材必须仍是"源帧的重排"，且重排顺序等于 _order_indices 的复算结果。

    两路证据：
      * **溯源**：派生目录若是源帧的字节副本（derive_actions 只 copyfile、
        不重编码），逐帧 SHA-256 能映射回源帧下标 → 与期望序列逐位比对。
      * **结构自证**：slow/hold/pingpong 的重排本身有内部不变量（成对相同、
        首尾各停 4 帧、后半镜相对称），即使源动作被重新渲染过也能独立验证。
    两路不一致时如实分开报告，不把"源动作变了"说成"重排错了"。
    """
    try:
        if str(PIPELINE) not in sys.path:
            sys.path.insert(0, str(PIPELINE))
        from derive_actions import DERIVATIONS, _order_indices
    except Exception as exc:
        return {"rows": [], "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    by_id = {str(e["id"]): e for e in exp_raw.get("actions", [])}
    rows: list[dict] = []

    for ident, spec in sorted(DERIVATIONS.items()):
        mode = str(spec["mode"])
        entry = by_id.get(ident)
        if entry is None:
            rows.append(
                {
                    "id": ident,
                    "mode": mode,
                    "source": spec["source"],
                    "source_n": None,
                    "derived_n": None,
                    "expected_n": None,
                    "order_ok": None,
                    "structure_ok": None,
                    "matched": None,
                    "verdict": "MISSING",
                    "detail": "派生条目不在 actions.json 里",
                }
            )
            continue

        source_dir = EXP_ACTIONS / str(spec["source"]) / "frames"
        source_files = sorted(source_dir.glob("*.webp"))
        derived_paths = lib_frame_paths(entry)
        derived_hashes = [digest_of(p) for p in derived_paths]
        expected = _order_indices(len(source_files), mode)

        structure_ratio, structure_note = _structure_ratio(mode, derived_hashes, len(source_files))

        # 视觉溯源：描述子最近邻 → 源帧下标（字节全变时也能验顺序）
        desc_order: list[int | None] = []
        desc_ok: bool | None = None
        desc_note = ""
        if scans is not None:
            src_desc = scans.get(str(spec["source"]))
            dst_desc = scans.get(ident)
            src_arr = [d for d in (src_desc.descs if src_desc else []) if d is not None]
            dst_arr = [d for d in (dst_desc.descs if dst_desc else []) if d is not None]
            if src_arr and len(dst_arr) == len(derived_paths) and len(src_arr) == len(source_files):
                stack = np.stack(src_arr)
                for dd in dst_arr:
                    dist = np.abs(stack - dd).mean(axis=(1, 2, 3))
                    desc_order.append(int(dist.argmin()))
                if len(desc_order) == len(expected):
                    desc_ok = desc_order == expected
                    if not desc_ok:
                        first = next(
                            i for i, (a, b) in enumerate(zip(desc_order, expected)) if a != b
                        )
                        desc_note = (
                            f"视觉最近邻在第 {first} 帧起偏离：实得 {desc_order[first]}，"
                            f"应为 {expected[first]}"
                        )
                    else:
                        desc_note = "视觉最近邻序列与期望完全一致"
                else:
                    desc_note = f"视觉最近邻只解出 {len(desc_order)}/{len(expected)} 帧"
            else:
                desc_note = "描述子不可用（源或派生有帧解码失败）"

        order_ok: bool | None = None
        matched: int | None = None
        detail = structure_note
        if source_files and len(derived_paths) == len(expected):
            hash_to_index: dict[str, int] = {}
            ambiguous: set[str] = set()
            for index, path in enumerate(source_files):
                digest = digest_of(path)
                if not digest:
                    continue
                if digest in hash_to_index:
                    ambiguous.add(digest)
                else:
                    hash_to_index[digest] = index
            actual: list[int | None] = []
            for digest in derived_hashes:
                if not digest or digest in ambiguous:
                    actual.append(None)
                    continue
                index = hash_to_index.get(digest)
                actual.append(index)
            matched = sum(1 for a in actual if a is not None)
            if matched == len(derived_hashes):
                order_ok = actual == expected
                if not order_ok:
                    first = next(i for i, (a, b) in enumerate(zip(actual, expected)) if a != b)
                    detail = f"第 {first} 帧起下标不一致：实得 {actual[first]}，应为 {expected[first]}"
            else:
                detail = (
                    f"只有 {matched}/{len(derived_hashes)} 帧能在当前源动作里找到同样字节，"
                    "无法按源哈希定序"
                )
        elif source_files:
            order_ok = False
            detail = (
                f"派生 {len(derived_paths)} 帧；{len(source_files)} 源帧 + {mode} 应为 {len(expected)} 帧"
            )

        if order_ok is True:
            verdict = "OK"
        elif order_ok is False:
            verdict = "MISMATCH"  # 字节全对上、但顺序不是 _order_indices 的结果 → 真错序
        elif mode == "reverse" and desc_ok is False:
            verdict = "MISMATCH"  # reverse 的视觉顺序也明确错了 → 真错序
        elif mode == "reverse" and desc_ok is True:
            verdict = "OK_VISUAL"  # reverse：字节变了、但视觉顺序逐帧对上
        elif structure_ratio is not None and structure_ratio > 0:
            # 结构（部分）成立，但字节对不上当前源：源动作被重新渲染过 / 派生是分批拼的旧字节。
            verdict = "STALE"
        elif structure_ratio == 0.0:
            verdict = "MISMATCH"
        else:
            verdict = "UNKNOWN"

        rows.append(
            {
                "id": ident,
                "mode": mode,
                "source": spec["source"],
                "source_n": len(source_files),
                "derived_n": len(derived_paths),
                "expected_n": len(expected),
                "order_ok": order_ok,
                "structure_ratio": structure_ratio,
                "desc_ok": desc_ok,
                "desc_note": desc_note,
                "structure_ok": None if structure_ratio is None else structure_ratio >= 0.999,
                "matched": matched,
                "verdict": verdict,
                "detail": detail,
            }
        )

    counts = {
        v: sum(1 for r in rows if r["verdict"] == v)
        for v in ("OK", "OK_VISUAL", "STALE", "MISMATCH", "MISSING", "UNKNOWN")
    }
    hard = [r for r in rows if r["verdict"] in ("MISMATCH", "MISSING")]
    return {"rows": rows, "ok": not hard, "error": "", "counts": counts}


# --------------------------------------------------------------------------- 第 6 项


def check_engine(library, rows_by_id: dict[str, dict]) -> dict:
    """用模拟时间推进引擎，不 sleep：每个动作 play 后推进到 Idle 并记录实播秒数。

    offscreen Qt 下 QTest/sendEvent 会段错误，所以这里完全不碰 Qt：直接构造
    领域层 PetEngine（不含任何 PySide6 引用），按 test_playback.py 的做法自己
    喂 dt 推进时间。
    """
    from mimi_pet.config import load_config
    from mimi_pet.engine import PetEngine
    from mimi_pet.state_machine import Event, PetState

    config = load_config()
    engine = PetEngine(library, config)
    dt = 1.0 / 60.0
    results: dict[str, dict] = {}
    failures: list[str] = []
    warnings: list[str] = []

    # sit_down 正常结束会切到 sit_idle 长驻（不是卡死），所以它settle 到
    # 循环体里就算通过；其余 one-shot 必须回到 IDLE。
    follow_ups = {"sit_down": "sit_idle"}

    def reset() -> None:
        # 只用公开路径收尾：停播放器 + 派发完成事件，让状态机回到 Idle。
        engine.player.stop()
        if engine.states.state is not PetState.IDLE:
            engine.states.dispatch(Event.ACTION_FINISHED)
        engine.player.stop()
        engine.stop_walk()  # 循环移动动作（walk_left/right）靠它清掉位移目标

    def settle(limit_s: float, still_start: int | None, expect_loop_id: str | None):
        now = 0.0
        t_at_still: float | None = 0.0 if still_start is not None and still_start <= 0 else None
        last_index = -1
        while now < limit_s:
            now += dt
            frame = engine.tick(now, dt)
            if frame.sprite is not None:
                last_index = max(last_index, frame.sprite.index)
                if (
                    t_at_still is None
                    and still_start is not None
                    and frame.sprite.index >= still_start
                ):
                    t_at_still = now
            action = engine.player.action
            if expect_loop_id is not None:
                if action is not None and action.id == expect_loop_id:
                    return now, t_at_still, last_index, "SETTLE_INTO_LOOP"
                continue
            if engine.states.state is PetState.IDLE and action is None:
                return now, t_at_still, last_index, "IDLE"
        return None, t_at_still, last_index, "NO_FINISH_IN_TIME"

    for spec in sorted(library.all(), key=lambda s: s.id):
        ident = spec.id
        row = rows_by_id.get(ident)
        if row is None:
            continue
        nominal = row["nominal_s"]
        capped = min(nominal, 3.0)  # FramePlayer.MAX_ONESHOT_MS 的加速后会落在 3.0s
        reset()

        if spec.loop:
            # 循环动作不该自己结束：推进 2 个循环（封顶 20s）看它是否报 finished。
            limit = max(2.0, min(2.0 * capped + 1.0, 20.0))
            if not engine.force_perform(ident):
                failures.append(f"{ident}: force_perform 被拒绝")
                results[ident] = {"status": "PLAY_REFUSED", "play_s": None}
                reset()
                continue
            finished_at: float | None = None
            now = 0.0
            while now < limit:
                now += dt
                frame = engine.tick(now, dt)
                if frame.sprite is not None and frame.sprite.finished:
                    finished_at = now
                    break
            if finished_at is None:
                results[ident] = {
                    "status": "LOOP_NO_FINISH_OK",
                    "play_s": None,
                    "idle_after_motion_s": None,
                }
            else:
                failures.append(f"{ident}: 循环动作在 {finished_at:.2f}s 报 finished")
                results[ident] = {
                    "status": "LOOP_FINISHED_BADLY",
                    "play_s": finished_at,
                    "idle_after_motion_s": None,
                }
            reset()
            continue

        follow = follow_ups.get(ident)
        limit = max(2.0, RUNTIME_FACTOR * capped + (2.0 if follow else 1.0))
        if not engine.force_perform(ident):
            failures.append(f"{ident}: force_perform 被拒绝")
            results[ident] = {"status": "PLAY_REFUSED", "play_s": None}
            reset()
            continue
        play_s, t_at_still, last_index, status = settle(limit, row.get("still_start"), follow)
        if play_s is None:
            failures.append(f"{ident}: {limit:.1f}s 内没有回到 Idle / 未接上 {follow}")
            results[ident] = {
                "status": "NO_FINISH_IN_TIME",
                "play_s": None,
                "idle_after_motion_s": None,
                "last_index": last_index,
            }
            reset()
            continue
        budget = RUNTIME_FACTOR * capped + 0.05
        if status == "IDLE" and play_s > budget:
            failures.append(
                f"{ident}: 实播 {play_s:.2f}s 超过 {RUNTIME_FACTOR}× 标称上限 {budget:.2f}s"
            )
        idle_after = None
        if row.get("still_start") is not None and t_at_still is not None:
            idle_after = max(0.0, play_s - t_at_still)
        results[ident] = {
            "status": status,
            "play_s": play_s,
            "idle_after_motion_s": idle_after,
            "last_index": last_index,
            "final_action": engine.player.action.id if engine.player.action else None,
        }
        reset()

    # 残留状态自检：一轮跑完必须回到干净的 Idle，否则就是"播完卡住"的真身。
    if engine.states.state is not PetState.IDLE:
        failures.append(f"跑完后引擎停在 {engine.states.state}，没有回到 IDLE")
    if engine.player.action is not None:
        warnings.append(f"跑完后 player 仍持有 {engine.player.action.id}")

    return {
        "results": results,
        "failures": failures,
        "warnings": warnings,
        "ok": not failures,
    }


# --------------------------------------------------------------------------- 第 7 项


def run_command(cmd: list[str], cwd: Path, timeout: float = 900.0) -> dict:
    env = dict(os.environ)
    env["MIMI_DEBUG_LOG"] = "0"
    env.pop("MIMI_ASSET_ROOT", None)
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        code = -1
    return {
        "cmd": " ".join(cmd),
        "cwd": str(cwd),
        "exit": code,
        "seconds": time.time() - started,
        "stdout": stdout,
        "stderr": stderr,
    }


def _parse_failures(output: str) -> list[str]:
    """从 unittest 输出里挑出失败的用例名。"""
    names = re.findall(r"^(?:ERROR|FAIL): (\S+) \(([\w.]+)\)", output, re.M)
    return [f"{cls}.{name}" for name, cls in names]


def check_regressions(python: str, attempts: int = 2) -> dict:
    """跑两套回归。unittest 带墙钟节流的用例会间歇失败，所以失败时重跑一次并
    如实记录每次的耗时与失败用例——把 flaky 和真失败分开报，不掩盖。"""
    runs: list[dict] = []
    for _ in range(attempts):
        run = run_command(
            [python, "-m", "unittest", "discover", "-s", "mimi_app/tests"],
            cwd=REPO / "pet",
        )
        output = run["stdout"] + run["stderr"]
        ran = re.search(r"Ran (\d+) tests?", output)
        runs.append(
            {
                "exit": run["exit"],
                "count": int(ran.group(1)) if ran else None,
                "seconds": run["seconds"],
                "failed": _parse_failures(output),
                "tail": "\n".join(output.strip().splitlines()[-4:]),
            }
        )
        if run["exit"] == 0:
            break

    last = runs[-1]
    tests_ok = last["exit"] == 0
    flaky = tests_ok and any(r["exit"] != 0 for r in runs[:-1])

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    accept = run_command([python, "scripts/verify/acceptance_expansion.py"], cwd=REPO)
    accept_out = accept["stdout"] + accept["stderr"]
    total = re.search(r"总计 (\d+) 项，失败 (\d+)", accept_out)
    accept_total = int(total.group(1)) if total else None
    accept_failed = int(total.group(2)) if total else None
    accept_ok = accept["exit"] == 0

    return {
        "tests": {
            "exit": last["exit"],
            "count": last["count"],
            "ok": tests_ok,
            "seconds": last["seconds"],
            "attempts": runs,
            "flaky": flaky,
            "failed": last["failed"],
            "summary": "\n".join(last["tail"]),
        },
        "acceptance": {
            "exit": accept["exit"],
            "total": accept_total,
            "failed": accept_failed,
            "ok": accept_ok,
            "seconds": accept["seconds"],
            "summary": "\n".join((accept_out.strip().splitlines() or [""])[-14:]),
        },
        "ok": tests_ok and accept_ok,
    }


# --------------------------------------------------------------------------- 报告


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def fmt(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_section(label: str, data: dict) -> str:
    rows: list[dict] = data["rows"]
    oneshot = [r for r in rows if not r["loop"]]
    loops = [r for r in rows if r["loop"]]
    with_ratio = [r for r in rows if r.get("ratio") is not None]
    long_actions = [r for r in oneshot if r["nominal_s"] > LONG_ACTION_S]
    frozen = [r for r in oneshot if classify(r) == "FROZEN"]
    soft = [r for r in oneshot if classify(r) == "SOFT"]
    ratio_still = [r for r in with_ratio if r["ratio"] <= RATIO_STILL]
    intentional = [r for r in oneshot if classify(r).startswith("INTENTIONAL")]

    lines: list[str] = []
    lines.append(f"## {label}")
    lines.append("")
    if data.get("inflight"):
        lines.append(INFLIGHT_SENTINEL)
        lines.append(
            "> ⚠ **中间状态快照**：manifest 声明的帧数与磁盘实际文件不符"
            f"（{data['frames']['frames_total'] - data['frames']['frames_ok']} 个槽位读不到文件）。"
            "这说明裁剪/重生成**正在进行**，本节的数字不是一份可比的完整快照，"
            "不能用来代替 BASELINE 或宣告 AFTER 通过。"
        )
        lines.append("")
    lines.append(
        f"生成：{data['generated']}　·　解释器 `{data['python']}`　·　"
        f"素材 `{data['asset_root']}`　·　动作 {len(rows)} 条"
        f"（one-shot {len(oneshot)} / loop {len(loops)}）"
    )
    lines.append("")
    lines.append("### 结果总览")
    lines.append("")
    checks = [
        ["1", "manifest 结构", "PASS" if data["structure"]["ok"] else "FAIL",
         f"条目 {data['structure']['checked']}；loader 载入 {data['structure']['loader_total']}"
         f"（expansion {data['structure']['loader_expansion']}）；问题 {len(data['structure']['problems'])}"],
        ["2", "帧文件", "PASS" if data["frames"]["ok"] else "FAIL",
         f"帧 {data['frames']['frames_total']}，可解码 {data['frames']['frames_ok']}，"
         f"缺失 {len(data['frames']['missing'])}，损坏 {len(data['frames']['undecodable'])}，"
         f"尺寸警告 {len(data['frames']['size_warn'])}，canvas 不符 {data['frames']['canvas_mismatch']}"],
        ["3", "尾巴检查", "PASS" if not frozen else "FINDINGS",
         f"ratio≤{RATIO_STILL:.0%}（尾巴最静）{len(ratio_still)} 条；静止实播 >{STILL_PLAY_TOLERANCE_S}s {len(frozen)} 条；"
         f"{SOFT_PLAY_LIMIT_S}–{STILL_PLAY_TOLERANCE_S}s {len(soft)} 条；刻意 hold/loop {len(intentional)} 条"],
        ["4", "时长表", "PASS" if not long_actions else "FINDINGS",
         f"one-shot 最长 {max((r['nominal_s'] for r in oneshot), default=0):.2f}s；"
         f">{LONG_ACTION_S}s 共 {len(long_actions)} 条；loop 最长 {max((r['nominal_s'] for r in loops), default=0):.2f}s"],
        ["5", "派生一致性", "PASS" if data["derived"]["ok"] else "FAIL",
         f"派生条目 {len(data['derived']['rows'])}；"
         + "、".join(
             f"{k} {data['derived'].get('counts', {}).get(k, 0)}"
             for k in ("OK", "OK_VISUAL", "STALE", "MISMATCH", "MISSING", "UNKNOWN")
             if data['derived'].get('counts', {}).get(k, 0)
         )],
        ["6", "引擎实播", "PASS" if data["engine"]["ok"] else "FAIL",
         f"one-shot 全部回到 Idle；失败 {len(data['engine']['failures'])}；"
         f"运动结束后仍静止最久 {fmt(data['engine_worst'])}s"],
        ["7", "回归", "PASS" if data["regressions"]["ok"] else "FAIL",
         f"unittest {data['regressions']['tests']['count']} 项 "
         f"({'OK' if data['regressions']['tests']['ok'] else 'FAILED'}"
         + (f"，重跑 {len(data['regressions']['tests']['attempts'])} 次" if len(data['regressions']['tests']['attempts']) > 1 else "")
         + ")；"
         f"acceptance_expansion 失败 {data['regressions']['acceptance']['failed']}"
         f"/{data['regressions']['acceptance']['total']}"],
    ]
    lines.append(table(["#", "检查项", "结论", "数字"], checks))
    lines.append("")
    lines.append(
        f"**验收门（{label}）**：{'PASS' if data['gate_ok'] else 'FAIL'}　"
        f"→ 硬失败 {len(data['hard_failures'])} 条"
        + ("" if label != "AFTER" else f"；尾巴 FROZEN {len(frozen)} 条（需为 0）")
    )
    if data["hard_failures"]:
        lines.append("")
        for item in data["hard_failures"]:
            lines.append(f"- ✗ {item}")
    if data.get("soft_findings"):
        lines.append("")
        lines.append(f"软发现 {len(data['soft_findings'])} 条（不阻塞验收，但必须是知情选择）：")
        lines.append("")
        for item in data["soft_findings"]:
            lines.append(f"- ⚠ {item}")
    lines.append("")

    # ---- 1
    lines.append("### 1. manifest 结构")
    lines.append("")
    st = data["structure"]
    lines.append(
        f"- 条目 {st['checked']} 条；`len(frames) != len(duration_ms)` 问题 {len(st['problems'])} 条；"
        f"重复 id {len(st['dupes'])} 条；跨库冲突 {len(st['collisions'])} 条。"
    )
    lines.append(
        f"- `ActionLibrary` 载入 {st['loader_total']} 条（其中 expansion {st['loader_expansion']} 条）。"
    )
    lines.append(f"- 警告 {len(st['warnings'])} 条；问题 {len(st['problems'])} 条。")
    if st["problems"] or st["warnings"]:
        lines.append("")
        for item in (st["problems"] + st["warnings"])[:20]:
            lines.append(f"  - {item}")
    lines.append("")

    # ---- 2
    lines.append("### 2. 帧文件")
    lines.append("")
    fr = data["frames"]
    lines.append(
        f"- 共 {fr['frames_total']} 帧，可解码 {fr['frames_ok']}；"
        f"缺失 {len(fr['missing'])}、损坏 {len(fr['undecodable'])}、"
        f"目录基准尺寸不符 {len(fr['size_warn'])}、声明 canvas 与实测不符 {fr['canvas_mismatch']}。"
    )
    lines.append(
        f"- 基准：library_v1 = {LIBRARY_CANVAS[0]}×{LIBRARY_CANVAS[1]}，"
        f"expansion_v1 = {EXPANSION_CANVAS[0]}×{EXPANSION_CANVAS[1]}。"
    )
    if fr["missing"] or fr["undecodable"] or fr["size_warn"] or fr["size_info"]:
        lines.append("")
        for item in (fr["missing"] + fr["undecodable"] + fr["size_warn"] + fr["size_info"])[:20]:
            lines.append(f"  - {item}")
    lines.append("")

    # ---- 3
    lines.append("### 3. 尾巴检查（核心）")
    lines.append("")
    lines.append(
        "口径：`ratio` = 最后 15% 帧的平均运动 ÷ 动作峰值运动（RGBA→预乘→80×120 BOX→"
        f"GaussianBlur σ={BLUR_SIGMA}→float32，帧差 mean|Δ|，5 帧中值平滑）。"
        f"**ratio 越低 = 尾巴越静**（并非「越健康」之意）；"
        f"`静止帧` 是尾部连续满足 `平滑帧差 ≤ max({STILL_PEAK_FRAC:.0%}×峰值, {STILL_FLOOR})` 的帧数，"
        "`静止秒` 是这些帧的标称时长，`静止实播秒` 再除以引擎加速比（`MAX_ONESHOT_MS=3000`）。"
    )
    lines.append("")
    lines.append(
        f"判定：`FROZEN` = 静止实播秒 > {STILL_PLAY_TOLERANCE_S}s（会被读成「卡住」）；"
        f"`SOFT` = {SOFT_PLAY_LIMIT_S}–{STILL_PLAY_TOLERANCE_S}s；"
        "`OK` = 不超过；`INTENTIONAL` = loop 或 hold/pingpong 派生（尾帧是有意保留的停顿）。"
    )
    lines.append("")
    lines.append(
        table(
            ["id", "库", "N", "总秒", "ratio", "峰值", "tail15", "静止帧", "保留到", "静止秒", "实播秒", "静止实播秒", "判定"],
            [
                [
                    r["id"],
                    "lib" if r["where"] == "library_v1" else "exp",
                    r["n"],
                    fmt(r["nominal_s"]),
                    fmt(r.get("ratio"), 3),
                    fmt(r.get("peak"), 3),
                    fmt(r.get("tail_mean"), 3),
                    fmt(r.get("still_frames")),
                    fmt(r.get("keep_to")),
                    fmt(r.get("still_s")),
                    fmt(r.get("engine_play_s")),
                    fmt(r.get("still_play_s")),
                    classify(r),
                ]
                for r in sorted(
                    [r for r in oneshot if r.get("ratio") is not None],
                    key=lambda r: (-(r.get("still_play_s") or 0.0), r.get("ratio") or 0.0),
                )
            ],
        )
    )
    lines.append("")
    sens = data["sensitivity"]
    lines.append(
        "灵敏度对照（尾部静止帧数，看结论对阈值有多敏感）："
        + "；".join(
            f"≤{frac:.0%}×峰值 → {sens[str(frac)]['actions']} 条有尾巴、"
            f"合计 {sens[str(frac)]['frames']} 帧 / {sens[str(frac)]['seconds']:.2f}s、"
            f"其中实播 > {STILL_PLAY_TOLERANCE_S}s 的 {sens[str(frac)]['over']} 条"
            for frac in SENSITIVITY
        )
        + "。"
    )
    lines.append("")
    lines.append(f"**ratio 最高的 8 条**（尾部仍有明显运动，说明末尾没有被静止帧吃掉）：")
    lines.append("")
    lines.append(
        table(
            ["id", "N", "总秒", "ratio", "静止秒", "实播秒"],
            [
                [r["id"], r["n"], fmt(r["nominal_s"]), fmt(r["ratio"], 3), fmt(r.get("still_s")), fmt(r.get("engine_play_s"))]
                for r in sorted(with_ratio, key=lambda r: -(r["ratio"] or 0.0))[:8]
            ],
        )
    )
    lines.append("")
    lines.append(f"**ratio 最低的 8 条**（尾巴几乎不动 = 正在被读成「卡住」的动作）：")
    lines.append("")
    lines.append(
        table(
            ["id", "N", "总秒", "ratio", "静止帧", "保留到", "静止秒", "静止实播秒", "判定"],
            [
                [
                    r["id"],
                    r["n"],
                    fmt(r["nominal_s"]),
                    fmt(r["ratio"], 3),
                    fmt(r.get("still_frames")),
                    fmt(r.get("keep_to")),
                    fmt(r.get("still_s")),
                    fmt(r.get("still_play_s")),
                    classify(r),
                ]
                for r in sorted(with_ratio, key=lambda r: (r["ratio"] or 0.0))[:8]
            ],
        )
    )
    lines.append("")

    # ---- 4
    lines.append("### 4. 时长表")
    lines.append("")
    lines.append(
        f"- one-shot {len(oneshot)} 条，合计 {sum(r['nominal_s'] for r in oneshot):.2f}s；"
        f"loop {len(loops)} 条，合计 {sum(r['nominal_s'] for r in loops):.2f}s。"
    )
    lines.append(
        f"- 引擎对 > 3.0s 的 one-shot 做整体加速（`MAX_ONESHOT_MS`），"
        f"所以 {len([r for r in oneshot if r['nominal_s'] > 3.0])} 条长动作的实播时长被压到 3.00s。"
    )
    lines.append(f"- **> {LONG_ACTION_S}s 的 one-shot {len(long_actions)} 条**：")
    lines.append("")
    lines.append(
        table(
            ["id", "库", "N", "总秒", "实播秒（引擎）", "ratio", "静止实播秒", "判定"],
            [
                [
                    r["id"],
                    "lib" if r["where"] == "library_v1" else "exp",
                    r["n"],
                    fmt(r["nominal_s"]),
                    fmt(r.get("engine_play_s")),
                    fmt(r.get("ratio"), 3),
                    fmt(r.get("still_play_s")),
                    classify(r),
                ]
                for r in sorted(long_actions, key=lambda r: -r["nominal_s"])
            ],
        )
    )
    lines.append("")
    if loops:
        lines.append("loop 动作（永不出 finished，靠外部 stop）：")
        lines.append("")
        lines.append(
            table(
                ["id", "库", "N", "总秒"],
                [
                    [r["id"], "lib" if r["where"] == "library_v1" else "exp", r["n"], fmt(r["nominal_s"])]
                    for r in sorted(loops, key=lambda r: -r["nominal_s"])
                ],
            )
        )
        lines.append("")

    # ---- 5
    lines.append("### 5. 派生一致性")
    lines.append("")
    dv = data["derived"]
    if dv.get("error"):
        lines.append(f"- 无法加载 `derive_actions`：{dv['error']}")
    else:
        lines.append(
            "复算方式：`derive_actions._order_indices(源帧数, mode)` 得到期望下标序列。两路证据分开报："
            "**溯源**（逐帧 SHA-256 能否映射回当前源帧下标并逐位对上期望序列）、"
            "**结构自证**（slow 成对相同 / hold 首尾各停 4 帧 / pingpong 后半镜相对称——"
            "即使源动作被重新渲染也能独立验证）。"
        )
        lines.append("")
        counts = dv.get("counts", {})
        lines.append(
            "结论分布：OK {OK} 条（字节与当前源一致且顺序逐位对上）、"
            "OK_VISUAL {OK_VISUAL} 条（字节已漂移，但视觉最近邻顺序逐帧对上，仅对 reverse 成立）、"
            "STALE {STALE} 条（重排结构成立，但字节对不上当前源、且该 mode 无视觉定序证据）、"
            "MISMATCH {MISMATCH} 条、MISSING {MISSING} 条、UNKNOWN {UNKNOWN} 条。".format(
                **{
                    k: counts.get(k, 0)
                    for k in ("OK", "OK_VISUAL", "STALE", "MISMATCH", "MISSING", "UNKNOWN")
                }
            )
        )
        lines.append("")
        lines.append(
            table(
                ["派生 id", "mode", "源动作", "源帧", "派生帧", "期望帧", "源哈希命中", "字节顺序", "视觉顺序", "结构自证率", "结论", "说明"],
                [
                    [
                        r["id"],
                        r["mode"],
                        r["source"],
                        fmt(r["source_n"]),
                        fmt(r["derived_n"]),
                        fmt(r["expected_n"]),
                        f"{r['matched']}/{r['derived_n']}" if r.get("matched") is not None else "—",
                        "—" if r["order_ok"] is None else ("是" if r["order_ok"] else "否"),
                        (
                            "—"
                            if r["mode"] != "reverse" or r.get("desc_ok") is None
                            else ("是" if r["desc_ok"] else "否")
                        ),
                        "—" if r.get("structure_ratio") is None else f"{r['structure_ratio']:.0%}",
                        r["verdict"],
                        r["detail"],
                    ]
                    for r in dv["rows"]
                ],
            )
        )
    lines.append("")

    # ---- 6
    lines.append("### 6. 引擎实播（模拟时间，不 sleep）")
    lines.append("")
    eng = data["engine"]
    lines.append(
        "构造 `PetEngine` 后逐条 `force_perform`，以 1/60s 步长推进 `engine.tick` 直到状态机回到 "
        f"`IDLE`（`sit_down` 期望接上 `sit_idle` 常驻），上限 {RUNTIME_FACTOR}× 标称。"
        "`运动结束后静止` = 从「最后一帧有运动的画面」到回 `IDLE` 之间还播了多久静止帧。"
    )
    lines.append("")
    lines.append(
        table(
            ["id", "库", "标称秒", "实播秒", "上限", "运动结束后静止", "状态"],
            [
                [
                    r["id"],
                    "lib" if r["where"] == "library_v1" else "exp",
                    fmt(r["nominal_s"]),
                    fmt(r.get("engine_play_s")),
                    fmt(min(r["nominal_s"], 3.0) * RUNTIME_FACTOR),
                    fmt(r.get("idle_after_motion_s")),
                    r.get("engine_status") or "—",
                ]
                for r in sorted(rows, key=lambda r: -r["nominal_s"])
            ],
        )
    )
    lines.append("")
    if eng["failures"]:
        lines.append("失败项：")
        for item in eng["failures"]:
            lines.append(f"- ✗ {item}")
        lines.append("")

    # ---- 7
    lines.append("### 7. 回归")
    lines.append("")
    rg = data["regressions"]
    lines.append(
        f"- `python -m unittest discover -s mimi_app/tests`（cwd=pet）：退出码 {rg['tests']['exit']}，"
        f"用例 {rg['tests']['count']}，耗时 {rg['tests']['seconds']:.2f}s，"
        f"尝试 {len(rg['tests']['attempts'])} 次。"
    )
    if len(rg["tests"]["attempts"]) > 1:
        lines.append("")
        lines.append("  每次尝试（用于区分 flaky 与真失败）：")
        lines.append("")
        lines.append(
            table(
                ["第几次", "退出码", "用例", "耗时s", "失败用例"],
                [
                    [
                        i,
                        a["exit"],
                        fmt(a["count"]),
                        f"{a['seconds']:.2f}",
                        "、".join(a["failed"]) or "—",
                    ]
                    for i, a in enumerate(rg["tests"]["attempts"], 1)
                ],
            )
        )
        lines.append("")
    lines.append(f"  ```\n  {rg['tests']['summary']}\n  ```")
    lines.append(
        f"- `python scripts/verify/acceptance_expansion.py`：退出码 {rg['acceptance']['exit']}，"
        f"通过 {None if rg['acceptance']['failed'] is None else (rg['acceptance']['total'] or 0) - (rg['acceptance']['failed'] or 0)}"
        f"/{rg['acceptance']['total']}，耗时 {rg['acceptance']['seconds']:.2f}s。"
    )
    lines.append(f"  ```\n  {rg['acceptance']['summary']}\n  ```")
    lines.append("")

    # ---- 额外线索
    lines.append("### 额外线索")
    lines.append("")
    for item in data["clues"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def build_clues(data: dict, rows: list[dict]) -> list[str]:
    """和"卡住"直接相关的线索，全部来自本次实测数字。"""
    clues: list[str] = []
    oneshot = [r for r in rows if not r["loop"] and r.get("ratio") is not None]
    scaled = [r for r in oneshot if r["nominal_s"] > 3.0]
    with_tail = [r for r in oneshot if (r.get("still_frames") or 0) > 0]
    dead = sum(r.get("still_play_s") or 0.0 for r in oneshot)
    over = [r for r in oneshot if classify(r) == "FROZEN"]
    soft = [r for r in oneshot if SOFT_PLAY_LIMIT_S < (r.get("still_play_s") or 0.0) <= STILL_PLAY_TOLERANCE_S]
    worst = sorted(oneshot, key=lambda r: -(r.get("still_play_s") or 0.0))[:6]

    clues.append(
        f"**尾巴规模**：{len(with_tail)}/{len(oneshot)} 条 one-shot 的尾部存在"
        f"连续静止段（阈值 ≤{STILL_PEAK_FRAC:.0%}×峰值）；全部 one-shot 的静止帧"
        f"合计 {sum(r.get('still_frames') or 0 for r in oneshot)} 帧 / "
        f"{sum(r.get('still_s') or 0.0 for r in oneshot):.2f}s 标称，折算到引擎实播是 "
        f"{dead:.2f}s 的「什么也没发生」。"
    )
    if worst:
        clues.append(
            "静止实播秒最长的 6 条（括号内是建议保留到的帧号 / 可删帧数）："
            + "、".join(
                f"`{r['id']}` {r.get('still_play_s') or 0:.2f}s（保留到第 {r.get('keep_to')} 帧，"
                f"可删 {r.get('still_frames')} 帧）"
                for r in worst
                if (r.get("still_play_s") or 0.0) > 0
            )
            + "。"
        )
    clues.append(
        f"**会被读成「卡住」的动作**：静止实播秒 > {STILL_PLAY_TOLERANCE_S}s 共 {len(over)} 条"
        + (f"（{len(soft)} 条落在 {SOFT_PLAY_LIMIT_S}–{STILL_PLAY_TOLERANCE_S}s 之间）" if soft else "")
        + "。注意这个数是**引擎实播时间**，已经算进了 `MAX_ONESHOT_MS=3000` 的加速；"
        "用户在屏幕上看的就是这个时长。"
    )
    clues.append(
        f"**引擎加速会掩盖尾巴**：{len(scaled)}/{len(oneshot)} 条 one-shot 标称 > 3.0s，"
        "`FramePlayer.MAX_ONESHOT_MS=3000` 会把它整体提速到 3.00s。所以裁尾**不会**改变这些动作的"
        "实播总长，只会改变这 3 秒里有多少帧是静止的——验收必须看`静止实播秒`/"
        "`运动结束后静止`，不能只看总时长。同时也要注意：只裁尾巴、不重建时长表时，"
        "加速比会变化（分母变小），实播总长依然是 3.00s。"
    )
    if over:
        clues.append(
            "裁剪优先级（按静止实播秒排序，已排除 loop 与 hold/pingpong 派生）："
            + "、".join(f"`{r['id']}`" for r in over)
            + "。"
        )
    intentional = [r for r in oneshot if classify(r).startswith("INTENTIONAL")]
    clues.append(
        f"**刻意保留的停顿**：{len(intentional)} 条 one-shot 是 hold/pingpong 派生"
        + (f"（{'、'.join('`%s`' % r['id'] for r in intentional[:8])}）" if intentional else "")
        + "。它们的尾帧静止是设计意图（`_order_indices` 的 hold 用 `[首帧]×4 + 全序列 + [尾帧]×4`），"
        "裁尾时应保持其派生语义或干脆不裁，否则派生一致性检查会报 MISMATCH。"
    )
    dup = data["dup_tail"]
    clues.append(
        f"**尾巴不是复制帧**：检查 {dup['actions']} 条动作的尾部 10% 是否存在逐字节重复帧，"
        f"命中 {dup['with_dup']} 条"
        + (f"（{dup['examples']}）" if dup["examples"] else "")
        + "。也就是说这些静止尾巴是生成模型留下的**近静止微抖**，不是可以靠去重/哈希"
        "检测到的定格；只能靠运动量指标识别，裁剪也只能按帧号切。"
    )
    clues.append(
        f"**canvas 声明**：声明与实测不符 {data['frames']['canvas_mismatch']} 条、"
        f"目录基准尺寸不符 {len(data['frames']['size_warn'])} 条（当前 library_v1 "
        f"{LIBRARY_CANVAS[0]}×{LIBRARY_CANVAS[1]}、expansion_v1 "
        f"{EXPANSION_CANVAS[0]}×{EXPANSION_CANVAS[1]}，两套内部自洽）。但生成器 "
        "`scripts/pipeline/derive_actions.py:write_manifest()` 把派生条目的 canvas 硬编码成 "
        "`[512, 768]` / pivot `[256, 744]`：现在 actions.json 里的派生条目是 "
        f"{EXPANSION_CANVAS[0]}×{EXPANSION_CANVAS[1]}，一旦重跑 "
        "`derive_actions.py build` 就会退回 512×768 并与 320×480 的实际帧不符。"
        "裁尾后必须重建派生条目，请盯住这一项（本脚本第 2 项会把它记成尺寸警告）。"
    )
    stale = [
        r for r in data["derived"]["rows"] if r["verdict"] in ("STALE", "UNKNOWN", "OK_VISUAL")
    ]
    if stale:
        clues.append(
            f"**派生条目的字节溯源断了**：{len(stale)}/{len(data['derived']['rows'])} 条派生动作的帧"
            "不是当前源动作帧的字节副本，但重排本身仍然成立（结构自证成立，"
            "或 reverse 的视觉顺序逐帧对上）——说明重排没错、只是源动作被重新渲染过。"
            "对裁剪的直接影响：这些派生动作播的是**旧版（含长尾巴）**的源帧，"
            "裁完源动作后必须整批重跑 `derive_actions.py build`，否则派生动作会继续带着未被裁掉的尾巴。"
            "清单：" + "、".join(f"`{r['id']}←{r['source']}`" for r in stale[:10])
            + ("…" if len(stale) > 10 else "") + "。"
        )
    bad = [r for r in data["derived"]["rows"] if r["verdict"] in ("MISMATCH", "MISSING")]
    if bad:
        clues.append(
            f"派生一致性有 {len(bad)} 条不通过（"
            + "、".join(f"`{r['id']}`" for r in bad[:8])
            + "）：裁了源动作却没重跑 `derive_actions.py build`，会导致派生动作播的是旧帧序。"
        )
    elif not stale:
        clues.append(
            f"派生一致性全部通过（{len(data['derived']['rows'])} 条按源帧哈希逐个对上下标序列）。"
            "**裁剪源动作后这一项一定会先红**，重跑 `derive_actions.py build` 才会恢复——"
            "这正是 AFTER 阶段要验的证据。"
        )
    loops = [r for r in rows if r["loop"]]
    clues.append(
        f"loop 动作 {len(loops)} 条（"
        + "、".join(f"`{r['id']}`" for r in sorted(loops, key=lambda r: r["id"])[:8])
        + "…）：`FramePlayer` 对 loop 永不上报 `finished`，引擎不会自动回 Idle，必须外部 "
        "`stop_walk`/`cancel_performance`/`stand_up`。这类「看起来卡住」是设计如此，"
        "不要和静止尾巴混为一谈。"
    )
    return clues


def compute_sensitivity(rows: list[dict]) -> dict:
    """尾巴结论对"静止阈值"有多敏感：三个阈值下各有多少条动作/多少帧/多少秒。"""
    out: dict[str, dict] = {}
    oneshot = [r for r in rows if not r["loop"] and r.get("runs")]
    for frac in SENSITIVITY:
        actions = 0
        frames = 0
        seconds = 0.0
        over = 0
        for r in oneshot:
            run = int(r["runs"].get(frac, 0))
            if run <= 0:
                continue
            actions += 1
            frames += run
            sec = sum(r["durations"][r["n"] - run :]) / 1000.0
            seconds += sec
            speed = r["speed"] or 1.0
            if sec / speed > STILL_PLAY_TOLERANCE_S and not classify(r).startswith("INTENTIONAL"):
                over += 1
        out[str(frac)] = {
            "actions": actions,
            "frames": frames,
            "seconds": round(seconds, 3),
            "over": over,
        }
    return out


def compute_dup_tail(entries) -> dict:
    """尾部（最后 10% 帧）是否有逐字节重复的定格帧。"""
    with_dup = 0
    examples: list[str] = []
    checked = 0
    for where, entry in entries:
        ident = str(entry["id"])
        paths = lib_frame_paths(entry)
        n = len(paths)
        if n < 3:
            continue
        checked += 1
        tail = paths[max(0, int(n * 0.9)) :]
        digests = []
        for path in tail:
            try:
                digests.append(digest_of(path))
            except OSError:
                digests.append("")
        dup = any(digests[i] == digests[i + 1] for i in range(len(digests) - 1))
        if dup:
            with_dup += 1
            if len(examples) < 6:
                examples.append(ident)
    return {"actions": checked, "with_dup": with_dup, "examples": "、".join(examples)}


# --------------------------------------------------------------------------- 主流程


def collect(args) -> dict:
    lib_raw = load_raw(LIB_MANIFEST)
    exp_raw = load_raw(EXP_MANIFEST)
    entries = [("library_v1", e) for e in lib_raw.get("usable_actions", [])]
    entries += [("expansion_v1", e) for e in exp_raw.get("actions", [])]

    print(f"[1/7] manifest 结构…", flush=True)
    structure = check_manifest_structure(lib_raw, exp_raw)

    print(f"[2/7][3/7] 解码 {len(entries)} 条动作的帧并算尾巴指标（并行 {WORKERS} 路读盘）…", flush=True)
    scans: dict[str, FrameScan] = {}
    metrics: dict[str, dict] = {}
    started = time.time()
    for index, (where, entry) in enumerate(entries, 1):
        ident = str(entry["id"])
        paths = lib_frame_paths(entry)
        scan = scan_frames(paths)
        scans[ident] = scan
        metrics[ident] = tail_metrics(list(entry["duration_ms"]), scan.descs)
        if index % 10 == 0 or index == len(entries):
            print(f"      {index}/{len(entries)} 条动作（{time.time() - started:.0f}s）", flush=True)
    frames = check_frames_and_tail(entries, scans)
    print(f"[5/7] 派生一致性…", flush=True)
    derived = check_derived(exp_raw, scans)

    print(f"[6/7] 引擎实播…", flush=True)
    from mimi_pet.action_library import ActionLibrary

    library = ActionLibrary(LIB_MANIFEST)
    rows = build_rows(entries, scans, metrics, {})
    rows_by_id = {r["id"]: r for r in rows}
    engine = check_engine(library, rows_by_id)
    rows = build_rows(
        entries,
        scans,
        metrics,
        {
            ident: {
                "play_s": info.get("play_s"),
                "status": info.get("status"),
                "idle_after_motion_s": info.get("idle_after_motion_s"),
            }
            for ident, info in engine["results"].items()
        },
    )
    engine_worst = max(
        (r.get("idle_after_motion_s") or 0.0 for r in rows if not r["loop"]), default=0.0
    )
    sensitivity = compute_sensitivity(rows)

    print(f"[7/7] 回归：unittest + acceptance…", flush=True)
    if args.no_tests:
        regressions = {
            "ok": True,
            "skipped": True,
            "tests": {
                "exit": 0,
                "count": None,
                "ok": True,
                "seconds": 0.0,
                "attempts": [],
                "flaky": False,
                "failed": [],
                "summary": "（--no-tests 跳过）",
            },
            "acceptance": {"exit": 0, "total": None, "failed": None, "ok": True, "seconds": 0.0, "summary": "（--no-tests 跳过）"},
        }
    else:
        regressions = check_regressions(sys.executable)

    dup_tail = compute_dup_tail(entries)
    inflight = frames["frames_total"] != frames["frames_ok"]

    hard_failures: list[str] = []
    hard_failures += structure["problems"][:10]
    hard_failures += [f"缺帧：{m}" for m in frames["missing"][:10]]
    hard_failures += [f"坏帧：{m}" for m in frames["undecodable"][:10]]
    hard_failures += [f"尺寸不符：{m}" for m in frames["size_warn"][:10]]
    hard_failures += [
        f"派生：{r['id']} {r['detail']}"
        for r in derived["rows"]
        if r["verdict"] in ("MISMATCH", "MISSING")
    ]
    def _soft(r: dict) -> str:
        bits = [f"字节命中 {r['matched']}/{r['derived_n']}"]
        if r.get("structure_ratio") is not None:
            bits.append(f"结构自证 {r['structure_ratio']:.0%}")
        if r["mode"] == "reverse" and r.get("desc_ok") is not None:
            bits.append("视觉顺序" + ("一致" if r["desc_ok"] else "不一致"))
        if r["mode"] != "reverse":
            bits.append("该 mode 有重复帧，视觉最近邻不定序")
        return f"派生字节漂移：{r['id']}（" + "，".join(bits) + "）"

    soft_findings = [
        _soft(r) for r in derived["rows"] if r["verdict"] in ("STALE", "UNKNOWN")
    ]
    hard_failures += engine["failures"]
    if not regressions["ok"]:
        hard_failures.append("回归测试未通过（见第 7 项）")
    if regressions["tests"]["flaky"]:
        first = regressions["tests"]["attempts"][0]
        soft_findings.append(
            f"unittest 间歇失败（flaky）：第 1 次 {first['seconds']:.2f}s 失败于 "
            + "、".join(first["failed"])
            + f"，重跑 {len(regressions['tests']['attempts'])} 次后通过。"
            "根因是 `dsh_integration._bubble()` 的 1.2s 墙钟节流：机器慢时"
            "`set_mode('agent')` 内部耗时超过窗口，`prompt_active` 的那次气泡不再被节流、"
            "落到 `engine.set_external_bubble`，而测试里的 FakeEngine 没这个方法。"
        )

    frozen = [r for r in rows if not r["loop"] and classify(r) == "FROZEN"]
    if inflight:
        hard_failures.insert(
            0,
            f"素材处于裁剪/重生成中间状态：{frames['frames_total'] - frames['frames_ok']} "
            "个帧槽位读不到文件，manifest 与磁盘不一致",
        )
    gate_ok = not hard_failures
    if args.label.upper() == "AFTER" and frozen:
        gate_ok = False

    data = {
        "label": args.label.upper(),
        "generated": hhmmss(),
        "python": f"{sys.version.split()[0]} (PySide6 {_pyside6_version()})",
        "asset_root": str(REPO / "pet/assets/characters/mimi"),
        "structure": structure,
        "frames": frames,
        "derived": derived,
        "engine": engine,
        "engine_worst": engine_worst,
        "regressions": regressions,
        "rows": rows,
        "hard_failures": hard_failures,
        "soft_findings": soft_findings,
        "gate_ok": gate_ok,
        "dup_tail": dup_tail,
        "sensitivity": sensitivity,
        "inflight": inflight,
        "generator": "derive_actions.write_manifest",
    }
    data["clues"] = build_clues(data, rows)
    return data


def _pyside6_version() -> str:
    try:
        import PySide6

        return PySide6.__version__
    except Exception:
        return "n/a"


#: 中间状态快照的机器可判标记（正文里出现这个词组不算，必须是这个注释）。
INFLIGHT_SENTINEL = "<!-- verify-trim: inflight-snapshot -->"
SECTION_RE = re.compile(r"^## (BASELINE|AFTER)\s*$", re.MULTILINE)


def upsert_section(path: Path, label: str, body: str) -> None:
    """把某个小节整段替换/追加，其它小节原样保留。"""
    header = (
        "# 动作裁剪验证报告（verify_trim.py）\n\n"
        "本文件由 `scripts/verify/verify_trim.py` 生成：重跑 `--label BASELINE` 或 "
        "`--label AFTER` 会**整段替换**对应小节，其余小节原样保留。\n\n"
        "- `BASELINE` = 裁剪前的现状测量；`AFTER` = 裁剪完成后的验收。\n"
        "- 每节自带走查口径与全部明细表；两节可直接逐列对比。\n"
        "- 素材目录：`pet/assets/characters/mimi`（`library_v1` 23 条 + `expansion_v1` 64 条）。\n"
        "- 只读素材/manifest：本脚本不修改任何素材文件。\n"
    )
    body = body.rstrip() + "\n"
    if not path.is_file():
        path.write_text(header + "\n" + body, encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    parts = SECTION_RE.split(text)
    # parts = [head, label1, body1, label2, body2, ...]
    head = parts[0]
    pairs = [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    order = [name for name, _ in pairs]
    replaced = False
    new_pairs = []
    for name, content in pairs:
        if name.upper() == label.upper():
            new_pairs.append((name, body))
            replaced = True
        else:
            new_pairs.append((name, content))
    if not replaced:
        new_pairs.append((label.upper(), body))
    chunks = [head if head.strip() else header + "\n"]
    for name, content in new_pairs:
        chunks.append(f"## {name}\n{content}")
    out = "\n".join(chunk.rstrip() + "\n" for chunk in chunks)
    path.write_text(out, encoding="utf-8")


def print_summary(data: dict) -> None:
    rows = data["rows"]
    oneshot = [r for r in rows if not r["loop"]]
    frozen = [r for r in oneshot if classify(r) == "FROZEN"]
    long_actions = [r for r in oneshot if r["nominal_s"] > LONG_ACTION_S]
    print("")
    print(f"== {data['label']} 摘要（{data['generated']}）==")
    print(f"动作 {len(rows)} 条（one-shot {len(oneshot)} / loop {len(rows) - len(oneshot)}）")
    print(
        f"帧：{data['frames']['frames_total']} 总 / {data['frames']['frames_ok']} 可解码 / "
        f"缺 {len(data['frames']['missing'])} / 坏 {len(data['frames']['undecodable'])} / "
        f"尺寸警告 {len(data['frames']['size_warn'])} / canvas 不符 {data['frames']['canvas_mismatch']}"
    )
    print(f"> {LONG_ACTION_S}s 的 one-shot：{len(long_actions)} 条")
    print(
        f"尾巴：ratio≤{RATIO_STILL:.0%} {sum(1 for r in rows if (r.get('ratio') or 1) <= RATIO_STILL)} 条；"
        f"FROZEN {len(frozen)} 条；SOFT {sum(1 for r in oneshot if classify(r) == 'SOFT')} 条；"
        f"INTENTIONAL {sum(1 for r in oneshot if classify(r).startswith('INTENTIONAL'))} 条"
    )
    print(f"引擎：{'PASS' if data['engine']['ok'] else 'FAIL'}；运动结束后静止最久 {fmt(data['engine_worst'])}s")
    print(
        f"回归：unittest {data['regressions']['tests']['count']} 项 "
        f"{'OK' if data['regressions']['tests']['ok'] else 'FAILED'}；"
        f"acceptance 失败 {data['regressions']['acceptance']['failed']}/"
        f"{data['regressions']['acceptance']['total']}"
    )
    print(f"验收门：{'PASS' if data['gate_ok'] else 'FAIL'}")
    if frozen:
        print("")
        print("FROZEN（静止实播秒 > %.1fs，最该被裁的尾巴）：" % STILL_PLAY_TOLERANCE_S)
        print(f"{'id':26s} {'N':>3s} {'总秒':>6s} {'ratio':>6s} {'静止帧':>5s} {'静止秒':>6s} {'静止实播秒':>8s}")
        for r in sorted(frozen, key=lambda r: -(r.get("still_play_s") or 0.0)):
            print(
                f"{r['id']:26s} {r['n']:3d} {r['nominal_s']:6.2f} {r.get('ratio') or 0:6.3f} "
                f"{r.get('still_frames') or 0:5d} {r.get('still_s') or 0:6.2f} {r.get('still_play_s') or 0:8.2f}"
            )
    if data.get("soft_findings"):
        print("")
        print(f"软发现 {len(data['soft_findings'])} 条：")
        for item in data["soft_findings"][:6]:
            print(f"  ⚠ {item}")
    if data["hard_failures"]:
        print("")
        print("硬失败：")
        for item in data["hard_failures"][:15]:
            print(f"  ✗ {item}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="裁剪前后一致性验证（task-4 的 7 项检查）")
    parser.add_argument("--label", default="BASELINE", choices=["BASELINE", "baseline", "AFTER", "after"])
    parser.add_argument("--out", default=str(REPO / "docs/expansion/TRIM-VERIFY.md"))
    parser.add_argument("--json", action="store_true", help="机器可读结果打到 stdout（不落盘）")
    parser.add_argument("--no-tests", action="store_true", help="跳过第 7 项子进程测试")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "强制覆盖已有小节。默认行为：BASELINE 已存在时拒绝覆盖（基线是一次性快照）；"
            "素材处于 manifest/磁盘不一致的中间状态时也拒绝覆盖完整快照。"
        ),
    )
    args = parser.parse_args(argv[1:])
    os.environ.setdefault("MIMI_DEBUG_LOG", "0")

    data = collect(args)

    body = render_section(data["label"], data)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_file() and not args.force:
        existing = out_path.read_text(encoding="utf-8")
        match = re.search(rf"^## {re.escape(data['label'])}\s*$", existing, re.MULTILINE)
        if match:
            section = existing[match.end():].split("\n## ", 1)[0]
            stable = INFLIGHT_SENTINEL not in section
            if data["label"] == "BASELINE" and stable:
                print(
                    "拒绝覆盖 BASELINE：基线是一份一次性快照，"
                    f"{out_path.name} 里已经有一份完整基线（{section.strip().splitlines()[0][:40]}…）。"
                    "要重新采基线请加 --force；裁剪后应改用 --label AFTER。"
                )
                return 2
            if data.get("inflight") and stable:
                print(
                    "拒绝覆盖：当前素材处于裁剪/重生成的中间状态"
                    f"（{data['frames']['frames_total'] - data['frames']['frames_ok']} 个帧槽位读不到），"
                    f"而 {out_path.name} 里的 {data['label']} 节是一份完整快照。"
                    "等素材稳定后重跑；确要用这份中间状态覆盖请加 --force。"
                )
                return 2
    upsert_section(out_path, data["label"], body)
    print(f"报告已写入 {out_path.relative_to(REPO) if out_path.is_relative_to(REPO) else out_path}")
    if not args.quiet:
        print_summary(data)
    else:
        print(f"gate={'PASS' if data['gate_ok'] else 'FAIL'} hard_failures={len(data['hard_failures'])}")

    if args.json:
        payload = {
            "label": data["label"],
            "generated": data["generated"],
            "gate_ok": data["gate_ok"],
            "hard_failures": data["hard_failures"],
            "long_actions": [r["id"] for r in data["rows"] if not r["loop"] and r["nominal_s"] > LONG_ACTION_S],
            "frozen": [
                {
                    "id": r["id"],
                    "n": r["n"],
                    "nominal_s": r["nominal_s"],
                    "ratio": r.get("ratio"),
                    "still_frames": r.get("still_frames"),
                    "still_s": r.get("still_s"),
                    "still_play_s": r.get("still_play_s"),
                    "engine_play_s": r.get("engine_play_s"),
                }
                for r in data["rows"]
                if not r["loop"] and classify(r) == "FROZEN"
            ],
            "actions": [
                {
                    "id": r["id"],
                    "where": r["where"],
                    "n": r["n"],
                    "nominal_s": round(r["nominal_s"], 4),
                    "loop": r["loop"],
                    "ratio": None if r.get("ratio") is None else round(r["ratio"], 6),
                    "still_frames": r.get("still_frames"),
                    "still_s": round(r.get("still_s") or 0.0, 4),
                    "still_play_s": round(r.get("still_play_s") or 0.0, 4),
                    "engine_play_s": r.get("engine_play_s"),
                    "idle_after_motion_s": r.get("idle_after_motion_s"),
                    "verdict": classify(r),
                }
                for r in sorted(data["rows"], key=lambda r: r["id"])
            ],
        }
        print(json.dumps(payload, ensure_ascii=False))

    return 0 if data["gate_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
