"""主动交互逻辑的契约测试。"""
from __future__ import annotations

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pet" / "mimi_app" / "src"))

import unittest

from mimi_pet.proactive import (
    BUDGET_MAX, Context, ProactiveDirector, ROUTINES, Routine, Step,
)


class _AlwaysRng:
    """永远通过概率门、永远选第一个候选。"""

    def random(self):
        return 0.0

    def choices(self, population, weights=None, k=1):
        return [population[0]]


def _ctx(now, **kw):
    base = dict(now=now, idle_s=999.0, hour=10)
    base.update(kw)
    return Context(**base)


class CatalogTests(unittest.TestCase):
    def test_routine_ids_unique(self):
        ids = [r.id for r in ROUTINES]
        self.assertEqual(len(ids), len(set(ids)), "行为 id 重复")

    def test_every_step_has_action(self):
        for routine in ROUTINES:
            self.assertTrue(routine.steps, f"{routine.id} 没有步骤")
            for step in routine.steps:
                self.assertTrue(step.action)
                self.assertGreaterEqual(step.pause_s, 0.0)

    def test_catalog_covers_many_actions(self):
        """目录要尽量用上现有动作，而不是反复播那两三个。"""
        actions = {
            step.action for routine in ROUTINES for step in routine.steps
            if not step.action.startswith("@")
        }
        self.assertGreaterEqual(len(actions), 35, f"只用到 {len(actions)} 个动作")

    def test_actions_exist_in_library(self):
        """目录里引用的动作必须真的能加载，否则运行时会被跳过。"""
        from mimi_pet.action_library import ActionLibrary
        from mimi_pet.config import load_config
        cfg = load_config()
        library = ActionLibrary(Path(cfg["asset_manifest"]))
        known = {spec.id for spec in library.all()}
        missing = sorted({
            step.action for routine in ROUTINES for step in routine.steps
            if not step.action.startswith("@")
        } - known)
        self.assertEqual(missing, [], f"目录引用了不存在的动作: {missing}")


class GatingTests(unittest.TestCase):
    def test_dragging_blocks(self):
        d = ProactiveDirector(rng=random.Random(1))
        self.assertIsNone(d.next_action(0.0, _ctx(0.0, dragging=True)))

    def test_dsh_busy_blocks(self):
        d = ProactiveDirector(rng=random.Random(1))
        self.assertIsNone(d.next_action(0.0, _ctx(0.0, dsh_busy=True)))

    def test_quiet_blocks_by_default(self):
        d = ProactiveDirector(rng=random.Random(1))
        self.assertIsNone(d.next_action(0.0, _ctx(0.0, quiet=True)))

    def test_focus_routine_needs_focus(self):
        focus = [r for r in ROUTINES if r.needs_focus]
        self.assertTrue(focus)
        d = ProactiveDirector(routines=focus, rng=random.Random(2))
        self.assertIsNone(d.next_action(0.0, _ctx(0.0, focus_running=False)))

    def test_music_routine_needs_music(self):
        music = [r for r in ROUTINES if r.needs_music]
        self.assertTrue(music)
        d = ProactiveDirector(routines=music, rng=random.Random(2))
        self.assertIsNone(d.next_action(0.0, _ctx(0.0, music_playing=False)))

    def test_idle_threshold_respected(self):
        routine = Routine(id="probe", name="探针", steps=(Step("nod"),),
                          idle_at_least_s=60, cooldown_s=0, weight=99)
        d = ProactiveDirector(routines=(routine,), rng=random.Random(3))
        self.assertIsNone(d.next_action(0.0, _ctx(0.0, idle_s=10)))

    def test_hour_window(self):
        routine = Routine(id="night", name="夜间", steps=(Step("yawn"),),
                          hours=(22, 29), idle_at_least_s=0, cooldown_s=0, weight=99)
        d = ProactiveDirector(routines=(routine,), rng=random.Random(4))
        # 22:00–05:00 之间可出现
        for hour in (22, 23, 0, 4):
            d2 = ProactiveDirector(routines=(routine,), rng=random.Random(4))
            self.assertIsNotNone(d2.next_action(0.0, _ctx(0.0, hour=hour)),
                                 f"{hour} 点应当可触发")
        for hour in (6, 12, 21):
            d3 = ProactiveDirector(routines=(routine,), rng=random.Random(4))
            self.assertIsNone(d3.next_action(0.0, _ctx(0.0, hour=hour)),
                              f"{hour} 点不应触发")


class SequenceTests(unittest.TestCase):
    def test_routine_plays_steps_in_order(self):
        routine = Routine(id="seq", name="序列", steps=(
            Step("a", 0.0), Step("b", 0.0), Step("c", 0.0),
        ), idle_at_least_s=0, cooldown_s=0, weight=99)
        d = ProactiveDirector(routines=(routine,), rng=_AlwaysRng())
        played = []
        now = 0.0
        for _ in range(10):
            action = d.next_action(now, _ctx(now))
            if action:
                played.append(action)
                d.notify_idle(now)
            now += 1.0
        self.assertEqual(played, ["a", "b", "c"])

    def test_pause_respected(self):
        routine = Routine(id="slow", name="慢", steps=(
            Step("a", 5.0), Step("b", 0.0),
        ), idle_at_least_s=0, cooldown_s=0, weight=99)
        d = ProactiveDirector(routines=(routine,), rng=_AlwaysRng())
        first = d.next_action(0.0, _ctx(0.0))
        self.assertEqual(first, "a")
        d.notify_idle(0.0)
        # 暂停期内不出下一步
        self.assertIsNone(d.next_action(1.0, _ctx(1.0)))
        self.assertIsNone(d.next_action(4.9, _ctx(4.9)))
        self.assertEqual(d.next_action(5.1, _ctx(5.1)), "b")

    def test_skips_unavailable_action(self):
        routine = Routine(id="skip", name="跳过", steps=(
            Step("missing", 0.0), Step("real", 0.0),
        ), idle_at_least_s=0, cooldown_s=0, weight=99)
        d = ProactiveDirector(routines=(routine,), rng=_AlwaysRng())
        action = d.next_action(0.0, _ctx(0.0, available=lambda a: a == "real"))
        self.assertEqual(action, "real")

    def test_budget_caps_frequency(self):
        routine = Routine(id="fast", name="频繁", steps=(Step("x", 0.0),),
                          idle_at_least_s=0, cooldown_s=0, weight=99)
        d = ProactiveDirector(routines=(routine,), rng=random.Random(8))
        fired = 0
        now = 0.0
        for _ in range(3600):
            now += 1.0
            if d.next_action(now, _ctx(now)):
                fired += 1
                d.notify_idle(now)
        # 半小时窗口最多 BUDGET_MAX 次
        self.assertLessEqual(fired, BUDGET_MAX * 3, f"一小时内触发了 {fired} 次")


class UserInputTests(unittest.TestCase):
    def test_input_cancels_current_routine(self):
        routine = Routine(id="long", name="长", steps=(
            Step("a", 0.0), Step("b", 0.0), Step("c", 0.0),
        ), idle_at_least_s=0, cooldown_s=0, weight=99)
        d = ProactiveDirector(routines=(routine,), rng=_AlwaysRng())
        self.assertEqual(d.next_action(0.0, _ctx(0.0)), "a")
        d.note_user_input(0.5)
        self.assertEqual(d.pending_routine(), "", "用户输入后仍挂着行为")

    def test_input_sets_grace_period(self):
        routine = Routine(id="p", name="p", steps=(Step("a", 0.0),),
                          idle_at_least_s=0, cooldown_s=0, weight=99)
        d = ProactiveDirector(routines=(routine,), rng=_AlwaysRng())
        d.note_user_input(0.0)
        self.assertIsNone(d.next_action(1.0, _ctx(1.0)), "冷却期内不应触发")


if __name__ == "__main__":
    unittest.main()
