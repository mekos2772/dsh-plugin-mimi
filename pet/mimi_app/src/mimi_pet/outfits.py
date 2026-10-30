"""预设造型目录：加载、校验、持久化当前选择。

Qt-free on purpose: the engine resolves an action id to concrete frames through
this module, so it must stay usable from the domain layer and from tests.

设计边界（见 docs/expansion/ROADMAP.md）：
* 只做“整套造型”，不做单件混搭。
* 造型分 display / daily 两级；display 只有待机循环，不参与走路/拖拽。
* 造型缺帧时回退默认造型，绝不把旧衣服的帧冒充新造型。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .action_library import _abs_path
from .debug_log import dbg

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class OutfitSpec:
    """One complete preset look."""

    id: str
    name_zh: str
    description: str
    mode: str                      # "display" | "daily"
    frames: tuple[Path, ...]
    duration_ms: tuple[int, ...]
    loop: bool
    canvas: tuple[int, int]
    ground_pivot: tuple[int, int]
    #: 语义动作 id → 该造型自己的帧序列。缺项表示这套衣服没有这个动作。
    action_map: dict[str, tuple[Path, ...]] = field(default_factory=dict)
    #: 拖拽受力档位 → 该造型自己的帧。缺档时拖拽回退默认外观。
    drag_map: dict[str, tuple[Path, ...]] = field(default_factory=dict)

    @property
    def total_duration_ms(self) -> int:
        return sum(self.duration_ms)

    #: 日常套装必须具备的动作。缺一个就不该当成能穿出门的完整套装。
    #: 出门必需：能走、能被摸、能有基本反馈、落地不卡。
    REQUIRED_ACTIONS = (
        "walk_right", "head_pat", "cheek_poke", "celebrate", "land_recover",
    )

    @property
    def is_daily(self) -> bool:
        return self.mode == "daily" and self.missing_actions() == ()

    @property
    def supported_actions(self) -> tuple[str, ...]:
        return tuple(sorted(self.action_map))

    def missing_actions(self) -> tuple[str, ...]:
        """日常套装还缺哪些必要动作。展示造型不参与这个判定。"""
        if self.mode != "daily":
            return ()
        return tuple(a for a in self.REQUIRED_ACTIONS if a not in self.action_map)

    def supports(self, action_id: str) -> bool:
        return action_id in self.action_map

    def drag_frames(self, tier: str) -> tuple[Path, ...]:
        """该造型在某一档受力的帧；没有就返回空。"""
        return self.drag_map.get(tier, ())

    @property
    def has_drag(self) -> bool:
        return bool(self.drag_map)


@dataclass
class OutfitSelection:
    """Persisted choice; ``id`` empty means the built-in default look."""

    id: str = ""
    revision: int = 0
    store_error: str = ""
    _extra: dict = field(default_factory=dict)


def default_store_path() -> Path:
    override = os.environ.get("MIMI_OUTFIT_STATE_PATH")
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    base = (
        Path(appdata) / "MimiDesktopPet"
        if appdata
        else Path.home() / ".mimi-desktop-pet"
    )
    return base / "outfit.json"


class OutfitCatalog:
    """Strict allowlist of preset looks; never scans arbitrary folders."""

    def __init__(self, manifest_path: Path | str) -> None:
        self.manifest_path = Path(manifest_path).resolve()
        self.root = self.manifest_path.parent
        self._outfits: dict[str, OutfitSpec] = {}
        self.load_error = ""
        self._load()

    def _load(self) -> None:
        if not self.manifest_path.is_file():
            self.load_error = "造型目录不存在，使用默认外观。"
            return
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.load_error = "造型目录无法读取，使用默认外观。"
            dbg(f"outfit manifest unreadable: {exc}")
            return
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            self.load_error = "造型目录版本不兼容，使用默认外观。"
            return
        for raw in data.get("presets", []):
            spec = self._parse(raw)
            if spec is not None:
                self._outfits[spec.id] = spec
        if not self._outfits:
            self.load_error = "造型目录为空，使用默认外观。"

    def _parse(self, raw: dict) -> OutfitSpec | None:
        """Validate one entry; a bad look is skipped, never fatal."""
        try:
            if raw.get("status") != "usable" or raw.get("enabled") is not True:
                return None
            frames = tuple(_abs_path(self.root, value) for value in raw["frames"])
            durations = tuple(int(value) for value in raw["duration_ms"])
            if not frames or len(frames) != len(durations):
                dbg(f"outfit {raw.get('id')}: frame/duration count mismatch")
                return None
            if any(value <= 0 for value in durations):
                dbg(f"outfit {raw.get('id')}: non-positive duration")
                return None
            # 只检查文件存在；解码由启动资产检查负责，避免领域层依赖 Qt。
            missing = [path for path in frames if not path.is_file()]
            if missing:
                dbg(f"outfit {raw.get('id')}: missing {len(missing)} frames")
                return None
            mode = str(raw.get("mode", "display"))
            if mode not in ("display", "daily"):
                mode = "display"
            action_map: dict[str, tuple[Path, ...]] = {}
            for action_id, entry in (raw.get("actions") or {}).items():
                paths = tuple(_abs_path(self.root, value) for value in entry.get("frames", []))
                durations = tuple(int(v) for v in entry.get("duration_ms", []))
                if not paths or len(paths) != len(durations):
                    dbg(f"outfit {raw.get('id')}/{action_id}: count mismatch")
                    continue
                if any(not path.is_file() for path in paths):
                    dbg(f"outfit {raw.get('id')}/{action_id}: missing frames")
                    continue
                action_map[str(action_id)] = paths
            drag_map: dict[str, tuple[Path, ...]] = {}
            for tier, entry in (raw.get("drag") or {}).items():
                paths = tuple(_abs_path(self.root, value) for value in entry.get("frames", []))
                durations = tuple(int(v) for v in entry.get("duration_ms", []))
                if not paths or len(paths) != len(durations):
                    dbg(f"outfit {raw.get('id')}/drag {tier}: count mismatch")
                    continue
                if any(not path.is_file() for path in paths):
                    dbg(f"outfit {raw.get('id')}/drag {tier}: missing frames")
                    continue
                drag_map[str(tier)] = paths
            return OutfitSpec(
                id=str(raw["id"]),
                name_zh=str(raw.get("name_zh", raw["id"])),
                description=str(raw.get("description", "")),
                mode=mode,
                frames=frames,
                duration_ms=durations,
                loop=bool(raw.get("loop", True)),
                canvas=tuple(int(v) for v in raw.get("canvas", [512, 768])),
                ground_pivot=tuple(int(v) for v in raw.get("ground_pivot", [256, 744])),
                action_map=action_map,
                drag_map=drag_map,
            )
        except (KeyError, TypeError, ValueError) as exc:
            dbg(f"outfit entry rejected: {exc}")
            return None

    # ------------------------------------------------------------- queries

    def get(self, outfit_id: str) -> OutfitSpec | None:
        return self._outfits.get(outfit_id)

    def presets(self) -> tuple[OutfitSpec, ...]:
        return tuple(self._outfits.values())

    def __len__(self) -> int:
        return len(self._outfits)

    def asset_paths(self) -> list[Path]:
        return [path for spec in self._outfits.values() for path in spec.frames]


class OutfitService:
    """Owns the remembered look and the display-mode decision."""

    def __init__(self, catalog: OutfitCatalog, store_path: Path | str | None = None) -> None:
        self.catalog = catalog
        self.store_path = Path(store_path) if store_path else default_store_path()
        self.selection = OutfitSelection()
        self._preview_id = ""
        self._load()

    # -------------------------------------------------------------- state

    def _load(self) -> None:
        if not self.store_path.is_file():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.selection.store_error = "已保存的造型记录无法读取，已回到默认外观。"
            dbg(f"outfit store unreadable: {exc}")
            return
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            self.selection.store_error = "造型记录版本不兼容，已回到默认外观。"
            return
        outfit_id = str(data.get("outfit_id", ""))
        if outfit_id and self.catalog.get(outfit_id) is None:
            # 保留偏好但不应用：造型可能被卸载，用户仍能看到它曾经的选择。
            self.selection.store_error = "选择的造型已不可用，已回到默认外观。"
            self.selection.id = ""
            self.selection._extra["missing"] = outfit_id
            return
        self.selection.id = outfit_id

    def current(self) -> OutfitSpec | None:
        """Active look, or None for the built-in default appearance."""
        if not self.selection.id:
            return None
        return self.catalog.get(self.selection.id)

    def is_display_mode(self) -> bool:
        spec = self.current()
        return bool(spec and not spec.is_daily)

    def preview(self, outfit_id: str) -> tuple[bool, str]:
        """试穿：只切换画面，不落盘、不改记住的选择。

        用户取消或重启后回到原造型，避免"随手点一下就被永久改掉"。
        """
        spec = self.catalog.get(outfit_id) if outfit_id else None
        if outfit_id and spec is None:
            return False, "这个造型已不可用。"
        self._preview_id = outfit_id
        return True, (f"试穿「{spec.name_zh}」。" if spec else "试回默认造型。")

    def cancel_preview(self) -> tuple[bool, str]:
        if not getattr(self, "_preview_id", ""):
            return False, "当前没有在试穿。"
        self._preview_id = ""
        return True, "已恢复原来的造型。"

    @property
    def preview_id(self) -> str:
        return getattr(self, "_preview_id", "")

    def preview_outfit(self) -> OutfitSpec | None:
        """试穿中的造型；None 表示没有试穿或试穿默认造型。"""
        pid = getattr(self, "_preview_id", "")
        if not pid:
            return None
        return self.catalog.get(pid)

    def effective(self) -> OutfitSpec | None:
        """当前应该显示哪套：试穿优先。"""
        if getattr(self, "_preview_id", None) is not None and self._preview_id:
            return self.catalog.get(self._preview_id)
        return self.current()

    def select(self, outfit_id: str) -> tuple[bool, str]:
        """Switch look. Returns ``(ok, message)``; never raises for bad input."""
        self._preview_id = ""
        if not outfit_id:
            self.selection.id = ""
            return self._save("已恢复默认造型。")
        spec = self.catalog.get(outfit_id)
        if spec is None:
            return False, "这个造型已不可用，请重新选择。"
        self.selection.id = outfit_id
        note = f"已换上「{spec.name_zh}」。"
        if spec.is_daily:
            note += f"可做 {len(spec.supported_actions)} 个动作。"
        else:
            missing = spec.missing_actions()
            if missing:
                note += f"这是展示造型，还缺 {len(missing)} 个日常动作，暂不走路。"
            else:
                note += "这是展示造型，走路和拖拽会先用静态方式呈现。"
        return self._save(note)

    def _save(self, ok_message: str) -> tuple[bool, str]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "outfit_id": self.selection.id,
        }
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.store_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.store_path)
            self.selection.store_error = ""
            return True, ok_message
        except OSError as exc:
            dbg(f"outfit save failed: {exc}")
            self.selection.store_error = "造型已切换，但没能保存；重启后会恢复上一次的选择。"
            return True, ok_message + "\n" + self.selection.store_error
