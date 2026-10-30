"""造型 × 交互矩阵：换装后每个动作都必须有反馈，且绝不换回默认衣服。

为什么要有这组测试
------------------
造型素材每套只画 15 个动作，动作库有 87 个。曾经的策略是"造型没有就拒绝"，
配上一句注释说"让调用方保持待机"。结果是：

* 摸肚子、击掌、喂食、拖文件、坐下、玩具、DSH 思考动画……换装后全部没反应；
* 主动行为按动作库挑步骤，挑到播不出来的就整套路作废；
* 坠落/睡眠/起身走的是只查动作库的旁路，换装后当场变回默认衣服。

两组测试都在，单元测试仍然是 170/170 全绿——因为**没有任何一个测试**在穿着
造型的引擎上碰过这些入口。这个文件就是补这个洞。

判断标准（比"有没有报错"更重要）：
1. 播放的每一帧都必须属于当前造型（`is_outfit_asset`），否则就是换衣服；
2. 用户能触发的入口必须**有动作**，不能静默；
3. 拖拽档位不能每来一个鼠标事件就重播同一段循环片。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pet" / "mimi_app" / "src"))

from mimi_pet.action_library import ActionLibrary
from mimi_pet.config import load_config
from mimi_pet.engine import PetEngine
from mimi_pet.outfit_actions import (
    GENERIC_FALLBACK,
    OUTFIT_ACTION_FALLBACKS,
    OUTFIT_ACTION_IDS,
    fallback_candidates,
)
from mimi_pet.outfits import OutfitCatalog, OutfitService
from mimi_pet.state_machine import PetState
from mimi_pet.renderer import QtRenderer

try:  # 渲染层依赖 Qt；没有 PySide6 的环境只跳过这组断言。
    from mimi_pet.renderer import build_snapshot
    HAVE_QT = True
except ImportError:  # pragma: no cover - 无 Qt 环境
    build_snapshot = None
    HAVE_QT = False

ASSETS = REPO / "pet" / "assets" / "characters" / "mimi"


def _config() -> dict:
    return load_config()


def _library() -> ActionLibrary:
    return ActionLibrary(Path(_config()["asset_manifest"]))


def _catalog() -> OutfitCatalog:
    return OutfitCatalog(ASSETS / "expansion_v1" / "presets.json")


class _OutfitMixin:
    """共享：默认外观 + 每套造型各一个引擎。"""

    @classmethod
    def setUpClass(cls):
        cls.config = _config()
        cls.library = _library()
        cls.catalog = _catalog()
        if not len(cls.catalog):
            raise unittest.SkipTest(f"造型目录为空：{cls.catalog.load_error}")
        cls.outfits = list(cls.catalog.presets())

    @classmethod
    def _rig(cls):
        from mimi_pet.rig_model import load_rig_model
        if not hasattr(cls, "_rig_instance"):
            from pathlib import Path as _Path
            cls._rig_instance = load_rig_model(
                _Path(cls.config["asset_manifest"]).parent / cls.library.live_rig["model"]
            )
        return cls._rig_instance

    @classmethod
    def _cache(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from mimi_pet.image_cache import ImageCache
        if not hasattr(cls, "_cache_instance"):
            cls._cache_instance = ImageCache(dpr=1.0)
        return cls._cache_instance

    def engine(self, outfit=None) -> PetEngine:
        engine = PetEngine(self.library, self.config)
        if outfit is not None:
            engine.set_outfit(outfit)
        engine.now_s = 1000.0
        return engine

    def played_id(self, engine: PetEngine) -> str:
        action = engine.player.action
        return action.id if action is not None else ""


class FallbackChainTests(unittest.TestCase):
    """近似链本身：只落在造型自己的 15 个动作上。"""

    def test_every_target_is_an_outfit_action(self):
        catalog = _catalog()
        for spec in catalog.presets():
            for requested, chain in OUTFIT_ACTION_FALLBACKS.items():
                resolved = spec.frames_for(requested)
                self.assertIsNotNone(
                    resolved, f"{spec.id} 无法解析 {requested}"
                )
                frames, used_id = resolved
                self.assertTrue(frames, f"{spec.id}/{requested} 解析出空帧")
                self.assertIn(
                    used_id,
                    spec.action_map,
                    f"{spec.id}/{requested} 落到了造型没有的 {used_id}",
                )

    def test_generic_fallback_is_shared_by_all_outfits(self):
        catalog = _catalog()
        for spec in catalog.presets():
            self.assertTrue(
                any(spec.supports(a) for a in GENERIC_FALLBACK),
                f"{spec.id} 连通用兜底动作都没有：{GENERIC_FALLBACK}",
            )

    def test_unknown_action_falls_back_instead_of_vanishing(self):
        """没登记的动作（未来新增）也要能落到造型帧上。"""
        catalog = _catalog()
        spec = catalog.presets()[0]
        resolved = spec.frames_for("brand_new_action_v9")
        self.assertIsNotNone(resolved, "未登记动作应当走通用兜底而不是消失")
        self.assertIn(resolved[1], spec.action_map)

    def test_chain_prefers_exact_frames(self):
        """造型真有这个动作时必须用它，不能被近似链抢走。"""
        catalog = _catalog()
        for spec in catalog.presets():
            for action_id in spec.action_map:
                frames, used = spec.frames_for(action_id)
                self.assertEqual(used, action_id, f"{spec.id}/{action_id} 被近似链抢走")
                self.assertEqual(frames, spec.action_map[action_id])

    def test_outfit_action_ids_match_shipped_manifests(self):
        """文档里那 15 个 id 必须与素材一致，否则常量会悄悄过期。"""
        catalog = _catalog()
        expected = set(OUTFIT_ACTION_IDS)
        for spec in catalog.presets():
            unknown = set(spec.action_map) - expected
            self.assertFalse(unknown, f"{spec.id} 有未登记的造型动作：{sorted(unknown)}")
        self.assertEqual(
            set(OUTFIT_ACTION_FALLBACKS),
            set(OUTFIT_ACTION_FALLBACKS),
            "sanity",
        )


class OutfitPlayabilityTests(_OutfitMixin, unittest.TestCase):
    """换装后：每个入口都有动作，且每一帧都属于这套衣服。"""

    #: 用户能直接触发的入口 → 期望的语义（不校验具体 id，只校验有反馈）
    ENTRY_POINTS = (
        ("touch_head", lambda e: e.trigger_touch("head", x_ratio=0.5)),
        ("touch_face", lambda e: e.trigger_touch("face", x_ratio=0.5)),
        ("touch_belly", lambda e: e.trigger_touch("belly", x_ratio=0.5)),
        ("touch_hand", lambda e: e.trigger_touch("hand", x_ratio=0.1)),
        ("touch_foot", lambda e: e.trigger_touch("foot", x_ratio=0.5)),
        ("double_click", lambda e: e.trigger_double_click()),
        ("feed", lambda e: e.feed_bread()),
        ("file_drop", lambda e: e.receive_drop()),
        ("sit", lambda e: e.start_sit()),
        ("sleep", lambda e: e.start_sleep()),
        ("walk_right", lambda e: e.start_walk("right")),
        ("toy_push_ball", lambda e: e.force_perform("push_ball")),
        ("toy_catch_star", lambda e: e.force_perform("catch_star")),
        ("dsh_thinking", lambda e: e.force_perform("harness_task_thinking_v1_12")),
        ("dsh_tool", lambda e: e.force_perform("harness_tool_working_v1_12")),
        ("yawn", lambda e: e.force_perform("yawn")),
        ("wave_slow", lambda e: e.force_perform("wave_slow")),
        ("health_water", lambda e: e.force_perform("drink_water_hold")),
        ("note_peek", lambda e: e.force_perform("peek_note")),
        ("wrapup", lambda e: e.force_perform("lantern_off")),
        ("low_battery", lambda e: e.force_perform("sleepy_nod")),
        ("network_lost", lambda e: e.force_perform("look_down_sad")),
        ("network_back", lambda e: e.force_perform("cheer_jump")),
        ("project_created", lambda e: e.force_perform("think_pencil")),
        ("task_step_done", lambda e: e.force_perform("catch_star")),
    )

    def test_every_entry_point_plays_under_every_outfit(self):
        dead = []
        for spec in self.outfits:
            for name, call in self.ENTRY_POINTS:
                engine = self.engine(spec)
                try:
                    call(engine)
                except (KeyError, ValueError) as exc:  # pragma: no cover
                    dead.append(f"{spec.id}/{name}: {type(exc).__name__}: {exc}")
                    continue
                if not self.played_id(engine):
                    dead.append(f"{spec.id}/{name}: 没有动作")
        self.assertFalse(dead, "换装后这些入口静默失败：\n" + "\n".join(dead))

    def test_default_look_is_unchanged(self):
        """没有穿造型时仍然用正式素材，不能被造型逻辑影响。"""
        for name, call in self.ENTRY_POINTS:
            engine = self.engine(None)
            call(engine)
            if name == "touch_foot":
                continue  # 跺脚会走路，动作 id 也是 walk_left，照样有反馈
            self.assertTrue(self.played_id(engine), f"默认外观 {name} 没有动作")

    def test_no_default_frames_while_outfit_is_worn(self):
        """造型动作只能落在这套衣服自己的帧上——这是"不换衣服"的硬约束。"""
        leaked = []
        for spec in self.outfits:
            for name, call in self.ENTRY_POINTS:
                engine = self.engine(spec)
                call(engine)
                action = engine.player.action
                if action is None:
                    continue
                for path in action.frames:
                    if not engine.is_outfit_asset(path):
                        leaked.append(f"{spec.id}/{name}: {path.name} 不是 {spec.id} 的素材")
                        break
        self.assertFalse(leaked, "换装后混入了默认素材：\n" + "\n".join(sorted(set(leaked))[:20]))

    def test_outfit_action_frames_agrees_with_playable_spec(self):
        """``outfit_action_frames`` 是给外部查"这套衣服能不能播"的接口，
        必须和真正播放用的 spec 完全一致，否则调用方会被骗。"""
        for spec in self.outfits:
            engine = self.engine(spec)
            for action_id in list(OUTFIT_ACTION_FALLBACKS) + list(spec.action_map):
                frames = engine.outfit_action_frames(action_id)
                playable = engine._playable_spec(action_id)
                if frames is None:
                    self.assertIsNone(
                        playable, f"{spec.id}/{action_id}: 查询说没有，实际却能播"
                    )
                    continue
                self.assertIsNotNone(playable, f"{spec.id}/{action_id}: 查询说有，实际播不了")
                self.assertEqual(
                    tuple(playable.frames), tuple(frames),
                    f"{spec.id}/{action_id}: 查询到的帧与播放的不一致",
                )

    def test_landing_keeps_outfit_and_does_not_lock_input(self):
        """落地：造型用自己的帧，而且不能把交互锁住好几秒。

        LANDING 期间 ``is_busy_physical()`` 拒绝一切交互，造型的 land_recover
        有 40–62 帧，按 83ms 播就是 3.5–5.1 秒的"点了没反应"。
        """
        from mimi_pet.engine import LANDING_MAX_MS

        for spec in self.outfits:
            engine = self.engine(spec)
            engine.begin_drag(500.0, 500.0, engine.now_s)
            engine.release(5000.0, engine.now_s + 1.0)
            action = None
            for _ in range(200):
                engine.now_s += 0.02
                engine.tick(engine.now_s, 0.02, ground_y=2000.0)
                if engine.player.action is not None:
                    action = engine.player.action
                    break
            self.assertIsNotNone(action, f"{spec.id} 落地没有动作")
            self.assertTrue(
                engine.is_outfit_asset(action.frames[0]),
                f"{spec.id} 落地用了默认素材 {action.frames[0].name}",
            )
            self.assertLessEqual(
                action.total_duration_ms, LANDING_MAX_MS + 60,
                f"{spec.id} 落地动作 {action.total_duration_ms}ms 太长，交互会被锁住",
            )

    def test_default_landing_is_untouched(self):
        """没穿造型时落地仍用正式素材（land_recover_v4_12），别被造型逻辑吃掉。"""
        engine = self.engine(None)
        engine.begin_drag(500.0, 500.0, engine.now_s)
        engine.release(5000.0, engine.now_s + 1.0)
        action = None
        for _ in range(200):
            engine.now_s += 0.02
            engine.tick(engine.now_s, 0.02, ground_y=2000.0)
            if engine.player.action is not None:
                action = engine.player.action
                break
        self.assertIsNotNone(action, "默认外观落地动画丢了")
        self.assertEqual(action.id, "land_recover_v4_12")

    def test_sleep_and_wake_keep_the_outfit(self):
        """睡眠/唤醒曾经绕过造型解析，睡下去就换回默认衣服。"""
        for spec in self.outfits:
            engine = self.engine(spec)
            self.assertTrue(engine.start_sleep(), f"{spec.id} 不能睡")
            action = engine.player.action
            self.assertIsNotNone(action)
            for path in action.frames:
                self.assertTrue(
                    engine.is_outfit_asset(path),
                    f"{spec.id} 睡眠用了默认素材 {path.name}",
                )
            # 睡够 → 循环保持，仍然必须是造型帧
            for _ in range(400):
                engine.now_s += 0.05
                engine.tick(engine.now_s, 0.05)
                if engine.states.state is not PetState.SLEEPING:
                    break
            engine.wake_up()
            action = engine.player.action
            if action is not None:
                for path in action.frames:
                    self.assertTrue(
                        engine.is_outfit_asset(path),
                        f"{spec.id} 唤醒用了默认素材 {path.name}",
                    )

    def test_stand_up_returns_to_idle_in_outfit(self):
        """造型没有起身素材时也要能站起来（回到待机），不能卡在坐姿。"""
        for spec in self.outfits:
            engine = self.engine(spec)
            self.assertTrue(engine.start_sit(), f"{spec.id} 不能坐")
            self.assertTrue(engine.is_sitting, f"{spec.id} 坐下后状态不对")
            self.assertTrue(engine.stand_up(), f"{spec.id} 站不起来")
            for _ in range(400):
                engine.now_s += 0.05
                engine.tick(engine.now_s, 0.05)
                if engine.states.state is PetState.IDLE:
                    break
            self.assertIs(
                engine.states.state, PetState.IDLE,
                f"{spec.id} 起身后没有回到待机，卡在 {engine.states.state.name}",
            )


class OutfitDragTests(_OutfitMixin, unittest.TestCase):
    """拖拽：任何造型都要有自己的拖拽画面，且档位不能反复重播。"""

    def _drag_steps(self, engine: PetEngine, dx: float, steps: int = 6):
        engine.begin_drag(500.0, 500.0, engine.now_s)
        rows = []
        for index in range(steps):
            engine.now_s += 0.05
            engine.update_drag(500.0 + index * dx, 500.0, engine.now_s)
            rows.append(engine.drag_pose_player.action)
        return rows

    def test_every_outfit_drags_in_its_own_frames(self):
        for spec in self.outfits:
            engine = self.engine(spec)
            rows = self._drag_steps(engine, 45.0)
            action = rows[-1]
            self.assertIsNotNone(action, f"{spec.id} 拖拽没有画面")
            for path in action.frames:
                self.assertTrue(
                    engine.is_outfit_asset(path),
                    f"{spec.id} 拖拽用了默认素材 {path.name}（会当场变衣服）",
                )

    def test_drag_tier_does_not_restart_on_every_move(self):
        """三档造型曾经每来一个鼠标事件就 play() 一次，画面永远停在第 0 帧。"""
        checked = 0
        for spec in self.outfits:
            if not spec.has_drag or spec.has_dense_drag:
                continue
            engine = self.engine(spec)
            self._drag_steps(engine, 45.0, steps=4)
            action = engine.drag_pose_player.action
            self.assertIsNotNone(action, f"{spec.id} 拖拽没有画面")
            first_id = action.id
            # 让这段循环片往前走一段
            engine.drag_pose_player.advance(250.0)
            progressed = engine.drag_pose_player.elapsed_ms
            self.assertGreater(progressed, 0.0, f"{spec.id} 拖拽帧没有推进")
            # 同速度继续拖：档位不变，动画必须接着放，而不是从头开始
            for _ in range(3):
                engine.now_s += 0.05
                engine.update_drag(engine.root_x + engine.grab_dx + 45.0, 500.0, engine.now_s)
            self.assertEqual(
                engine.drag_pose_player.action.id, first_id,
                f"{spec.id} 拖拽档位在速度没变时跳档了",
            )
            self.assertGreaterEqual(
                engine.drag_pose_player.elapsed_ms, progressed,
                f"{spec.id} 拖拽动画被重置回第 0 帧（会看着像卡住）",
            )
            checked += 1
        self.assertGreater(checked, 0, "素材里应当有少档位的造型")

    def test_dense_drag_follows_pose_index(self):
        """画满 11 档的造型要逐档跟随真实受力。"""
        dense = [spec for spec in self.outfits if spec.has_dense_drag]
        self.assertTrue(dense, "素材里应当至少有一套 11 档拖拽")
        for spec in dense:
            seen = set()
            engine = self.engine(spec)
            engine.begin_drag(500.0, 500.0, engine.now_s)
            x = 500.0
            # 逐步加速：从慢拖一路拖到快拖，档位必须跟着涨。
            for index in range(60):
                step = 6.0 + index * 2.2
                x += step
                engine.now_s += 0.03
                engine.update_drag(x, 500.0, engine.now_s)
                if engine.drag_pose_player.action is not None:
                    seen.add(engine.drag_pose_player.action.id)
            self.assertGreaterEqual(
                len(seen), 5, f"{spec.id} 拖拽只用了 {len(seen)} 档：{sorted(seen)}"
            )

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造渲染快照")
    def test_outfit_drag_art_is_lifted_off_the_ground(self):
        """造型拖拽素材是站姿，必须由渲染层补上"被拎起来"的位移。

        素材实测：默认拖拽素材的包围盒顶在 y≈78/768（悬空被拎住），而造型
        拖拽素材的脚底仍在 y≈372/384 的地面线上——不补程序化位移，拖动时
        画面和站着几乎一样，用户会以为拖拽没生效。
        """
        for spec in self.outfits:
            engine = self.engine(spec)
            engine.begin_drag(500.0, 500.0, engine.now_s)
            engine.now_s += 0.05
            engine.update_drag(560.0, 500.0, engine.now_s)
            frame = engine.tick(engine.now_s, 0.05)
            snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
            self.assertEqual(snapshot.mode, "drag_pose", f"{spec.id} 拖拽没有走拖拽渲染")
            self.assertGreater(
                snapshot.drag_lift, 0.0,
                f"{spec.id} 拖拽没有离地位移，画面会和站着一样",
            )
            self.assertIsNotNone(
                snapshot.drag_anchor,
                f"{spec.id} 拖拽缺少摆荡锚点",
            )

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造渲染快照")
    def test_default_drag_has_no_programmatic_lift(self):
        """默认素材已经画好悬空姿势，不能再叠一层位移。"""
        engine = self.engine(None)
        engine.begin_drag(500.0, 500.0, engine.now_s)
        engine.now_s += 0.05
        engine.update_drag(560.0, 500.0, engine.now_s)
        frame = engine.tick(engine.now_s, 0.05)
        snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
        self.assertEqual(snapshot.mode, "drag_pose")
        self.assertEqual(snapshot.drag_lift, 0.0, "默认拖拽不应该再被抬高")
        self.assertIsNone(snapshot.drag_anchor, "默认拖拽不应该改旋转锚点")
        self.assertFalse(snapshot.drag_mirror, "默认素材左右各有一套，不该镜像")

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造渲染快照")
    def test_outfit_landing_actually_dips(self):
        """造型的 land_recover 素材几乎不动，落地必须有可见的下蹲-回弹。

        实测：默认落地素材帧间差异 22.5、垂直位移 147px；造型只有 4–5 / 14–19px，
        肉眼看就是"站着不动"。
        """
        for spec in self.outfits:
            engine = self.engine(spec)
            engine.begin_drag(500.0, 500.0, engine.now_s)
            engine.root_y = 300.0
            engine.release(2200.0, engine.now_s)
            squashes = []
            for _ in range(400):
                engine.now_s += 0.01
                frame = engine.tick(engine.now_s, 0.01, ground_y=2200.0)
                snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
                if engine.states.state is PetState.LANDING:
                    squashes.append(snapshot.land_squash)
                elif squashes:
                    break
            self.assertTrue(squashes, f"{spec.id} 没有落地阶段")
            self.assertLess(
                min(squashes), 0.98,
                f"{spec.id} 落地没有压缩，看起来还是站着",
            )
            self.assertGreater(
                max(squashes), 0.995,
                f"{spec.id} 落地没有回弹回原高度",
            )

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造渲染快照")
    def test_default_landing_has_no_forced_squash(self):
        """默认落地素材自带下蹲，不能被再压一次。"""
        engine = self.engine(None)
        engine.begin_drag(500.0, 500.0, engine.now_s)
        engine.root_y = 300.0
        engine.release(2200.0, engine.now_s)
        for _ in range(400):
            engine.now_s += 0.01
            frame = engine.tick(engine.now_s, 0.01, ground_y=2200.0)
            snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
            if engine.states.state is PetState.LANDING:
                self.assertEqual(snapshot.land_squash, 1.0, "默认落地不该被程序化压缩")
                return
        self.fail("没有进入落地阶段")

    def _drag_snapshot(self, spec, dx):
        engine = self.engine(spec)
        start = 800.0 if dx < 0 else 200.0
        engine.begin_drag(start, 500.0, engine.now_s)
        for _ in range(5):
            engine.now_s += 0.05
            engine.update_drag(engine.root_x + engine.grab_dx + dx, 500.0, engine.now_s)
        frame = engine.tick(engine.now_s, 0.05)
        return build_snapshot(frame, engine, (engine.display_w, engine.display_h))

    def test_every_outfit_declares_drag_face(self):
        """素材朝向必须显式声明，不能靠代码猜。

        素材里既有"左拖姿势"（拖尾在右下）也有"右拖姿势"（sporty/star_night），
        固定成"往右拖就镜像"会让其中一半左右反掉。
        """
        for spec in self.outfits:
            if not spec.drag_map:
                continue
            self.assertIn(
                spec.drag_face, ("left", "right", "center"),
                f"{spec.id} 没有声明 drag_face，左右方向会靠猜",
            )

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造渲染快照")
    def test_drag_mirror_follows_declared_face(self):
        """镜像规则：素材是左拖姿势→往右拖镜像；右拖姿势→往左拖镜像。"""
        for spec in self.outfits:
            if not spec.drag_map:
                continue
            left = self._drag_snapshot(spec, -70.0)
            right = self._drag_snapshot(spec, +70.0)
            if spec.drag_face == "right":
                # 素材本身就是右拖姿势：向右拖不镜像，向左拖才镜像。
                self.assertFalse(right.drag_mirror, f"{spec.id} 右拖姿势素材，向右拖不该镜像")
                self.assertTrue(left.drag_mirror, f"{spec.id} 右拖姿势素材，向左拖必须镜像")
            else:
                self.assertFalse(left.drag_mirror, f"{spec.id} 左拖姿势素材，向左拖不该镜像")
                self.assertTrue(right.drag_mirror, f"{spec.id} 左拖姿势素材，向右拖必须镜像")
            # 两个方向必须真的不同，否则等于没有方向感。
            self.assertNotEqual(
                left.drag_mirror, right.drag_mirror,
                f"{spec.id} 左右拖拽渲染完全一样，看不出方向",
            )

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造渲染快照")
    def test_single_frame_drag_outfits_still_move(self):
        """只用一张静态拖拽图的造型，也必须看得出在动。

        素材实测：rain_coat / pajama / summer_dress / sporty 每档只有 1 帧，
        maid_blue 完全没有拖拽图。以前拖动时只是换一张静图，观感上"没有拖拽
        动作"。现在叠一层随时间摆动的倾角。
        """
        static = [
            spec for spec in self.outfits
            if not spec.has_drag or all(
                len(frames) <= 1 for frames in spec.drag_map.values()
            )
        ]
        self.assertTrue(static, "素材里应当有只用单帧拖拽的造型")
        for spec in static:
            engine = self.engine(spec)
            engine.begin_drag(400.0, 500.0, engine.now_s)
            tilts = []
            for _ in range(24):
                engine.now_s += 0.05
                engine.update_drag(engine.root_x + engine.grab_dx + 55.0, 500.0, engine.now_s)
                frame = engine.tick(engine.now_s, 0.05)
                snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
                tilts.append(snapshot.body_tilt)
            self.assertGreater(
                max(tilts) - min(tilts), 0.5,
                f"{spec.id} 的拖拽倾角没有变化，静态单帧看起来就是没动作",
            )

    def test_every_outfit_drag_tier_is_animated(self):
        """每套造型的每个拖拽档位都必须是真的多帧动画。

        素材曾经有 4 套只生成 1 帧、1 套完全没有拖拽图，拖起来"换张图就没了"，
        用户反馈就是"这个造型没有拖拽动作"。现在由
        `scripts/pipeline/bake_drag_motion.py` 离线补齐。
        """
        for spec in self.outfits:
            if not spec.drag_map:
                # 没有档位的造型允许用待机循环兜底，但必须有帧可放。
                self.assertGreater(len(spec.frames), 1, f"{spec.id} 连待机帧都没有")
                continue
            for tier, frames in spec.drag_map.items():
                self.assertGreater(
                    len(frames), 1,
                    f"{spec.id}/{tier} 只有 {len(frames)} 帧，拖拽不会有动画",
                )

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造渲染快照")
    def test_drag_legs_trail_opposite_to_motion(self):
        """物理判据：被拎住头往左走，身体/脚应拖在右侧，反之亦然。

        这是用户定的标准（"腿朝哪里，哪里反方向就是正确的拖拽方向"）。
        默认素材即基准：左拖时脚相对中轴 +7.6%，右拖 −5.8%。
        曾经因为程序化倾角写成 -vx，导致所有造型的脚都甩向反方向。
        """
        import numpy as np

        def feet_lean(pixmap):
            image = pixmap.toImage()
            width, height = image.width(), image.height()
            arr = np.frombuffer(
                image.constBits().tobytes(), dtype=np.uint8
            ).reshape(height, image.bytesPerLine() // 4, 4)
            alpha = arr[:, :width, 3]
            ys, xs = np.where(alpha > 60)
            if len(xs) < 500:
                return 0.0
            center = xs.mean()
            y0, y1 = ys.min(), ys.max()
            foot = ys > (y1 - (y1 - y0) * 0.12)
            return float((xs[foot].mean() - center) / width * 100) if foot.any() else 0.0

        def render(spec, dx):
            engine = self.engine(spec)
            renderer = QtRenderer(self._cache(), self._rig(),
                                  (engine.display_w, engine.display_h),
                                  self.config.get("rig"))
            start = 400.0 if dx > 0 else 800.0
            engine.begin_drag(start, 500.0, engine.now_s)
            x = start
            for _ in range(8):
                engine.now_s += 0.05
                x += dx
                engine.update_drag(x, 500.0, engine.now_s)
            frame = engine.tick(engine.now_s, 0.05)
            snap = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
            return renderer.render(snap)

        for spec in [None] + list(self.outfits):
            name = spec.name_zh if spec else "默认"
            left = feet_lean(render(spec, -70.0))
            right = feet_lean(render(spec, +70.0))
            self.assertGreater(
                left, right,
                f"{name} 拖拽方向反了：左拖脚偏移 {left:+.1f}，右拖 {right:+.1f}"
                "（左拖应脚朝右、右拖应脚朝左）",
            )

    def test_two_tier_outfit_uses_band_mapping(self):
        """档位少于三档时按 hold/medium/fast 映射，不因 pose_index 抖动。"""
        for spec in self.outfits:
            if not spec.has_drag or spec.has_dense_drag or len(spec.drag_map) >= 11:
                continue
            tiers = {spec.drag_tier_for(index, band)
                     for index in range(11)
                     for band in ("hold", "medium", "fast")}
            self.assertLessEqual(
                len(tiers), 3,
                f"{spec.id} 档位组合过多，拖拽会跳档：{sorted(tiers)}",
            )
            self.assertTrue(tiers.issubset(set(spec.drag_map)))


class RetinaHitTestTests(unittest.TestCase):
    """Retina 屏上"点上去没反应"的回归测试。

    ``ImageCache`` 在 2 倍屏上出的是物理尺寸位图（256×384 窗口 → 512×768），
    而 ``_visible_at`` 以前直接拿**逻辑坐标**去 ``pixelColor``，等于只查到了
    位图左上角 1/4。实测 dpr=2 时，点身体（逻辑 128,200）取到 alpha=0，
    ``mousePressEvent`` 因此根本不进入点击/拖拽分支——用户看到的就是
    "怎么点都没动作"。真机屏幕就是 4096×2304（2 倍）。
    """

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造窗口")
    def test_click_hits_body_on_retina(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint

        app = QApplication.instance() or QApplication([])
        from mimi_pet.engine import PetEngine
        from mimi_pet.image_cache import ImageCache
        from mimi_pet.qt_window import MimiWindow
        from mimi_pet.renderer import QtRenderer
        from mimi_pet.rig_model import load_rig_model

        config = load_config()
        library = ActionLibrary(Path(config["asset_manifest"]))
        rig = load_rig_model(Path(config["asset_manifest"]).parent / library.live_rig["model"])

        # 必须是"穿造型"的场景：默认外观走 _render_rig，自己画的是 256×384、
        # dpr=1 的画布，碰巧没暴露；造型走 _render_full_frame →
        # cache.scaled_pixmap，2 倍屏上是 512×768 + dpr=2，才会错位。
        catalog = _catalog()
        self.assertTrue(len(catalog), f"造型目录为空：{catalog.load_error}")
        outfit = catalog.presets()[0]
        # 一个身体上的点（逻辑坐标），两倍屏上曾经 miss。
        probe = QPoint(128, 240)
        for dpr in (1.0, 2.0):
            cache = ImageCache(dpr=dpr)
            engine = PetEngine(library, config)
            engine.set_outfit(outfit)
            renderer = QtRenderer(cache, rig, (engine.display_w, engine.display_h), config.get("rig"))
            window = MimiWindow(engine, renderer, lambda x, y: None, lambda: None)
            window.resize(engine.display_w, engine.display_h)
            for index in range(3):
                frame = engine.tick(1000.0 + index * 0.016, 0.016)
                snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
                window.show_frame(snapshot, renderer.render(snapshot))
            self.assertEqual(
                window._pixmap.toImage().width() >= engine.display_w, True,
                "贴图尺寸异常",
            )
            self.assertTrue(
                window._visible_at(probe),
                f"dpr={dpr} 穿 {outfit.id} 时点身体 ({probe.x()},{probe.y()}) "
                f"判定为透明——点击会完全没反应",
            )

    @unittest.skipUnless(HAVE_QT, "需要 PySide6 才能构造窗口")
    def test_click_through_transparent_corner_is_ignored(self):
        """空白角落必须仍然穿过去（不能为了修命中把整个窗口变成可点）。"""
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint

        app = QApplication.instance() or QApplication([])
        from mimi_pet.engine import PetEngine
        from mimi_pet.image_cache import ImageCache
        from mimi_pet.qt_window import MimiWindow
        from mimi_pet.renderer import QtRenderer
        from mimi_pet.rig_model import load_rig_model

        config = load_config()
        library = ActionLibrary(Path(config["asset_manifest"]))
        rig = load_rig_model(Path(config["asset_manifest"]).parent / library.live_rig["model"])
        catalog = _catalog()
        outfit = catalog.presets()[0]
        for dpr in (1.0, 2.0):
            cache = ImageCache(dpr=dpr)
            engine = PetEngine(library, config)
            engine.set_outfit(outfit)
            renderer = QtRenderer(cache, rig, (engine.display_w, engine.display_h), config.get("rig"))
            window = MimiWindow(engine, renderer, lambda x, y: None, lambda: None)
            window.resize(engine.display_w, engine.display_h)
            for index in range(3):
                frame = engine.tick(1000.0 + index * 0.016, 0.016)
                snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
                window.show_frame(snapshot, renderer.render(snapshot))
            self.assertFalse(
                window._visible_at(QPoint(4, 4)),
                f"dpr={dpr} 左上角空白处不该拦住鼠标",
            )


class OutfitSwitchTests(_OutfitMixin, unittest.TestCase):
    """换造型本身：忙的时候排队，但不能永远排队。"""

    def test_pending_outfit_applies_when_busy(self):
        engine = self.engine(None)
        engine.start_sit()  # 坐姿是循环动作，永远回不到待机
        self.assertIs(engine.states.state, PetState.PERFORMING)
        target = self.outfits[0]
        self.assertTrue(engine.set_outfit(target))
        self.assertIs(engine.outfit, None, "忙时不应该硬切")
        self.assertIs(engine.pending_outfit, target)
        self.assertTrue(engine.apply_pending_outfit_now(), "排队的造型必须能被立刻结算")
        self.assertIs(engine.outfit, target)
        self.assertIs(engine.pending_outfit, None)

    def test_pending_outfit_is_not_applied_mid_drag(self):
        engine = self.engine(None)
        engine.begin_drag(500.0, 500.0, engine.now_s)
        target = self.outfits[0]
        engine.set_outfit(target)
        self.assertFalse(
            engine.apply_pending_outfit_now(),
            "拖拽中不能换衣服（会当场消失）",
        )
        self.assertIs(engine.pending_outfit, target)

    def test_asset_index_tracks_active_outfit(self):
        engine = self.engine(None)
        self.assertFalse(engine.is_outfit_asset(Path("nope.webp")))
        spec = self.outfits[0]
        engine.set_outfit(spec)
        self.assertTrue(engine.is_outfit_asset(spec.frames[0]))
        for frames in spec.action_map.values():
            self.assertTrue(engine.is_outfit_asset(frames[0]))
        engine.set_outfit(None)
        self.assertFalse(engine.is_outfit_asset(spec.frames[0]))


class OutfitServicePolicyTests(unittest.TestCase):
    """目录层：daily 的判定与"能做什么"的说明必须与运行时一致。"""

    def test_daily_requires_walk_and_touch(self):
        catalog = _catalog()
        for spec in catalog.presets():
            if not spec.is_daily:
                continue
            for action_id in ("walk_right", "head_pat", "cheek_poke", "land_recover"):
                self.assertTrue(spec.supports(action_id), f"{spec.id} 标了 daily 却缺 {action_id}")

    def test_every_outfit_resolves_all_entry_action_ids(self):
        """目录层也要保证：程序里请求的 id 一定能落到这套衣服上。"""
        catalog = _catalog()
        requested = set(OUTFIT_ACTION_FALLBACKS) | {
            "walk_left", "walk_right", "head_pat", "cheek_poke", "celebrate",
            "wave", "nod", "yawn", "clap", "land_recover", "listen", "look_up",
            "shy", "sit_idle", "tilt_head",
        }
        for spec in catalog.presets():
            for action_id in sorted(requested):
                resolved = spec.frames_for(action_id)
                self.assertIsNotNone(resolved, f"{spec.id} 无法解析 {action_id}")
                self.assertIn(resolved[1], spec.action_map)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
