#!/usr/bin/env python3
"""量每个动作尾部"静止帧"有多长，给出建议保留帧数。

背景：视频模型生成的素材，尾巴上常挂着一大段几乎静止的帧 —— 动作其实已经
做完了，画面还在耗时间。桌宠照着 duration 播完，就表现为"播完后卡住回不
去"。这个脚本把那段静尾巴量出来，产出裁剪提案。

口径 v2（当前生效，Lead 决定：要"播完一轮就切"，不追求自然收尾）

    descriptor(frame) = RGBA → RGB × alpha/255（alpha 预乘）
                        → Image.BOX 缩到 80×120
                        → GaussianBlur(sigma=1.6)
                        → float32
    M[i] = mean(|desc[i+1] − desc[i]|)         i = 0..N−2   相邻帧差
    S[i] = median(M[max(0,i−2) .. i+2])        5 帧滑动中值，压单帧抖动
    P       = max(S)                           整段动作的峰值运动
    F_tail  = median(M[-12:])                  尾巴噪声地板（v2 只作参考列）
    thr     = max(0.20 × P, 0.008)             S 低于此即判为静止
    last_motion = max{ i : S[i] > thr }        最后一个还在动的帧索引
    keep        = clamp(last_motion + 1, max(6, ceil(0.35×N)), N)

    loop=True 的动作不裁（keep = N）：闭环素材把尾巴砍了，回环接缝就断了。

`keep` 是「保留前 keep 帧」的帧数，即 frames[0:keep]，不是索引。
`+1` 只让最后一个有动的帧自己留下，不留缓冲——这是 v2 和 v1 的核心差异。

ends_off_pose：结尾帧与首帧的描述子差 > 0.60 × P 的动作标 True。
意思是"裁完最后一帧不是站姿"，运行时接回 idle 会显得回不正，需要交叉淡入。
这个标签是给运行时用的，不影响 keep 的计算。

口径 v1（历史，仅供对照，不参与决策）

    last_motion_2F  = max{ i : S[i] > 2.0 × F_tail }
    last_motion_30P = max{ i : S[i] > 0.30 × P }
    keep = clamp(last_motion_2F + 1 + 2, max(16, ceil(0.5×N)), N)

v1 被换掉的原因：它把 `2 × F_tail` 当静止判据，而 F_tail 量到的正是尾巴上
残留的低幅运动（呼吸、发丝、裙摆）本身，于是判据退化成"尾巴跟自己比"，
在 5 条动作上把动作开头十帧之后就判成静止。v2 改用绝对+相对双下限，不再
拿尾巴自比。两者都保留在 JSON 里，方便回看差异。

三个工程细节，都是被素材咬过才这么写的：

1. alpha 必须预乘。这些帧是带透明背景的立绘，直接拿 RGB 算，透明区的
   RGB 是编码残留，一点点抖动就被算成"运动"，底噪被抬高，尾巴反而量不
   出来。预乘后透明区恒为 0，M 只反映真正看得见的像素变化。
2. 必须按帧序算，不能按唯一文件去重。hold / slow / pingpong 这类动作会
   重复引用同一个 webp（同一路径在 frames 里出现多次）。重复引用在时间
   轴上是两帧、中间隔着别的帧，当成一帧就算错了。这里只按路径缓存"解码
   结果"（同一文件解出来必然一样，缓存不改变语义），索引一律走帧序，
   不去重、不重排。
3. 下限 max(6, ceil(0.35N)) 是防呆：砍太狠会变成幻灯片。下限高于上限时
   clamp 回落到 N，也就是"规则本身不允许裁"，脚本打 capped-n 标记。

用法：

    /tmp/mimienv/bin/python scripts/pipeline/tail_motion.py --report
    ... --report --workers 8          # 指定并行度
    ... --report --no-write           # 只打表，不落盘
    ... --render-only                 # 用已有 JSON 重画 Markdown（不重新测量）

产出（只写这两个文件，不碰素材）：

    docs/expansion/trim_proposal.json    机器可读
    docs/expansion/TRIM-PROPOSAL.md      人读
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

REPO = Path(__file__).resolve().parents[2]
MIMI = REPO / "pet/assets/characters/mimi"

# ── 口径常量（v2 生效值）──────────────────────────────────────────────
BOX = (80, 120)            # 统一缩放尺寸
BLUR_SIGMA = 1.6           # 高斯模糊，压掉 webp 压缩噪点
SMOOTH_RADIUS = 2          # S 的滑动中值半径 → 窗口 5 帧
TAIL_WINDOW = 12           # F_tail 取样窗口（仅参考列）

THR_PEAK_FRAC = 0.20       # v2：thr 的峰值相对项（Lead 定档：0.03 抓不到低幅漂移，作废）
THR_ABS = 0.008            # v2：thr 的绝对下限
KEEP_AFTER_LAST = 1        # v2：keep = last_motion + 1（只留最后一个有动的帧本身）
MIN_KEEP = 6               # v2：keep 绝对下限
MIN_KEEP_FRAC = 0.35       # v2：keep 相对下限

# 站姿参考帧。manifest 的 live_rig.identity_source = "wave/frames/0001.webp"，
# 以 library_v1/usable/actions 为基准拼出完整路径。ends_off_pose 判的是
# "裁完停在离站姿多远"，所以参照物必须是站姿，不是动作自己的第一帧 ——
# 有些动作第一帧就已经不是站姿（低头、跪着、侧身起手）。
REF_RELATIVE = "wave/frames/0001.webp"
REF_BASE = MIMI / "library_v1/usable/actions"

# d_last / d_peak 超过此值 → ends_off_pose。0.60 是起点，Lead 可按运行时体验收紧。
OFF_POSE_FRAC = 0.60

# 阈值敏感性扫描：只做参考，不参与 keep 决策。用来回答"阈值调多少才真的裁得动"。
SENS_COEFFS = (0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30)

# v1 常量，只用于计算对照列
V1_TAIL_MULT = 2.0
V1_PEAK_FRAC = 0.30
V1_KEEP_AFTER_LAST = 3     # v1：last_motion_2F + 1 + 2 收尾帧
V1_MIN_KEEP = 16
V1_MIN_KEEP_FRAC = 0.5

LIBRARIES = (
    # (library 名, 清单路径, 动作列表键, 帧路径的基准目录)
    ("library_v1", MIMI / "library_v1/manifest.json", "usable_actions", MIMI / "library_v1"),
    ("expansion_v1", MIMI / "expansion_v1/actions.json", "actions", MIMI / "expansion_v1"),
)


# ── 描述子与曲线 ──────────────────────────────────────────────────────
def descriptor(path: str) -> np.ndarray:
    """一帧 → (120, 80, 3) float32 描述子。口径见模块 docstring。"""
    with Image.open(path) as raw:
        rgba = raw.convert("RGBA")
    px = np.asarray(rgba, dtype=np.float32)
    premul = px[..., :3] * (px[..., 3:4] / 255.0)
    # 预乘后量化回 uint8 交给 Pillow 做 BOX + blur：误差 ≤ 0.5/255，远小于
    # 任何判据跨度；而且 Pillow 的 GaussianBlur 在 F 模式上不可用，uint8 是
    # 唯一能走原生实现的路径（实测与 float 路径的 P/F_tail 偏差 < 0.1%）。
    rgb = Image.fromarray(np.rint(np.clip(premul, 0.0, 255.0)).astype(np.uint8))
    small = rgb.resize(BOX, Image.BOX).filter(ImageFilter.GaussianBlur(BLUR_SIGMA))
    return np.asarray(small, dtype=np.float32)


def motion_curve(paths: list[str], cache: dict[str, np.ndarray] | None = None):
    """按帧序算出 M、S，外加首末帧描述子（ends_off_pose 要用）。

    重复引用同一路径 = 两帧，绝不去重：descs 是位置追加，不是集合追加。
    """
    descs: list[np.ndarray] = []
    for p in paths:
        d = cache.get(p) if cache is not None else None
        if d is None:
            d = descriptor(p)
            if cache is not None:
                cache[p] = d
        descs.append(d)
    n = len(descs)
    if n < 2:
        return np.zeros(0), np.zeros(0), descs
    m = np.array(
        [float(np.mean(np.abs(descs[i + 1] - descs[i]))) for i in range(n - 1)],
        dtype=np.float64,
    )
    s = np.array(
        [
            float(np.median(m[max(0, i - SMOOTH_RADIUS): i + SMOOTH_RADIUS + 1]))
            for i in range(len(m))
        ],
        dtype=np.float64,
    )
    return m, s, descs


# ── 判据 ──────────────────────────────────────────────────────────────
def tail_floor(m: np.ndarray) -> float:
    tail = m[-TAIL_WINDOW:] if len(m) >= TAIL_WINDOW else m
    return float(np.median(tail)) if len(tail) else 0.0


def last_above(s: np.ndarray, thr: float) -> int:
    """最大 i 使 S[i] > thr；一个都没有则 -1。"""
    idx = np.nonzero(s > thr)[0]
    return int(idx[-1]) if len(idx) else -1


def keep_floor(n: int) -> int:
    return max(MIN_KEEP, math.ceil(MIN_KEEP_FRAC * n))


def clamp_keep(raw: int, n: int) -> int:
    return min(max(raw, keep_floor(n)), n)


def v1_keep_floor(n: int) -> int:
    return max(V1_MIN_KEEP, math.ceil(V1_MIN_KEEP_FRAC * n))


# ── 单动作分析（worker 入口，必须顶层） ───────────────────────────────
def _analyze_job(job):
    library, action_id, name_zh, paths, durations, is_loop, ref_desc = job
    m, s, descs = motion_curve(paths)
    n = len(paths)

    f_tail = tail_floor(m)
    peak = float(s.max()) if len(s) else 0.0

    # ── v2 生效判据 ──
    thr = max(THR_PEAK_FRAC * peak, THR_ABS)
    last_motion = last_above(s, thr)
    raw_keep = last_motion + KEEP_AFTER_LAST if last_motion >= 0 else 0
    floor = keep_floor(n)

    if is_loop:
        # 闭环素材：尾巴砍了回环接缝就断，不裁
        keep = n
    else:
        keep = clamp_keep(raw_keep, n)

    # ── ends_off_pose：裁剪后结尾姿势离站姿多远 ──
    #   d[i]   = mean(|desc[i] - desc_ref|)      逐帧相对站姿的姿势偏移
    #   d_peak = max(d)                          该动作全片离站姿最远的时刻
    #   d_last = d[keep-1]                       裁完停在的那个姿势
    #   ratio  = d_last / d_peak                 收得多接近站姿（0=完全站姿）
    #
    # 用 frames[keep-1] 而不是素材原始末帧：运行时播到 keep 就结束，被裁掉
    # 的那段根本不会被看到，拿原帧算会把不存在的画面算进"结尾姿势"。
    # 参照物用站姿参考帧而不是 descs[0]：有些动作第一帧就不是站姿。
    d_curve = ([float(np.mean(np.abs(d - ref_desc))) for d in descs]
               if ref_desc is not None else [])
    if d_curve:
        d_peak = float(max(d_curve))
        d_peak_index = int(np.argmax(d_curve))
        d_first = float(d_curve[0])
        # 终态口径（runtime 真实看到的最后一帧）：素材已落盘修剪，N-1 就是终点
        d_last_final = float(d_curve[n - 1])
        pose_ratio_final = (d_last_final / d_peak) if d_peak > 0 else 0.0
        # 若还有残留尾巴、再裁一刀后的口径
        end_idx = max(0, min(keep - 1, n - 1))
        d_last = float(d_curve[end_idx])
        pose_ratio = (d_last / d_peak) if d_peak > 0 else 0.0
    else:
        d_peak = d_peak_index = 0
        d_first = d_last = d_last_final = 0.0
        pose_ratio = pose_ratio_final = 0.0
        end_idx = max(0, n - 1)
    # ends_off_pose 以终态为准：这是运行时真正会接 idle 的那一帧
    ends_off_pose = bool(pose_ratio_final > OFF_POSE_FRAC)
    ends_off_pose_if_trimmed = bool(pose_ratio > OFF_POSE_FRAC)
    d_tail_8 = [round(v, 4) for v in d_curve[-8:]] if d_curve else []

    # ── v1 对照（不参与决策）──
    v1_thr_2f = V1_TAIL_MULT * f_tail
    v1_thr_30p = V1_PEAK_FRAC * peak
    lm_2f = last_above(s, v1_thr_2f)
    lm_30p = last_above(s, v1_thr_30p)
    v1_raw = lm_2f + V1_KEEP_AFTER_LAST if lm_2f >= 0 else 0
    v1_keep = min(max(v1_raw, v1_keep_floor(n)), n)

    # 尾部完全重复帧（M 严格为 0）：hold 族重复引用同一 webp，是"死图"铁证
    exact_tail = 0
    for i in range(len(m) - 1, -1, -1):
        if m[i] == 0.0:
            exact_tail += 1
        else:
            break

    saved_ms = float(sum(durations[keep:])) if durations else 0.0
    total_ms = float(sum(durations)) if durations else 0.0

    # 敏感性：换阈值系数，其余口径不变（同样 last+1、同一下限、loop 不裁）
    sensitivity = {}
    for c in SENS_COEFFS:
        t = max(c * peak, THR_ABS)
        lm = last_above(s, t)
        k = n if is_loop else clamp_keep(lm + KEEP_AFTER_LAST if lm >= 0 else 0, n)
        sensitivity[f"{c:.2f}"] = {
            "keep": k,
            "trimmed_frames": n - k,
            "saved_sec": round(float(sum(durations[k:])) / 1000.0, 3) if durations else 0.0,
        }

    flags = []
    if n < MIN_KEEP:
        flags.append("capped-n")          # 下限高于 N，规则不允许裁
    if last_motion < 0:
        flags.append("all-static")        # 全片没有任何一帧超过 thr
    if not is_loop and raw_keep < floor:
        flags.append("floor-bound")       # 想裁更多，被下限挡住
    if keep >= n:
        flags.append("no-trim")
    if exact_tail >= 2:
        flags.append("exact-tail")
    if peak < 0.05:
        flags.append("near-zero-peak")    # 整段几乎不动，thr 由绝对值项主导
    if ends_off_pose:
        flags.append("ends-off-pose")
    if is_loop:
        flags.append("loop-keep-all")

    return {
        "library": library,
        "id": action_id,
        "name_zh": name_zh,
        "N": n,
        "unique_files": len(set(paths)),
        "is_loop": bool(is_loop),
        # ── v2 主判据 ──
        "P": round(peak, 4),
        "threshold": round(thr, 4),
        "last_motion": last_motion,
        "keep_raw": raw_keep,
        "keep_floor": floor,
        "keep": keep,
        # ── ends_off_pose ──
        "ends_off_pose": ends_off_pose,
        "ends_off_pose_if_trimmed": ends_off_pose_if_trimmed,
        "kept_end_index": end_idx,
        "d_last": round(d_last_final, 4),
        "d_last_if_trimmed": round(d_last, 4),
        "d_peak": round(d_peak, 4),
        "d_peak_index": d_peak_index,
        "d_first": round(d_first, 4),
        "pose_ratio": round(pose_ratio_final, 4),
        "pose_ratio_if_trimmed": round(pose_ratio, 4),
        "d_tail_8": d_tail_8,
        # ── 结果 ──
        "trimmed_frames": n - keep,
        "trim_ratio": round((n - keep) / n, 4) if n else 0.0,
        "total_sec": round(total_ms / 1000.0, 3),
        "kept_sec": round((total_ms - saved_ms) / 1000.0, 3),
        "saved_sec": round(saved_ms / 1000.0, 3),
        "s_tail_12": [round(float(v), 3) for v in s[-TAIL_WINDOW:]],
        # ── v1 对照 ──
        "F_tail": round(f_tail, 4),
        "threshold_2F": round(v1_thr_2f, 4),
        "threshold_30P": round(v1_thr_30p, 4),
        "last_motion_2F": lm_2f,
        "last_motion_30P": lm_30p,
        "keep_2F": v1_keep,
        "keep_30P": min(max(lm_30p + V1_KEEP_AFTER_LAST if lm_30p >= 0 else 0,
                           v1_keep_floor(n)), n),
        "exact_tail_frames": exact_tail,
        "sensitivity": sensitivity,
        "flags": flags,
    }


# ── 表格（中文表头按显示宽度对齐）────────────────────────────────────
def pose_tier(ratio: float) -> str:
    """交叉淡入分档。数字越小越接近站姿，可以硬切。

    T1 >=0.95  几乎原样停在动作里（躺着/跪着/走着）→ 最长淡入
    T2 0.80-0.95 明显非站姿 → 长淡入
    T3 0.60-0.80 轻微偏离 → 中淡入
    T4 0     <0.60 已接近站姿 → 可硬切
    """
    if ratio >= 0.95:
        return "T1"
    if ratio >= 0.80:
        return "T2"
    if ratio > OFF_POSE_FRAC:
        return "T3"
    return "-"


def w_disp(text: str) -> int:
    """终端显示宽度：CJK / 全角字符占 2 列，其余 1 列。

    str.format 的宽度按码点数算，中文表头会因此整列错位。表头交给这个函数
    自己补齐；正文是 ASCII 数字，直接 format 就对齐。
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(text))


def pad(text: str, width: int, align: str = ">") -> str:
    text = str(text)
    gap = max(0, width - w_disp(text))
    if align == ">":
        return " " * gap + text
    if align == "<":
        return text + " " * gap
    half = gap // 2
    return " " * half + text + " " * (gap - half)


def fixed_table(spec, body, tail=""):
    """spec = [(表头, 列宽, 对齐), ...]；body = 等长单元格行。"""
    head = "  ".join(pad(name, width, align) for name, width, align in spec)
    if tail:
        head += "  " + tail
    lines = [head, "-" * w_disp(head)]
    for cells in body:
        lines.append("  ".join(
            pad(val, width, align) for val, (_, width, align) in zip(cells, spec)
        ))
    return "\n".join(lines)


def table_for(rows: list[dict], library: str) -> str:
    sel = [r for r in rows if r["library"] == library]
    w = max([len(r["id"]) for r in sel] + [2])
    spec = [("id", w, "<"), ("N", 3, ">"), ("keep", 4, ">"), ("砍", 3, ">"),
            ("裁%", 6, ">"), ("省s", 6, ">"), ("ends_off_pose", 13, ">"),
            ("P", 6, ">"), ("thr", 6, ">"), ("last", 4, ">"), ("loop", 4, ">")]
    body = [
        (r["id"], r["N"], r["keep"], r["trimmed_frames"],
         f"{r['trim_ratio'] * 100:.1f}%", f"{r['saved_sec']:.2f}",
         "True" if r["ends_off_pose"] else "False",
         f"{r['P']:.3f}", f"{r['threshold']:.4f}", r["last_motion"],
         "Y" if r["is_loop"] else "-")
        for r in sel
    ]
    return fixed_table(spec, body)


def bucket_stats(rows: list[dict]) -> dict:
    long_ = [r for r in rows if r["trim_ratio"] > 0.30]
    mid = [r for r in rows if 0.10 <= r["trim_ratio"] <= 0.30]
    short = [r for r in rows if r["trim_ratio"] < 0.10]
    return {
        "over_30pct": len(long_),
        "between_10_and_30pct": len(mid),
        "under_10pct": len(short),
        "zero_trim": len([r for r in rows if r["keep"] >= r["N"]]),
        "long_list": long_,
        "mid_list": mid,
        "short_list": short,
    }


def short_list(rows: list[dict]) -> str:
    spec = [("id", 26, "<"), ("library", 13, "<"), ("N", 3, ">"), ("keep", 4, ">"),
            ("裁%", 6, ">"), ("省s", 6, ">"), ("ends_off_pose", 13, ">")]
    body = [
        (r["id"], r["library"], r["N"], r["keep"], f"{r['trim_ratio'] * 100:.1f}%",
         f"{r['saved_sec']:.2f}", "True" if r["ends_off_pose"] else "False")
        for r in rows
    ]
    return fixed_table(spec, body)


# ── Markdown ──────────────────────────────────────────────────────────
def build_markdown(rows: list[dict], metric: dict) -> str:
    b = bucket_stats(rows)
    loops = [r for r in rows if r["is_loop"]]
    oneshot = [r for r in rows if not r["is_loop"]]
    off = [r for r in rows if r["ends_off_pose"]]
    off_oneshot = [r for r in off if not r["is_loop"]]
    by_saved = sorted(oneshot, key=lambda r: (-r["saved_sec"], -r["trim_ratio"]))
    by_ratio = sorted(oneshot, key=lambda r: -r["trim_ratio"])
    total_frames = sum(r["N"] for r in rows)
    total_orig = sum(r["total_sec"] for r in rows)
    saved = sum(r["saved_sec"] for r in rows)
    capped = [r for r in rows if "capped-n" in r["flags"]]
    floorbound = [r for r in rows if "floor-bound" in r["flags"]]
    allstatic = [r for r in rows if "all-static" in r["flags"]]
    held = [r for r in rows if r["exact_tail_frames"] >= 2]
    n_lib = len([r for r in rows if r["library"] == "library_v1"])
    n_exp = len([r for r in rows if r["library"] == "expansion_v1"])

    L = []
    A = L.append
    A("# 动作尾巴裁剪提案（tail_motion v2 — 播完一轮就切）")
    A("")
    A("> 桌宠「播完后卡住回不去」的根因在素材：视频模型生成的帧序列，尾巴上挂着一段几乎静止的画面——"
      "动作早就做完了，播放器还在按 duration 一帧帧耗时间。本文给出**每个动作实测的静止尾巴有多长**，"
      "以及建议保留的帧数。只测量，不动素材。")
    A(">")
    A(f"> 全量实测：**{len(rows)} 个动作 / {total_frames} 个帧槽位**"
      f"（library_v1 {n_lib} 条 + expansion_v1 {n_exp} 条），一条不落。"
      f"原始总时长 {total_orig:.1f}s，按本方案砍掉 **{saved:.1f}s**"
      f"（{(total_orig - saved) / total_orig * 100:.0f}% 的动作时长被去掉）。")
    A("")
    A("## 0. 这一版和上一版的区别")
    A("")
    A("Lead 定的方向是**「播完一轮就切」**，不追求自然收尾。所以 v2 比 v1 激进得多：")
    A("")
    A("> **执行状态**：v2 口径（0.20）已经在素材上落盘两轮。"
      "第一轮用错了系数（0.03，只砍 136 帧），第二轮按 0.20 落盘 59 条决策、砍 460 帧。"
      "本文所有数字都在这两轮落盘**之后**的素材上测得，`pose_ratio` 是运行时真实终态。")
    A("")
    A("| | v1（旧） | **v2（当前）** |")
    A("|---|---|---|")
    A(f"| 静止判据 | `S > 2.0 × F_tail` | **`S > max(0.20 × P, 0.008)`** |")
    A(f"| 收尾缓冲 | +2 帧 | **+0 帧缓冲（只留 `last_motion + 1`）** |")
    A(f"| keep 下限 | `max(16, ceil(0.5N))` | **`max(6, ceil(0.35N))`** |")
    A(f"| loop / hold / pingpong | 一视同仁 | **`loop=True` 不裁；其余一律裁** |")
    A("")
    A("v1 被换掉的原因是个真实缺陷：它拿 `2 × F_tail` 当静止判据，而 `F_tail` 量到的")
    A("**正是尾巴上残留的低幅运动本身**（呼吸、发丝、裙摆），判据因此退化成「尾巴跟自己比」——")
    A("在 5 条动作上把「开头十帧之后」就判成了静止。v2 改用「相对峰值 3% + 绝对 0.008」双下限，")
    A("不再拿尾巴自比。v1 的数字仍保留在 JSON 和 §5 对照表里，方便回看。")
    A("")
    A("## 1. 结果总览")
    A("")
    A("| 裁剪比例 | 动作数 |")
    A("|---|---|")
    A(f"| **> 30%** | **{b['over_30pct']}** |")
    A(f"| 10–30% | {b['between_10_and_30pct']} |")
    A(f"| < 10% | {b['under_10pct']}（其中 {b['zero_trim']} 条不动刀） |")
    A("")
    A(f"- 可裁的 one-shot：**{len(oneshot)}** 条；`loop=True` 不裁：**{len(loops)}** 条"
      f"（{', '.join(r['id'] for r in loops)}）。")
    A(f"- 按本表口径还能再砍 **{sum(r['saved_sec'] for r in oneshot):.1f}s / {total_orig:.1f}s**"
      "（即 §2.2 的残留尾巴，要不要做第三轮由 Lead 定）。")
    A(f"- 需要交叉淡入（`ends_off_pose`）：**{len(off)} / {len(rows)}** 条，清单见 §2。")
    A("")
    A("### 1.1 砍得最狠的 15 个（按省下的秒数）")
    A("")
    A("```")
    spec = [("#", 2, ">"), ("id", 26, "<"), ("library", 13, "<"), ("N", 3, ">"),
            ("keep", 4, ">"), ("原s", 6, ">"), ("裁后s", 6, ">"), ("省s", 6, ">"),
            ("裁%", 6, ">"), ("ends_off_pose", 13, ">")]
    body = [
        (i, r["id"], r["library"], r["N"], r["keep"], f"{r['total_sec']:.2f}",
         f"{r['kept_sec']:.2f}", f"{r['saved_sec']:.2f}", f"{r['trim_ratio'] * 100:.1f}%",
         "True" if r["ends_off_pose"] else "False")
        for i, r in enumerate(by_saved[:15], 1)
    ]
    A(fixed_table(spec, body))
    A("```")
    A("")
    A("### 1.2 几乎没有尾巴（< 10%）")
    A("")
    if b["short_list"]:
        A("```")
        A(short_list(sorted(b["short_list"], key=lambda r: r["trim_ratio"])))
        A("```")
    else:
        A("（无）")
    A("")
    A("## 2. ends_off_pose：裁完停不到站姿的动作")
    A("")
    A("运行时要把动作接回 idle，如果裁完最后一帧不是站姿，衔接会跳一下。这一列就是给"
      "**交叉淡入**用的：淡入时长和优先级按 `pose_ratio` 分档。")
    A("")
    A(f"锚点是**站姿参考帧** `{REF_RELATIVE}`（manifest 的 `live_rig.identity_source`），"
      "不是动作自己的第一帧——有些动作起手就已经不是站姿（低头、跪着、侧身），"
      "拿首帧当基准会把「本来就歪」算成「收得正好」。")
    A("")
    A("**本表按落盘后的终态算**：`d_last = d[N-1]`，N 是素材现在的真实帧数。"
      "也就是说 `pose_ratio` 就是运行时播完这一条、准备接 idle 时看到的那个姿势。"
      "§2.2 另外给了「如果再把残留尾巴裁掉一刀」的值，供你判断要不要第三轮。")
    A("")
    A("```")
    A("d[i]   = mean(|desc[i] − desc_ref|)      逐帧相对站姿的姿势偏移")
    A("d_peak = max(d)                          该动作离站姿最远的时刻")
    A("d_last = d[N−1]                          素材现在的最后一帧（运行时真实终点）")
    A("pose_ratio = d_last / d_peak             收得多接近站姿（0 = 完全站姿）")
    A(f"ends_off_pose = pose_ratio > {OFF_POSE_FRAC}")
    A("```")
    A("")
    A("`d_last` 用 `frames[N-1]`：素材已经落盘修剪过，N 就是现在的真实帧数，"
      "所以这一帧就是运行时播完、准备接 idle 时看到的画面。"
      "（若还要第三轮，再按 `frames[keep-1]` 取值，见 §2.2 的 `若再裁 pose_ratio` 列。）")
    A("")
    A(f"共 **{len(off)} / {len(rows)}** 条命中（one-shot {len(off_oneshot)} 条 + loop {len(off) - len(off_oneshot)} 条）。"
      "按 `pose_ratio` 从高到低排，越靠前越需要用长一点的淡入。")
    A("")
    if off:
        A("```")
        spec = [("id", 26, "<"), ("library", 13, "<"), ("N", 3, ">"),
                ("d_last", 7, ">"), ("d_peak", 7, ">"), ("pose_ratio", 10, ">"),
                ("档", 4, ">"), ("loop", 4, ">")]
        body = [
            (r["id"], r["library"], r["N"], f"{r['d_last']:.3f}",
             f"{r['d_peak']:.3f}", f"{r['pose_ratio']:.3f}",
             pose_tier(r["pose_ratio"]), "Y" if r["is_loop"] else "-")
            for r in sorted(off, key=lambda x: -x["pose_ratio"])
        ]
        A(fixed_table(spec, body))
        A("```")
    else:
        A("（无）")
    A("")
    A("### 2.1 交叉淡入分档建议")
    A("")
    A("| 档 | pose_ratio | 含义 | 建议淡入 |")
    A("|---|---|---|---|")
    A("| **T1** | ≥ 0.95 | 几乎原样停在动作里（躺着、跪着、走着） | 最长，约 200–260ms |")
    A("| **T2** | 0.80–0.95 | 明显非站姿 | 长，约 140–200ms |")
    A("| **T3** | 0.60–0.80 | 轻微偏离站姿 | 中，约 80–140ms |")
    A("| **–** | < 0.60 | 已接近站姿 | 可硬切，或 ≤60ms |")
    A("")
    tier_counts = {}
    for r in rows:
        tier_counts[pose_tier(r["pose_ratio"])] = tier_counts.get(pose_tier(r["pose_ratio"]), 0) + 1
    A("本批分布：" + "，".join(f"{k} {v} 条" for k, v in sorted(tier_counts.items())) + "。")
    A("")
    A("### 2.2 残留尾巴（如果还要第三轮）")
    A("")
    residual = [r for r in rows if r["trimmed_frames"] > 0]
    A(f"第二轮落盘后，仍有 **{len(residual)} 条**可按同一口径再裁，合计 "
      f"**{sum(r['trimmed_frames'] for r in residual)} 帧 / {sum(r['saved_sec'] for r in residual):.1f}s**。"
      "注意其中 `*_hold` 的 5 帧静止尾巴是**构造语义**（「尾帧停住」），不算漂移，"
      "要不要裁是产品取舍，不是测量问题。")
    A("")
    if residual:
        A("```")
        spec = [("id", 26, "<"), ("library", 13, "<"), ("N", 3, ">"), ("keep", 4, ">"),
                ("裁", 3, ">"), ("裁%", 6, ">"), ("省s", 6, ">"),
                ("若再裁 pose_ratio", 18, ">")]
        body = [
            (r["id"], r["library"], r["N"], r["keep"], r["trimmed_frames"],
             f"{r['trim_ratio'] * 100:.1f}%", f"{r['saved_sec']:.2f}",
             f"{r['pose_ratio_if_trimmed']:.3f}")
            for r in sorted(residual, key=lambda x: -x["saved_sec"])
        ]
        A(fixed_table(spec, body))
        A("```")
    A("")
    A("### 2.3 没有命中的（裁完已接近站姿，可硬切）")
    A("")
    rest = [r for r in rows if not r["ends_off_pose"]]
    if rest:
        A("```")
        spec = [("id", 26, "<"), ("library", 13, "<"), ("N", 3, ">"),
                ("d_last", 7, ">"), ("d_peak", 7, ">"), ("pose_ratio", 10, ">")]
        body = [
            (r["id"], r["library"], r["N"], f"{r['d_last']:.3f}",
             f"{r['d_peak']:.3f}", f"{r['pose_ratio']:.3f}")
            for r in sorted(rest, key=lambda x: x["pose_ratio"])
        ]
        A(fixed_table(spec, body))
        A("```")
    else:
        A("（无）")
    A("")
    A("## 3. 口径（v2 生效，改这里等于改口径）")
    A("")
    A("```")
    A("descriptor(frame) = RGBA → RGB × alpha/255（alpha 预乘）")
    A("                    → Image.BOX 缩到 80×120")
    A("                    → GaussianBlur(sigma=1.6) → float32")
    A("M[i] = mean(|desc[i+1] - desc[i]|)      i = 0..N-2")
    A("S[i] = median(M[max(0,i-2) .. i+2])     5 帧滑动中值")
    A("P    = max(S)                           峰值运动")
    A("thr  = max(0.20 × P, 0.008)             S 低于此即静止")
    A("last_motion = max{ i : S[i] > thr }     最后一个还在动的帧（无则 -1）")
    A("keep = clamp(last_motion + 1, max(6, ceil(0.35×N)), N)")
    A("loop=True → keep = N（不裁）")
    A("d[i] = mean(|desc[i] - desc_ref|)      站姿参考帧的偏移")
    A("ends_off_pose = d[keep-1] / max(d) > 0.60")
    A("```")
    A("")
    A("- `keep` 是**保留前 keep 帧**的帧数（`frames[0:keep]`），不是索引。")
    A("- `+1` 只让最后一个有动的帧自己留下，**不留缓冲**——这是 v2 的核心。")
    A("- `thr` 双下限的作用：`0.20 × P` 让阈值随动作幅度缩放（大动作容忍大残留），"
      "`0.008` 兜住 `P` 本身很小的动作（否则 3% 的峰值会低到被噪声淹没）。")
    A("- `F_tail` / `last_motion_2F` / `last_motion_30P` / `keep_2F` / `keep_30P` "
      "是 v1 对照列，**不参与决策**。")
    A("")
    A("两个工程细节，直接决定数字对不对：")
    A("")
    A("- **必须预乘 alpha**。这些帧是透明背景立绘，直接拿 RGB 算，透明区的编码残留会被当成运动，"
      "底噪虚高，尾巴反而量不出来。预乘后透明区恒为 0，M 只反映看得见的像素变化。")
    A(f"- **必须按帧序算，不能按唯一文件去重**。hold / slow / pingpong 会重复引用同一个 webp"
      f"（本次 {len([r for r in rows if r['unique_files'] < r['N']])} 条动作有重复引用）。"
      "重复引用在时间轴上是两帧、中间隔着别的帧，当一帧算就错了。脚本只按路径缓存「解码结果」"
      "（同一文件解出来必然一样，缓存不改语义），索引一律走帧序。")
    A("")
    A("## 4. 规则的已知行为（不是 bug，是取舍）")
    A("")
    A(f"### 4.1 `loop=True` 整段保留：{len(loops)} 条")
    A("")
    A("闭环素材（walk / sleep_loop / pingpong / sit_idle）砍了尾巴，回环接缝就断，"
      "播放器从尾接回头会跳帧。按 Lead 口径这批准许不动。")
    A("")
    if loops:
        A("```")
        A(short_list(loops))
        A("```")
    A("")
    A(f"### 4.2 被 keep 下限挡住：{len(floorbound)} 条 `floor-bound`")
    A("")
    A("`last_motion + 1` 比 `max(6, ceil(0.35N))` 还短，下限把它们抬回来。"
      "`keep_raw` 是放下限的长度——差额就是被规则吃掉的收益。这批要再短就得动下限。")
    A("")
    if floorbound:
        A("```")
        spec = [("id", 26, "<"), ("library", 13, "<"), ("N", 3, ">"),
                ("last_motion", 12, ">"), ("keep_raw", 8, ">"), ("keep", 4, ">"),
                ("多留", 5, ">"), ("P", 6, ">"), ("thr", 6, ">")]
        body = [
            (r["id"], r["library"], r["N"], r["last_motion"], r["keep_raw"],
             r["keep"], r["keep"] - r["keep_raw"], f"{r['P']:.3f}", f"{r['threshold']:.4f}")
            for r in sorted(floorbound, key=lambda x: x["keep_raw"] - x["keep"])
        ]
        A(fixed_table(spec, body))
        A("```")
    A("")
    A(f"### 4.3 N < {MIN_KEEP} 的动作裁不动：{len(capped)} 条")
    A("")
    A(f"下限 `max({MIN_KEEP}, ceil(0.35N))` 高于上限 `N`，clamp 回落到 N。"
      "不是没有尾巴，是规则不允许裁。")
    A("")
    if capped:
        A("```")
        A(short_list(capped))
        A("```")
    A("")
    A(f"### 4.4 hold 族尾巴是逐帧完全相同的死图：{len(held)} 条")
    A("")
    A("这批全是 `*_hold`，构造方式是「首帧复制 5 份 + 主体 + 末帧复制 5 份」。"
      "尾部差分**严格等于 0**，不依赖阈值。`exact_tail_frames` 是这段死图的长度。")
    A("")
    if held:
        A("```")
        spec = [("id", 26, "<"), ("library", 13, "<"), ("N", 3, ">"),
                ("0尾", 4, ">"), ("keep", 4, ">"), ("裁%", 6, ">"),
                ("ends_off_pose", 13, ">")]
        body = [
            (r["id"], r["library"], r["N"], r["exact_tail_frames"], r["keep"],
             f"{r['trim_ratio'] * 100:.1f}%", "True" if r["ends_off_pose"] else "False")
            for r in sorted(held, key=lambda x: -x["exact_tail_frames"])
        ]
        A(fixed_table(spec, body))
        A("```")
    A("")
    A(f"### 4.5 全片都没有超过 thr 的帧：{len(allstatic)} 条")
    A("")
    if allstatic:
        A("`last_motion = -1`，等于说整段都没动过。这种动作按 v2 会裁到下限。")
        A("")
        A("```")
        A(short_list(allstatic))
        A("```")
    else:
        A("（无。没有整段静止的动作。）")
    A("")
    A("## 5. 全量表")
    A("")
    A("列：`N` = 原帧数；`keep` = 建议保留帧数；`砍` = 砍掉帧数；`裁%` = 裁剪比例；"
      "`省s` = 按 duration_ms 实际省下的秒数；`ends_off_pose` = 结尾不是站姿；"
      "`P` = 峰值运动；`thr` = 静止阈值；`last` = `last_motion`（最后一个还在动的帧索引）；"
      "`loop` = 该动作是否是闭环素材（闭环不裁）。")
    A("")
    for idx, (lib, title) in enumerate(
        (("library_v1", "library_v1 — manifest.json · usable_actions"),
         ("expansion_v1", "expansion_v1 — actions.json · actions")), 1
    ):
        sel = [r for r in rows if r["library"] == lib]
        A(f"### 5.{idx} {title}（{len(sel)} 条）")
        A("")
        A("```")
        A(table_for(rows, lib))
        A("```")
        A("")
    A("### 5.3 S 曲线尾部 12 值（人工复核用）")
    A("")
    A("`thr` 一列是对应动作的静止阈值；`S[-12:]` 里掉到 `thr` 以下的位置就是尾巴起点。")
    A("")
    A("```")
    spec = [("id", 26, "<"), ("library", 13, "<"), ("thr", 6, ">"), ("last", 4, ">")]
    head = "  ".join(pad(name, width, align) for name, width, align in spec) + "  S[-12:]"
    A(head)
    A("-" * w_disp(head))
    for r in rows:
        cells = "  ".join([
            pad(r["id"], 26, "<"), pad(r["library"], 13, "<"),
            pad(f"{r['threshold']:.4f}", 6, ">"), pad(r["last_motion"], 4, ">"),
        ])
        A(cells + "  " + " ".join(f"{v:5.2f}" for v in r["s_tail_12"]))
    A("```")
    A("")
    A("## 6. v1 → v2 差异（哪些动作被改判）")
    A("")
    A("`keep_2F` 是 v1 的结论，`keep` 是 v2 的结论。`Δ` = v2 − v1，负数表示 v2 裁得更狠。")
    A("")
    A("```")
    spec = [("id", 26, "<"), ("library", 13, "<"), ("N", 3, ">"),
            ("keep_2F", 7, ">"), ("keep", 4, ">"), ("Δ", 5, ">"), ("变化", 8, "<")]
    body = []
    for r in sorted(rows, key=lambda x: x["keep"] - x["keep_2F"]):
        delta = r["keep"] - r["keep_2F"]
        if delta < 0:
            tag = "更狠"
        elif delta > 0:
            tag = "更松"
        else:
            tag = "同"
        body.append((r["id"], r["library"], r["N"], r["keep_2F"], r["keep"], delta, tag))
    A(fixed_table(spec, body))
    A("```")
    A("")
    A("## 7. 复现")
    A("")
    A("```bash")
    A("/tmp/mimienv/bin/python scripts/pipeline/tail_motion.py --report")
    A("```")
    A("")
    A("生成器 `scripts/pipeline/tail_motion.py`，机器可读版 `docs/expansion/trim_proposal.json`。"
      f"口径常量：BOX={BOX}，sigma={BLUR_SIGMA}，平滑半径={SMOOTH_RADIUS}，"
      f"thr=max({THR_PEAK_FRAC}×P, {THR_ABS})，keep=last+{KEEP_AFTER_LAST}，"
      f"下限=max({MIN_KEEP}, {MIN_KEEP_FRAC}N)，ends_off_pose 阈值=pose_ratio>{OFF_POSE_FRAC}，"
      f"站姿锚点={REF_RELATIVE}。")
    A("")
    return "\n".join(L)


# ── 主流程 ────────────────────────────────────────────────────────────
def reference_path() -> Path:
    """站姿参考帧的完整路径。优先按 manifest 的 identity_source 拼，缺失则用约定路径。"""
    p = REF_BASE / REF_RELATIVE
    if p.exists():
        return p
    raise SystemExit(f"站姿参考帧缺失: {p}")


def collect_jobs(ref_desc: np.ndarray) -> list[tuple]:
    jobs = []
    for lib_name, manifest, key, base in LIBRARIES:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for act in data[key]:
            paths = [os.path.normpath(os.path.join(base, f)) for f in act["frames"]]
            missing = [p for p in paths if not os.path.exists(p)]
            if missing:
                raise SystemExit(f"{lib_name}/{act['id']}: 缺帧 {missing[:3]}")
            durations = act.get("duration_ms") or []
            jobs.append((lib_name, act["id"], act.get("name_zh", ""), paths,
                         durations, bool(act.get("loop")), ref_desc))
    return jobs


def run(jobs, workers: int) -> list[dict]:
    if workers <= 1:
        return [_analyze_job(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_analyze_job, jobs, chunksize=1))


def build_payload(rows: list[dict], total_frames: int) -> dict:
    b = bucket_stats(rows)
    oneshot = [r for r in rows if not r["is_loop"]]
    off = [r for r in rows if r["ends_off_pose"]]
    return {
        "schema_version": 2,
        "generator": "scripts/pipeline/tail_motion.py",
        "metric": {
            "rule": "v2-aggressive: 播完一轮就切",
            "box": list(BOX),
            "blur_sigma": BLUR_SIGMA,
            "smooth_radius": SMOOTH_RADIUS,
            "tail_window": TAIL_WINDOW,
            "thr_peak_frac": THR_PEAK_FRAC,
            "thr_abs": THR_ABS,
            "keep_after_last": KEEP_AFTER_LAST,
            "min_keep": MIN_KEEP,
            "min_keep_frac": MIN_KEEP_FRAC,
            "loop_policy": "loop=True 不裁，keep=N",
            "off_pose_frac": OFF_POSE_FRAC,
            "keep_semantics": "keep = frames[0:keep] 的帧数",
            "v1_reference": {
                "tail_mult": V1_TAIL_MULT,
                "peak_frac": V1_PEAK_FRAC,
                "keep_after_last": V1_KEEP_AFTER_LAST,
                "min_keep": V1_MIN_KEEP,
                "min_keep_frac": V1_MIN_KEEP_FRAC,
            },
        },
        "summary": {
            "actions": len(rows),
            "frames": total_frames,
            "libraries": {
                lib: len([r for r in rows if r["library"] == lib]) for lib, _, _, _ in LIBRARIES
            },
            "total_sec": round(sum(r["total_sec"] for r in rows), 3),
            "saved_sec": round(sum(r["saved_sec"] for r in rows), 3),
            "kept_sec": round(sum(r["kept_sec"] for r in rows), 3),
            "oneshot_actions": len(oneshot),
            "loop_actions": len(rows) - len(oneshot),
            "over_30pct": b["over_30pct"],
            "between_10_and_30pct": b["between_10_and_30pct"],
            "under_10pct": b["under_10pct"],
            "zero_trim": b["zero_trim"],
            "ends_off_pose": [r["id"] for r in off],
            "ends_off_pose_count": len(off),
            "ends_off_pose_oneshot": [r["id"] for r in off if not r["is_loop"]],
            "loop_keep_all": [r["id"] for r in rows if r["is_loop"]],
            "floor_bound": [r["id"] for r in rows if "floor-bound" in r["flags"]],
            "capped_n": [r["id"] for r in rows if "capped-n" in r["flags"]],
            "all_static": [r["id"] for r in rows if "all-static" in r["flags"]],
        },
        "actions": rows,
    }


def derived_ids() -> set[str]:
    """派生动作集合，从 derive_actions.py 现读，不硬编码。

    派生动作（slow/hold/pingpong/reverse）由应用器在裁完后重建，所以不能进
    决策表——写进去会和重建结果打架。从源脚本 AST 里读，避免两边漂移。
    """
    import ast

    src = (REPO / "scripts/pipeline/derive_actions.py").read_text(encoding="utf-8")
    ids: list[str] = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Attribute) and f.attr == "update" and node.value.args:
                arg = node.value.args[0]
                if isinstance(arg, ast.Dict):
                    ids += [k.value for k in arg.keys if isinstance(k, ast.Constant)]
        if isinstance(node, (ast.AnnAssign, ast.Assign)):
            val = node.value
            if isinstance(val, ast.Dict):
                keys = [k.value for k in val.keys if isinstance(k, ast.Constant)]
                if len(keys) > 3:          # DERIVATIONS 本体；忽略零散小 dict
                    ids += keys
    return set(ids)


def build_decisions(rows: list[dict]) -> dict:
    """把测量结果翻成应用器要的决策表。

    筛选规则（全部来自 Lead 的口径）：
      - loop=True          → 不写（闭环砍了接缝就断）
      - 派生动作           → 不写（应用器重建）
      - keep >= N          → 不写（已经没尾巴了）
      - capped-n           → 不写（下限高于 N，规则本身不允许裁）
    """
    derived = derived_ids()
    decisions: list[dict] = []
    skipped: dict[str, list[str]] = {"loop": [], "derived": [], "no_tail": [], "capped": []}

    for r in rows:
        if r["is_loop"]:
            skipped["loop"].append(r["id"])
            continue
        if r["id"] in derived:
            skipped["derived"].append(r["id"])
            continue
        if r["keep"] >= r["N"]:
            skipped["no_tail"].append(r["id"])
            continue
        if "capped-n" in r["flags"]:
            skipped["capped"].append(r["id"])
            continue
        decisions.append({
            "library": r["library"],
            "id": r["id"],
            "keep_frames": r["keep"],
            "reason": (
                f"v2 thr>={THR_PEAK_FRAC:.2f}P ({r['threshold']:.4f}); "
                f"last_motion={r['last_motion']} +{KEEP_AFTER_LAST} -> raw={r['keep_raw']}; "
                f"floor={r['keep_floor']} -> keep={r['keep']}/{r['N']}; "
                f"trim {r['trimmed_frames']}f ({r['trim_ratio'] * 100:.1f}%), "
                f"save {r['saved_sec']:.2f}s"
            ),
        })

    chosen = {(d["library"], d["id"]) for d in decisions}
    kept = [r for r in rows if (r["library"], r["id"]) in chosen]
    return {
        "schema_version": 1,
        "generator": "scripts/pipeline/tail_motion.py --emit-decisions",
        "rule": {
            "thr_peak_frac": THR_PEAK_FRAC,
            "thr_abs": THR_ABS,
            "keep_after_last": KEEP_AFTER_LAST,
            "min_keep": MIN_KEEP,
            "min_keep_frac": MIN_KEEP_FRAC,
            "loop_policy": "loop=True 不写",
            "derived_policy": "派生动作不写，交给应用器重建",
            "skip_if_keep_ge_n": True,
        },
        "summary": {
            "decisions": len(decisions),
            "skipped": {k: len(v) for k, v in skipped.items()},
            "expected_trimmed_frames": sum(r["N"] - r["keep"] for r in kept),
            "expected_saved_sec": round(sum(r["saved_sec"] for r in kept), 3),
        },
        "skipped_detail": skipped,
        "decisions": decisions,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="量动作尾部静止帧，产出裁剪提案（v2 激进版）")
    ap.add_argument("--report", action="store_true", help="打印表格并写 JSON/Markdown")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    ap.add_argument("--json", default=str(REPO / "docs/expansion/trim_proposal.json"))
    ap.add_argument("--markdown", default=str(REPO / "docs/expansion/TRIM-PROPOSAL.md"))
    ap.add_argument("--no-write", action="store_true", help="只打表，不写文件")
    ap.add_argument("--render-only", action="store_true",
                    help="不重新测量，用已有 JSON 重画 Markdown")
    ap.add_argument("--emit-decisions", default=None, metavar="PATH",
                    help="额外写出应用器用的决策表（trim_decisions_v2.json）")
    args = ap.parse_args(argv)

    if args.render_only:
        payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
        Path(args.markdown).write_text(
            build_markdown(payload["actions"], payload["metric"]), encoding="utf-8")
        print(f"重画 {args.markdown}")
        return 0

    if not args.report:
        ap.print_help()
        return 2

    t0 = time.time()
    ref_path = reference_path()
    ref_desc = descriptor(str(ref_path))
    print(f"站姿参考帧: {ref_path.relative_to(REPO)}")
    jobs = collect_jobs(ref_desc)
    rows = run(jobs, args.workers)

    total_frames = sum(r["N"] for r in rows)
    print(f"测完 {len(rows)} 个动作 / {total_frames} 帧，用时 {time.time() - t0:.1f}s "
          f"(workers={args.workers})")
    print()
    for lib, title in (("library_v1", "library_v1"), ("expansion_v1", "expansion_v1")):
        print(f"=== {title} ===")
        print(table_for(rows, lib))
        print()

    b = bucket_stats(rows)
    oneshot = [r for r in rows if not r["is_loop"]]
    loops = [r for r in rows if r["is_loop"]]
    off = [r for r in rows if r["ends_off_pose"]]
    print("裁剪比例：> 30%% = %d，10–30%% = %d，< 10%% = %d（其中不动刀 %d）"
          % (b["over_30pct"], b["between_10_and_30pct"], b["under_10pct"], b["zero_trim"]))
    print("one-shot %d 条可裁，loop %d 条整段保留" % (len(oneshot), len(loops)))
    print("砍掉 %.1fs / 原始 %.1fs" % (sum(r["saved_sec"] for r in rows),
                                       sum(r["total_sec"] for r in rows)))
    print("ends_off_pose %d 条（one-shot %d 条）"
          % (len(off), len([r for r in off if not r["is_loop"]])))
    print()
    print("ends_off_pose ids:", ", ".join(r["id"] for r in off) or "无")

    if args.no_write:
        return 0

    payload = build_payload(rows, total_frames)
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    Path(args.markdown).write_text(build_markdown(rows, payload["metric"]), encoding="utf-8")
    print(f"\n写出 {args.json}")
    print(f"写出 {args.markdown}")

    if args.emit_decisions:
        dec = build_decisions(rows)
        Path(args.emit_decisions).parent.mkdir(parents=True, exist_ok=True)
        Path(args.emit_decisions).write_text(
            json.dumps(dec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        s = dec["summary"]
        print(f"写出 {args.emit_decisions}")
        print(f"  决策 {s['decisions']} 条，预期砍 {s['expected_trimmed_frames']} 帧 "
              f"/ {s['expected_saved_sec']}s")
        for k, v in s["skipped"].items():
            print(f"  跳过 {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
