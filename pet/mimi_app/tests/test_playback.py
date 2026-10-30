"""动作播放时长：每个动作按自己的素材时长原速播，不强行拉齐。

历史教训（两个极端都试过）：
1. 不设上限 —— 素材尾部有 1–2 秒静止帧，播完像卡死。
2. 上限 3000 ms —— 70 个动作全被压成正好 3.00 秒，长动作抽搐、短动作
   按原速，速度不统一。而动作本身长短差很多（0.8s–6.4s），强行拉齐是错的。

现在的做法：尾巴已经在素材层裁掉（动作主体结束就切），运行时**一律按
素材标称时长原速播放**；只有异常超长（> 8s）才兜底加速，避免真的像死机。
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
from mimi_pet.frame_player import FramePlayer
from mimi_pet.state_machine import PetState


def _library() -> ActionLibrary:
    return ActionLibrary(Path(load_config()["asset_manifest"]))


def _play_ms(spec, step_ms: int = 16) -> int:
    player = FramePlayer()
    player.play(spec)
    total = 0
    for _ in range(4000):
        total += step_ms
        sample = player.advance(step_ms)
        if sample is not None and sample.finished:
            break
    return total


class PlaybackDurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = _library()

    def test_long_action_plays_at_its_own_length(self):
        """按素材时长原速播：实播应贴近标称，不能被压成固定值。"""
        spec = self.library.get("wave_slow")
        nominal = spec.total_duration_ms
        played = _play_ms(spec)
        expected = min(nominal, FramePlayer.MAX_ONESHOT_MS)
        self.assertAlmostEqual(played / expected, 1.0, delta=0.12,
                               msg=f"{spec.id}: 标称 {nominal}ms 实播 {played}ms")

    def test_durations_are_not_uniform(self):
        """不同动作的原速时长必须各不相同（就是这轮修的问题）。"""
        ids = ["land_recover_v4_12", "nod_yes", "cheer_jump", "wave_hello"]
        played = {}
        for action_id in ids:
            try:
                spec = self.library.get(action_id)
            except KeyError:
                continue
            played[action_id] = _play_ms(spec)
        self.assertGreaterEqual(len(played), 3, "样本动作缺失，检查素材清单")
        self.assertGreater(len(set(played.values())), 2,
                           f"多个动作实播时长完全相同：{played}")

    def test_short_action_not_slowed_down(self):
        spec = self.library.get("land_recover_v4_12")
        self.assertLess(_play_ms(spec), 1500)

    def test_no_action_exceeds_guard_ceiling(self):
        """兜底上限只该挡住异常超长动作，正常动作一条都不该被它碰到。"""
        for spec in self.library.all():
            if spec.loop:
                continue
            played = _play_ms(spec)
            self.assertLessEqual(
                played, FramePlayer.MAX_ONESHOT_MS + 100,
                f"{spec.id}: 实播 {played}ms 超过兜底上限",
            )

    def test_tail_frames_are_not_static(self):
        """素材层已裁掉静止尾巴：最后 3 帧不该是同一条路径的重复引用。"""
        offenders = []
        for spec in self.library.all():
            if spec.loop or len(spec.frames) < 6:
                continue
            tail = spec.frames[-3:]
            if len(set(tail)) == 1:
                offenders.append(spec.id)
        self.assertEqual(offenders, [],
                         f"这些动作最后 3 帧是同一张图（尾巴没裁干净）：{offenders}")

    def test_looping_action_never_finishes(self):
        spec = self.library.get("listen_pingpong")
        self.assertTrue(spec.loop)
        player = FramePlayer()
        player.play(spec)
        finished = False
        for _ in range(600):
            sample = player.advance(16)
            if sample is not None and sample.finished:
                finished = True
                break
        self.assertFalse(finished, "循环动作不该报 finished")

    def test_wave_returns_to_idle_without_terminal_frame(self):
        """A completed original-skin wave must immediately expose idle state."""
        engine = PetEngine(self.library, load_config())
        self.assertTrue(engine.force_perform("wave"))
        now = 0.0
        completed = None
        for _ in range(200):
            completed = engine.tick(now, 0.083)
            now += 0.083
            if completed.state is PetState.IDLE:
                break
        self.assertIsNotNone(completed)
        self.assertEqual(completed.state, PetState.IDLE)
        self.assertIsNone(completed.sprite)
        self.assertIsNone(engine.player.action)


if __name__ == "__main__":
    unittest.main()
