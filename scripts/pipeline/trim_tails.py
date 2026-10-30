"""按决策表裁掉动作尾部帧，并把多余帧移进隔离目录（绝不删除）。

背景
----
`derive_actions.py` 合成派生动作时取的是 `sorted(source_dir.glob("*.webp"))`：
**目录里剩几张，就按几张重建**。所以缩减源动作必须先把多出来的帧文件移出
`frames/` 目录，再触发派生重建；顺序反了，派生会带着残帧重新生成一遍，
裁剪等于白做。

本脚本按这个顺序做四件事：
1. 读决策表，把每个动作的 `frames` 与 `duration_ms` 同时截到前 `keep_frames`
   项（两者长度不等说明素材本身已经坏了，直接报错跳过，不动它）；
2. 把该动作 `frames/` 目录里"不再被任何动作引用"的 webp 移进
   `pet/assets/characters/mimi/.trimmed/<library>/<action>/`，保留原文件名；
3. 只要有一个"派生源动作"被裁过，就重建 expansion_v1 的派生动作；
4. 写 `docs/expansion/TRIM-APPLIED.json`，跑完整性自检，失败非零退出。

默认 dry-run：只打印对照表，一个字节都不写；`--apply` 才落盘。
dry-run 与 --apply 打印的**表格完全相同**，方便先看再决定。

幂等
----
已裁到位的动作（`keep_frames >= 当前帧数`）记为 noop：不再移文件、不再改
manifest、不触发派生重建。所以重复 `--apply` 不会二次移动、不会改变结果，
也已经没有派生源被裁过时连 `derive build` 都不跑（避免无意义地重写派生目录）。

用法
----
    python trim_tails.py                     # dry-run，看表
    python trim_tails.py --apply             # 真正写盘
    python trim_tails.py --apply \
        --decisions /tmp/trim_decisions.json \
        --base /tmp/trim-sandbox/pet/assets/characters/mimi

    python trim_tails.py --apply --no-derive     # 不动派生（调试用）
    python trim_tails.py --apply --python /tmp/mimienv/bin/python

决策文件（默认 docs/expansion/trim_decisions.json）
    {"schema_version": 1,
     "decisions": [{"library": "expansion_v1", "id": "wave_hello",
                    "keep_frames": 40, "reason": "尾部 22 帧是静止收势"}]}

退出码
    0  全部按计划完成（含 noop），自检通过
    1  完整性自检不通过
    2  决策文件 / 参数有问题，或至少一条决策因契约错误被跳过
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BASE = REPO / "pet/assets/characters/mimi"
DERIVE_REL = Path("scripts/pipeline/derive_actions.py")
DEFAULT_PYTHON = Path("/tmp/mimienv/bin/python")

#: 库名 → manifest 相对路径 + 动作列表字段名。
#: 两套库走同一条代码路径，只靠这张表分派。
LIBRARIES: dict[str, dict[str, str]] = {
    "library_v1": {"manifest": "library_v1/manifest.json", "key": "usable_actions"},
    "expansion_v1": {"manifest": "expansion_v1/actions.json", "key": "actions"},
}
#: expansion_v1 里另一份引用清单：换装预设也有自己的帧和子动作。
PRESETS_REL = "expansion_v1/presets.json"
TRIM_DIRNAME = ".trimmed"
TAIL_LINES = 8


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def norm(path: Path) -> Path:
    """纯字符串归一化：不解析软链接，两侧都过一遍就能稳定比较。

    用 resolve() 会对 18000 条引用做十几万次 lstat，白花时间。
    """
    return Path(os.path.normpath(str(path)))


def human_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def disp_width(text: str) -> int:
    """终端显示宽度：东亚宽字符算 2 列，否则中英文混排的表会歪。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - disp_width(text))
    if align == "right":
        return " " * gap + text
    return text + " " * gap


def render_table(header: list[str], rows: list[list[str]], aligns: list[str]) -> str:
    widths = [max(disp_width(header[i]), *(disp_width(r[i]) for r in rows)) if rows
              else disp_width(header[i]) for i in range(len(header))]
    lines = ["  ".join(pad(header[i], widths[i], aligns[i]) for i in range(len(header)))]
    lines.append("  ".join("-" * widths[i] for i in range(len(header))))
    for row in rows:
        lines.append("  ".join(pad(row[i], widths[i], aligns[i]) for i in range(len(header))))
    return "\n".join(lines)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    """与仓库既有产物保持同一格式：utf-8、ensure_ascii=False、indent=2、尾换行。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 库
# --------------------------------------------------------------------------- #
class Library:
    """一套库的 manifest 视图；内存里改，changed 置位了才写回。"""

    def __init__(self, name: str, base: Path) -> None:
        cfg = LIBRARIES[name]
        self.name = name
        self.path = base / cfg["manifest"]
        self.key = cfg["key"]
        if not self.path.is_file():
            raise SystemExit(f"[fatal] 缺少 {self.path}")
        self.data = read_json(self.path)
        self.entries: list[dict] = self.data[self.key]
        self.changed = False

    def by_id(self, action_id: str) -> dict | None:
        for entry in self.entries:
            if entry.get("id") == action_id:
                return entry
        return None

    def save(self) -> None:
        # 已验证：读进来再 dumps(indent=2)+"\n" 与原文件字节一致，不会顺手改格式。
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.changed = False


# --------------------------------------------------------------------------- #
# 引用收集
# --------------------------------------------------------------------------- #
def _add_ref(refs: dict[Path, set[str]], manifest_path: Path, ref: str) -> None:
    target = norm(manifest_path.parent / ref)
    refs.setdefault(target.parent, set()).add(target.name)


def gather_refs(libs: dict[str, Library], base: Path) -> dict[Path, set[str]]:
    """目录 → 被引用文件名集合。跨库求并集，宁可少移也不多移。"""
    refs: dict[Path, set[str]] = {}

    for lib in libs.values():
        for entry in lib.entries:
            for ref in entry.get("frames") or []:
                _add_ref(refs, lib.path, ref)

    # library_v1：drag_poses.sets[*].frames 也是真实引用。
    drag = (libs["library_v1"].data.get("drag_poses") or {}).get("sets") or {}
    for pose in drag.values():
        for ref in pose.get("frames") or []:
            _add_ref(refs, libs["library_v1"].path, ref)

    # library_v1.live_rig.identity_source：形如 "wave/frames/0001.webp"，
    # 相对 usable/actions/；解析不出来就跳过（它只是身份参考帧）。
    live = libs["library_v1"].data.get("live_rig") or {}
    identity = live.get("identity_source")
    if isinstance(identity, str) and identity:
        manifest_parent = libs["library_v1"].path.parent
        for candidate in (manifest_parent / "usable/actions" / identity,
                          manifest_parent / identity):
            if candidate.is_file():
                _add_ref(refs, libs["library_v1"].path,
                         str(candidate.relative_to(manifest_parent)))
                break

    # expansion_v1/presets.json：只引用 presets/ 下的帧，但仍算进来，
    # 免得将来 presets 引用到 actions/ 时被误移。
    presets_path = base / PRESETS_REL
    if presets_path.is_file():
        presets = read_json(presets_path)
        for preset in presets.get("presets") or []:
            for ref in preset.get("frames") or []:
                _add_ref(refs, presets_path, ref)
            for sub in (preset.get("actions") or {}).values():
                for ref in (sub or {}).get("frames") or []:
                    _add_ref(refs, presets_path, ref)

    return refs


# --------------------------------------------------------------------------- #
# 派生动作（不 import，避免为了读常量把 numpy 拉进来）
# --------------------------------------------------------------------------- #
def _literal_dict(node: ast.Dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key_node, value_node in zip(node.keys, node.values):
        if key_node is None:
            continue
        try:
            key = ast.literal_eval(key_node)
            value = ast.literal_eval(value_node)
        except (ValueError, SyntaxError):
            continue
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = value
    return out


def load_derivations(script: Path) -> dict[str, dict]:
    """从 derive_actions.py 抠出 DERIVATIONS 字面量。

    只认 `DERIVATIONS = {...}` 与 `DERIVATIONS.update({...})` 两种写法；
    抠不到就返回空表——派生预测降级为"不可用"，不影响裁剪本身。
    """
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}
    found: dict[str, dict] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "DERIVATIONS" for t in node.targets):
                if isinstance(node.value, ast.Dict):
                    found.update(_literal_dict(node.value))
        elif isinstance(node, ast.AnnAssign):
            # 源码里写的是 `DERIVATIONS: dict[str, dict] = {...}`，带注解的赋值
            # 在 AST 里是 AnnAssign，漏了这一支就一条派生都读不到。
            target = node.target
            if isinstance(target, ast.Name) and target.id == "DERIVATIONS":
                if isinstance(node.value, ast.Dict):
                    found.update(_literal_dict(node.value))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            func = call.func
            if (isinstance(func, ast.Attribute) and func.attr == "update"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "DERIVATIONS"):
                for arg in call.args:
                    if isinstance(arg, ast.Dict):
                        found.update(_literal_dict(arg))
    return found


def predict_derived(mode: str, count: int) -> tuple[int, int] | None:
    """返回（唯一文件数, 引用帧数），与 derive_actions._order_indices 对齐。

    四种模式去重后都恰好用满源目录的全部帧，所以派生目录里唯一文件数 ==
    裁剪后的源目录帧数，体积也等于源目录体积（derive 是逐字节 copyfile）。

    各模式的引用帧数（n = 源目录唯一帧数）：
        reverse   n          顺序播放
        slow      2n         每帧重复一次
        hold      n + 2      前留两帧预备
        pingpong  n + (n-2)  正放后倒放，首尾不重复
    """
    if count <= 0:
        return None
    if mode == "slow":
        return count, 2 * count
    if mode == "hold":
        # derive_actions 的 hold 当前是 [0]*2 + range(count)：前留两帧预备，尾帧不留
        # 定格。这个常量必须跟着那边改，否则 drift 检测会把正常状态误报成脱节。
        return count, count + 2
    if mode == "pingpong":
        return count, count + max(0, count - 2)
    return count, count  # reverse / 未知模式退化为顺序播放


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #
@dataclass
class Row:
    library: str
    action_id: str
    status: str = "skipped"          # trimmed | noop | skipped
    note: str = ""
    keep: int = 0
    before_frames: int = 0
    after_frames: int = 0
    before_ms: int = 0
    after_ms: int = 0
    dirs: list[Path] = field(default_factory=list)
    surplus: list[Path] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)   # apply 后：隔离目录里的文件名
    moved_bytes: int = 0
    freed: int = 0
    isolation: str = ""


def plan(base: Path, decisions: list[dict], libs: dict[str, Library],
         derive_enabled: bool) -> tuple[list[Row], list[Row], list[Row]]:
    """纯内存裁剪，返回（全部行, 被裁行, 出错行）。dry-run / apply 共用。"""
    all_rows: list[Row] = []
    trimmed_rows: list[Row] = []
    error_rows: list[Row] = []
    seen: set[tuple[str, str]] = set()

    for raw in decisions:
        library = str(raw.get("library") or "")
        action_id = str(raw.get("id") or "")
        row = Row(library=library, action_id=action_id)

        def fail(note: str) -> None:
            row.status = "skipped"
            row.note = note
            all_rows.append(row)
            error_rows.append(row)

        if library not in LIBRARIES:
            fail(f"未知库 {library!r}（只认 {'/'.join(LIBRARIES)}）")
            continue
        key = (library, action_id)

        entry = libs[library].by_id(action_id)
        if entry is None:
            fail(f"{library} 里没有这个动作")
            continue

        frames = entry.get("frames")
        duration = entry.get("duration_ms")
        if not isinstance(frames, list) or not isinstance(duration, list):
            fail("frames / duration_ms 不是数组")
            continue
        row.before_frames = len(frames)
        row.before_ms = sum(int(x) for x in duration)
        if len(frames) != len(duration):
            fail(f"frames({len(frames)}) 与 duration_ms({len(duration)}) 长度不等，契约已坏")
            continue
        if not frames:
            fail("该动作没有帧")
            continue

        keep_raw = raw.get("keep_frames")
        if not isinstance(keep_raw, int) or isinstance(keep_raw, bool):
            fail(f"keep_frames 不是整数：{keep_raw!r}")
            continue
        row.keep = keep_raw
        if keep_raw < 1:
            fail(f"keep_frames={keep_raw}，至少要留 1 帧")
            continue

        # 派生条目是 derive 每次重建的产物，裁它会被下一次重建覆盖。
        source = str(entry.get("source") or "")
        if derive_enabled and source.startswith("derived"):
            fail("这是派生动作，重建会覆盖；请改裁它的源动作")
            continue

        # 去重放在校验之后：前面一条写错了不该把后面正确的同一条也拖下水。
        if key in seen:
            fail("决策表里重复（前一条已生效）")
            continue
        seen.add(key)

        if keep_raw >= row.before_frames:
            row.status = "noop"
            row.after_frames = row.before_frames
            row.after_ms = row.before_ms
            if keep_raw > row.before_frames:
                row.note = f"keep_frames={keep_raw} ≥ 当前 {row.before_frames} 帧，视为已裁剪"
            else:
                row.note = "keep_frames 等于当前帧数"
            # 记下目录只为让自检能看见"上次中断留下的残帧"；noop 不移动任何文件。
            row.dirs = sorted({norm(libs[library].path.parent / ref).parent for ref in frames})
            all_rows.append(row)
            continue

        # —— 内存里截断（apply 时才写回磁盘） ——
        entry["frames"] = frames[:keep_raw]
        entry["duration_ms"] = duration[:keep_raw]
        libs[library].changed = True
        row.status = "trimmed"
        row.after_frames = keep_raw
        row.after_ms = sum(int(x) for x in duration[:keep_raw])
        row.dirs = sorted({norm(libs[library].path.parent / ref).parent for ref in frames})
        row.isolation = str(base / TRIM_DIRNAME / library / action_id)
        all_rows.append(row)
        trimmed_rows.append(row)

    return all_rows, trimmed_rows, error_rows


def compute_surplus(base: Path, libs: dict[str, Library], rows: list[Row]) -> None:
    """裁剪后的引用集合已定，算出每个被裁目录里"没人再引用"的 webp。"""
    refs = gather_refs(libs, base)
    for row in rows:
        surplus: list[Path] = []
        for folder in row.dirs:
            keep = refs.get(folder, set())
            for path in sorted(folder.glob("*.webp")):
                if path.name not in keep and path.is_file():
                    surplus.append(path)
        row.surplus = surplus
        row.freed = sum(p.stat().st_size for p in surplus)


def derived_drift(libs: dict[str, Library], derivations: dict[str, dict]) -> list[dict]:
    """检测派生动作与"当前源目录长度"是否已经脱节。

    为什么需要这个：`derive build` 是「先 rmtree 重写 24 个派生目录、最后才
    write_manifest()」两步。如果进程在两步之间被打断，磁盘上会留下
    「目录已是新内容、manifest 还是旧引用」的中间态 —— 也就是一批指向不存在
    文件的路径。这种情况下所有决策都已经 noop，`--apply` 会因为没有要裁的
    动作直接退出，坏状态永远修不好。

    所以每次 apply 都先比对一遍；只要脱节就强制重建，不必等新的裁剪。
    """
    if not derivations:
        return []
    entries = {e["id"]: e for e in libs["expansion_v1"].entries}
    actions_dir = libs["expansion_v1"].path.parent / "actions"
    drift: list[dict] = []
    for derived_id, spec in derivations.items():
        entry = entries.get(derived_id)
        if entry is None:
            continue
        source_dir = actions_dir / str(spec.get("source") or "") / "frames"
        count = len(list(source_dir.glob("*.webp"))) if source_dir.is_dir() else 0
        want = predict_derived(str(spec.get("mode") or ""), count)
        frames = entry.get("frames") or []
        problems: list[str] = []
        if want is None:
            if frames:
                problems.append("源目录为空但 manifest 仍有条目")
        elif len(frames) != want[1]:
            problems.append(f"manifest {len(frames)} 帧 ≠ 期望 {want[1]}")
        missing = [ref for ref in frames
                   if not (libs["expansion_v1"].path.parent / ref).is_file()]
        if missing:
            problems.append(f"{len(missing)} 条引用指向不存在的文件")
        frame_dir = actions_dir / derived_id / "frames"
        on_disk = len(list(frame_dir.glob("*.webp"))) if frame_dir.is_dir() else 0
        if want is not None and on_disk != want[0]:
            problems.append(f"目录 {on_disk} 个唯一文件 ≠ 期望 {want[0]}")
        if problems:
            drift.append({"id": derived_id, "problems": problems})
    return drift


def predict_derived_rows(libs: dict[str, Library], trimmed: list[Row],
                         derivations: dict[str, dict]) -> list[dict]:
    """预测源动作被裁后，派生动作会变成多少帧 —— dry-run 也要能看见。

    关键：预测必须基于"裁剪后仍会留在 frames/ 里的文件"，而不是当前磁盘。
    dry-run 阶段多余帧还没搬走，直接 glob 会把旧帧数算进去，结果永远是
    "124 → 124"，等于什么都没预测。
    """
    trimmed_ids = {row.action_id for row in trimmed}
    if not trimmed_ids or not derivations:
        return []
    entries = {e["id"]: e for e in libs["expansion_v1"].entries}
    remaining: dict[str, list[Path]] = {}
    for row in trimmed:
        dropped = set(row.surplus)
        kept: list[Path] = []
        for folder in row.dirs:
            kept.extend(p for p in sorted(folder.glob("*.webp")) if p not in dropped)
        remaining[row.action_id] = kept

    out: list[dict] = []
    for derived_id, spec in derivations.items():
        source = str(spec.get("source") or "")
        if source not in trimmed_ids:
            continue
        files = remaining.get(source, [])
        predicted = predict_derived(str(spec.get("mode") or ""), len(files))
        before_entry = entries.get(derived_id) or {}
        before_frames = len(before_entry.get("frames") or [])
        if predicted is None:
            out.append({"id": derived_id, "source": source,
                        "mode": spec.get("mode"), "before_frames": before_frames,
                        "after_frames": None, "note": "源目录为空，重建会跳过"})
            continue
        unique, refs = predicted
        out.append({
            "id": derived_id,
            "source": source,
            "mode": spec.get("mode"),
            "before_frames": before_frames,
            "after_frames": refs if refs >= 8 else 0,
            "after_unique_files": unique,
            "after_bytes": sum(p.stat().st_size for p in files),
            "note": "" if refs >= 8 else "重建后不足 8 帧，derive 会跳过该条",
        })
    return out


# --------------------------------------------------------------------------- #
# 隔离移动
# --------------------------------------------------------------------------- #
def move_to_isolation(src: Path, dest_dir: Path, dry: bool) -> tuple[bool, int, str]:
    """把一帧移进隔离目录。返回（是否移动, 字节, 备注）。

    os.rename 优先；跨设备（EXDEV）再退化成 copy2 + unlink。
    目标已存在同尺寸文件时视为"上一轮已经隔离过"，不覆盖、不重复计数。
    """
    size = src.stat().st_size if src.exists() else 0
    dest = dest_dir / src.name
    if dest.exists():
        if dest.is_file() and dest.stat().st_size == size:
            return False, 0, "已隔离"
        dest = dest_dir / f"{src.stem}.dup{src.suffix}"
        suffix = 1
        while dest.exists():
            dest = dest_dir / f"{src.stem}.dup{suffix}{src.suffix}"
            suffix += 1
    if dry:
        return True, size, ""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
    except OSError:
        # 跨设备（EXDEV）或目标被占用：退化成 copy2 + unlink，绝不先删源。
        shutil.copy2(src, dest)
        os.unlink(src)
    return True, size, ""


# --------------------------------------------------------------------------- #
# 派生重建
# --------------------------------------------------------------------------- #
def find_repo_root(base: Path) -> Path | None:
    """从 base 往上找带 derive_actions.py 的目录；--base 指到沙盒时也能对上。"""
    for candidate in [base, *base.parents]:
        if (candidate / DERIVE_REL).is_file():
            return candidate
    if (REPO / DERIVE_REL).is_file():
        return REPO
    return None


def run_derive(repo_root: Path, python: Path, script: Path) -> tuple[int, str]:
    cmd = [str(python), str(script), "build"]
    print(f"[derive] {' '.join(cmd)}  (cwd={repo_root})")
    try:
        proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    except OSError as exc:
        return 127, f"无法启动 {python}: {exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    tail = output.strip().splitlines()[-TAIL_LINES:]
    for line in tail:
        print(f"  | {line}")
    return proc.returncode, output


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #
def selfcheck(libs: dict[str, Library], refs: dict[Path, set[str]],
              residual: dict[str, list[str]]) -> list[str]:
    """返回问题列表；空列表即通过。dry-run 查"计划态"，apply 查"磁盘态"。"""
    problems: list[str] = []

    align = 0
    for lib in libs.values():
        for entry in lib.entries:
            frames = entry.get("frames") or []
            duration = entry.get("duration_ms") or []
            if len(frames) != len(duration):
                problems.append(f"{lib.name}/{entry.get('id')}: frames={len(frames)} "
                                f"≠ duration_ms={len(duration)}")
            align += 1

    missing: list[str] = []
    referenced: dict[Path, set[str]] = {}
    for lib in libs.values():
        for entry in lib.entries:
            for ref in entry.get("frames") or []:
                target = norm(lib.path.parent / ref)
                referenced.setdefault(target.parent, set()).add(target.name)
                if not target.is_file():
                    missing.append(f"{lib.name}/{entry.get('id')}: {ref}")
    if missing:
        problems.append(f"{len(missing)} 条引用指向不存在的文件，例：{missing[0]}")

    declared = sum(len(v) for v in referenced.values())
    on_disk = sum(1 for folder, names in referenced.items()
                  for name in names if (folder / name).is_file())
    if on_disk != declared:
        problems.append(f"被引用帧集合不是实际文件集合的子集：{on_disk}/{declared}")

    leaked = [f"{folder}/{name}" for folder, names in referenced.items()
              for name in names if TRIM_DIRNAME in folder.parts]
    if leaked:
        problems.append(f"{len(leaked)} 条引用指进了隔离目录：{leaked[0]}")

    for where, names in residual.items():
        if names:
            problems.append(f"{where} 还残留 {len(names)} 个未被引用的帧：{names[0]}")

    print(f"  对齐检查   {align} 条动作 len(frames)==len(duration_ms)")
    print(f"  引用存在   {declared} 条引用 / 磁盘命中 {on_disk}")
    print(f"  隔离泄漏   {len(leaked)} 条引用落在 {TRIM_DIRNAME}/ 下")
    print(f"  目录残留   {len(residual)} 个目录，残留 "
          f"{sum(len(v) for v in residual.values())} 帧")
    return problems


def disk_residual(base: Path, rows: list[Row], libs: dict[str, Library]) -> dict[str, list[str]]:
    refs = gather_refs(libs, base)
    out: dict[str, list[str]] = {}
    for row in rows:
        for folder in row.dirs:
            keep = refs.get(folder, set())
            extra = sorted(p.name for p in folder.glob("*.webp") if p.name not in keep)
            out[str(folder)] = extra
    return out


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="按决策表裁掉动作尾部帧并隔离多余帧（默认 dry-run）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true", help="真正写盘（默认只打印）")
    parser.add_argument("--base", default=os.environ.get("MIMI_ASSET_BASE") or str(DEFAULT_BASE),
                        help="mimi 素材根目录（也可用环境变量 MIMI_ASSET_BASE）")
    parser.add_argument("--decisions", default=None,
                        help="决策文件，默认 <repo>/docs/expansion/trim_decisions.json")
    parser.add_argument("--out", default=None,
                        help="结果文件，默认 <repo>/docs/expansion/TRIM-APPLIED.json")
    parser.add_argument("--python", default=os.environ.get("MIMI_PYTHON") or None,
                        help="跑 derive_actions.py 的解释器（需 numpy+Pillow）")
    parser.add_argument("--derive-script", default=None, help="derive_actions.py 路径")
    parser.add_argument("--no-derive", action="store_true", help="不重建派生动作")
    parser.add_argument("--json", action="store_true", help="表格之后追加机器可读摘要")
    args = parser.parse_args(argv[1:])

    base = Path(args.base).expanduser().absolute()
    if not (base / LIBRARIES["library_v1"]["manifest"]).is_file():
        print(f"[fatal] --base 里没有 library_v1/manifest.json：{base}", file=sys.stderr)
        return 2

    repo_root = find_repo_root(base) or REPO
    decisions_path = Path(args.decisions).expanduser() if args.decisions \
        else repo_root / "docs/expansion/trim_decisions.json"
    out_path = Path(args.out).expanduser() if args.out else \
        repo_root / "docs/expansion/TRIM-APPLIED.json"
    derive_script = Path(args.derive_script).expanduser() if args.derive_script \
        else repo_root / DERIVE_REL
    if args.python:
        python = Path(args.python).expanduser()
    elif DEFAULT_PYTHON.is_file():
        python = DEFAULT_PYTHON
    else:
        python = Path(sys.executable)
    dry = not args.apply

    print("=" * 78)
    print(f"trim_tails.py   模式：{'DRY-RUN（不写盘）' if dry else 'APPLY（会写盘）'}")
    print(f"  素材根   {base}")
    print(f"  决策表   {decisions_path}")
    print(f"  结果表   {out_path}")
    print(f"  derive   {derive_script}  (python={python})"
          f"{'  [已禁用]' if args.no_derive else ''}")
    print("=" * 78)

    if not decisions_path.is_file():
        print(f"[fatal] 决策文件不存在：{decisions_path}", file=sys.stderr)
        return 2
    try:
        payload = read_json(decisions_path)
    except (OSError, ValueError) as exc:
        print(f"[fatal] 决策文件读不了：{decisions_path}（{exc}）", file=sys.stderr)
        return 2
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        print("[fatal] 决策文件缺少 decisions 数组", file=sys.stderr)
        return 2
    decisions = payload["decisions"]
    if payload.get("schema_version") not in (None, 1):
        print(f"[warn] schema_version={payload.get('schema_version')!r}，按 1 处理")
    if not decisions:
        print("[warn] 决策表是空的，什么都不做")

    libs = {name: Library(name, base) for name in LIBRARIES}
    derivations = {} if args.no_derive or not derive_script.is_file() \
        else load_derivations(derive_script)
    if not args.no_derive and not derivations:
        print(f"[warn] 没能从 {derive_script.name} 解析出 DERIVATIONS，派生预测降级")

    # —— 1. 规划（内存截断） ——
    all_rows, trimmed_rows, error_rows = plan(
        base, decisions, libs, derive_enabled=not args.no_derive)
    compute_surplus(base, libs, trimmed_rows)
    derived_rows = predict_derived_rows(libs, trimmed_rows, derivations)

    # —— 2. 对照表（dry-run 与 apply 完全一致） ——
    table_rows: list[list[str]] = []
    for row in all_rows:
        moved = str(len(row.surplus)) if row.status == "trimmed" else "-"
        if row.status == "skipped":
            frames_cell = f"{row.before_frames} → 保持" if row.before_frames else "-"
            ms_cell = f"{row.before_ms} → 保持" if row.before_frames else "-"
        else:
            frames_cell = f"{row.before_frames} → {row.after_frames}"
            ms_cell = f"{row.before_ms} → {row.after_ms}"
        table_rows.append([
            row.library,
            row.action_id,
            frames_cell,
            ms_cell,
            moved,
            human_bytes(row.freed) if row.status == "trimmed" else "-",
            {"trimmed": "裁掉", "noop": "noop", "skipped": "跳过"}[row.status],
        ])
    total = [
        "合计",
        f"{len(all_rows)} 条决策",
        f"{sum(r.before_frames for r in all_rows)} → "
        f"{sum(r.after_frames for r in all_rows)}",
        f"{sum(r.before_ms for r in all_rows)} → {sum(r.after_ms for r in all_rows)}",
        str(sum(len(r.surplus) for r in trimmed_rows)),
        human_bytes(sum(r.freed for r in trimmed_rows)),
        f"{len(trimmed_rows)} 裁 / {sum(1 for r in all_rows if r.status == 'noop')} noop"
        f" / {len(error_rows)} 跳过",
    ]
    print()
    print(render_table(
        ["库", "动作", "帧数", "毫秒", "移出", "释放", "状态"],
        table_rows + [total],
        ["left", "left", "right", "right", "right", "right", "left"],
    ))

    for row in all_rows:
        if row.status == "skipped":
            print(f"  ! {row.library}/{row.action_id}: {row.note}")
        elif row.status == "noop":
            print(f"  = {row.library}/{row.action_id}: {row.note}")
    reasons = [(row, next((d.get("reason") for d in decisions
                           if d.get("id") == row.action_id
                           and d.get("library") == row.library), "")) for row in trimmed_rows]
    if reasons:
        print("\n决策理由：")
        for row, reason in reasons:
            print(f"  {row.library}/{row.action_id}: {reason or '（未填）'}")

    # —— 3. 派生预测（两种模式一致） ——
    if derived_rows:
        print("\n派生动作（会随源动作重建）：")
        dtable = [[d["id"], str(d["source"]), str(d["mode"]),
                   f"{d['before_frames']} → {d['after_frames']}",
                   human_bytes(d.get("after_bytes") or 0),
                   d.get("note") or ""] for d in derived_rows]
        print(render_table(["派生", "源", "模式", "帧数", "体积", "备注"], dtable,
                           ["left", "left", "left", "right", "right", "left"]))

    changed_anything = bool(trimmed_rows)

    # —— 4. apply：写 manifest → 移帧 → 重建派生 ——
    moved_count = 0
    moved_bytes = 0
    derive_report: dict = {"ran": False, "exit_code": None, "changed": [],
                           "output_tail": [], "note": ""}
    # 即使这一轮全是 noop，也要检查派生态有没有被上一次中断留在半路上。
    drift = [] if (args.no_derive or not derivations) else derived_drift(libs, derivations)
    if drift:
        print(f"\n[drift] {len(drift)} 条派生动作与源目录脱节，需要重建：")
        for item in drift:
            print(f"  ! {item['id']}: {'; '.join(item['problems'])}")
    if not dry and (changed_anything or drift):
        for lib in libs.values():
            if lib.changed:
                lib.save()
                print(f"[write] {lib.path}")

        for row in trimmed_rows:
            dest_dir = base / TRIM_DIRNAME / row.library / row.action_id
            for src in row.surplus:
                ok, size, note = move_to_isolation(src, dest_dir, dry=False)
                if ok:
                    row.moved.append(src.name)
                    row.moved_bytes += size
                    moved_count += 1
                    moved_bytes += size
                elif note:
                    print(f"  [skip] {row.isolation}/{src.name}: {note}")
        print(f"[move] {moved_count} 帧 → {base / TRIM_DIRNAME}（{human_bytes(moved_bytes)}）")

        touched = {row.action_id for row in trimmed_rows}
        builders = [spec for spec in derivations.values() if spec.get("source") in touched]
        if args.no_derive or not derive_script.is_file():
            derive_report["note"] = "已禁用或脚本缺失，跳过重建"
            print(f"[derive] {derive_report['note']}")
        elif not builders and not drift:
            derive_report["note"] = "没有派生源被裁剪，跳过重建"
            print(f"[derive] {derive_report['note']}")
        else:
            code, output = run_derive(repo_root, python, derive_script)
            derive_report.update({
                "ran": True,
                "exit_code": code,
                "changed": [d["id"] for d in derived_rows],
                "output_tail": output.strip().splitlines()[-TAIL_LINES:],
                "note": "" if code == 0 else "derive 非零退出",
            })
            if code != 0:
                print(f"[derive] 失败（exit={code}），manifest 可能不完整")

        # derive 会重写 actions.json，重新读盘再对比预测值
        libs = {name: Library(name, base) for name in LIBRARIES}
        entries = {e["id"]: e for e in libs["expansion_v1"].entries}
        for item in derived_rows:
            entry = entries.get(item["id"])
            actual = len(entry.get("frames") or []) if entry else None
            item["actual_frames"] = actual
            if item["after_frames"] is not None and actual != item["after_frames"]:
                item["note"] = (item.get("note") or "") + \
                    f" | 实际 {actual} ≠ 预测 {item['after_frames']}"
                print(f"  ! {item['id']}: 实际 {actual} 帧，预测 {item['after_frames']} 帧")
    elif not dry:
        print("[apply] 没有需要裁的动作，manifest 与隔离目录都不动")

    # —— 5. 自检（dry-run 查计划态，apply 查磁盘态） ——
    print("\n完整性自检：")
    refs = gather_refs(libs, base)
    if dry:
        # 计划态：被裁目录里的文件要么仍被引用，要么在 surplus 里等着被移走。
        residual: dict[str, list[str]] = {}
        for row in all_rows:
            if not row.dirs:
                continue
            planned = {p.name for p in row.surplus}
            for folder in row.dirs:
                keep = refs.get(folder, set())
                residual[str(folder)] = sorted(
                    p.name for p in folder.glob("*.webp")
                    if p.name not in keep and p.name not in planned)
        problems = selfcheck(libs, refs, residual)
    else:
        problems = selfcheck(libs, refs, disk_residual(base, all_rows, libs))
    for problem in problems:
        print(f"  ✗ {problem}")
    if problems:
        print(f"自检不通过：{len(problems)} 项")
    else:
        print("自检通过")

    # —— 6. 结果文件 ——
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    entries_json = [{
        "library": row.library,
        "id": row.action_id,
        "status": row.status,
        "reason": next((d.get("reason") for d in decisions
                        if d.get("id") == row.action_id
                        and d.get("library") == row.library), ""),
        "note": row.note,
        "keep_frames": row.keep,
        "before_frames": row.before_frames,
        "after_frames": row.after_frames,
        "before_ms": row.before_ms,
        "after_ms": row.after_ms,
        "moved_files": len(row.moved) if not dry else len(row.surplus),
        "freed_bytes": row.moved_bytes if not dry else row.freed,
        "isolation_dir": row.isolation,
        "moved_names": row.moved if not dry else [p.name for p in row.surplus],
    } for row in all_rows]

    result = {
        "schema_version": 1,
        "generated_by": "scripts/pipeline/trim_tails.py",
        "generated_at": now,
        "mode": "dry-run" if dry else "apply",
        "base": str(base),
        "decisions_file": str(decisions_path),
        "decisions_count": len(decisions),
        "summary": {
            "trimmed": len(trimmed_rows),
            "noop": sum(1 for r in all_rows if r.status == "noop"),
            "skipped": len(error_rows),
            "frames_before": sum(r.before_frames for r in all_rows),
            "frames_after": sum(r.after_frames for r in all_rows),
            "ms_before": sum(r.before_ms for r in all_rows),
            "ms_after": sum(r.after_ms for r in all_rows),
            "moved_files": moved_count if not dry else sum(len(r.surplus) for r in trimmed_rows),
            "freed_bytes": moved_bytes if not dry else sum(r.freed for r in trimmed_rows),
            "isolation_dir": str(base / TRIM_DIRNAME),
        },
        "entries": entries_json,
        "derived_rebuild": {**derive_report, "drift_found": drift, "derived": derived_rows},
        "selfcheck": {"ok": not problems, "problems": problems},
    }

    if dry:
        print(f"\n[dry-run] 未写盘；--apply 会写 {out_path.name} 并移动上面列出的帧")
    else:
        if changed_anything or not out_path.is_file():
            write_json(out_path, result)
            print(f"\n[write] {out_path}")
        else:
            print(f"\n[write] 没有变化，保留原 {out_path.name}")

    if args.json:
        print("\n" + json.dumps({
            "mode": result["mode"],
            "summary": result["summary"],
            "derived_rebuild": {"ran": derive_report["ran"],
                                "exit_code": derive_report["exit_code"],
                                "note": derive_report["note"]},
            "selfcheck": result["selfcheck"],
        }, ensure_ascii=False, indent=2))

    if problems:
        return 1
    if error_rows:
        print(f"[warn] {len(error_rows)} 条决策因契约错误被跳过", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
