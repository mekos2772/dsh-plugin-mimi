"""Startup asset check: the loaders accept files the renderer cannot use.

``ActionLibrary`` and ``load_rig_model`` only ask whether a file exists, so a
0-byte or truncated image passes both and fails at the first render instead —
one dropped frame on the full-frame path, a frozen silhouette on the rig path.
(That is not hypothetical: ``sit_down/frames/0046.webp`` was committed as 0
bytes and shipped that way.) These tests pin the check that catches it, and the
agreement between the check and the renderer about which file gets loaded.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from mimi_pet.action_library import ActionLibrary  # noqa: E402
from mimi_pet.asset_check import find_broken_assets  # noqa: E402
from mimi_pet.config import load_config  # noqa: E402
from mimi_pet.image_cache import ImageCache  # noqa: E402
from mimi_pet.renderer import QtRenderer  # noqa: E402
from mimi_pet.rig_model import load_rig_model, resolve_expression_master  # noqa: E402


class AssetCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # QApplication, not QGuiApplication: this file sorts before the widget
        # tests, so it is the one that creates the process-wide singleton.
        # A QGuiApplication here makes every later QWidget() a fatal Qt error
        # ("Cannot create a QWidget without QApplication").
        cls.app = QApplication.instance() or QApplication([])
        cls.manifest = Path(load_config()["asset_manifest"])
        cls.library = ActionLibrary(cls.manifest)
        cls.rig = load_rig_model(
            cls.manifest.parent / cls.library.live_rig["model"]
        )
        # A real, decodable frame to truncate for the damage cases.
        cls.good_frame = cls.library.get("sit_down").frames[44]

    # ------------------------------------------------------------ real assets
    def test_the_shipped_assets_all_pass_the_stat_sweep(self) -> None:
        every = (*self.library.asset_paths(), *self.rig.asset_paths())
        self.assertEqual(find_broken_assets(every, decode=False), [])

    def test_the_shipped_rig_assets_all_decode(self) -> None:
        # Small enough to decode eagerly, which is why the startup check does.
        self.assertEqual(find_broken_assets(self.rig.asset_paths(), decode=True), [])

    def test_library_asset_paths_cover_every_playable_frame(self) -> None:
        expected: set = set()
        for spec in (
            *self.library.all(),
            *self.library.drag_pose_sets(),
            *self.library._release_transitions.values(),
        ):
            expected.update(spec.frames)
        paths = self.library.asset_paths()
        self.assertEqual(len(paths), len(set(paths)), "must be deduplicated")
        self.assertEqual(set(paths), expected)
        for path in paths:
            self.assertTrue(path.is_file(), path)

    def test_rig_asset_paths_include_every_frame_drawing_file(self) -> None:
        names = {path.name for path in self.rig.asset_paths()}
        self.assertIn("master_action_style_v1.png", names)   # the silhouette
        self.assertIn("master_neutral_eye_base_v1.png", names)  # the eyes
        self.assertIn("lids_blink_v1.png", names)             # a patch
        self.assertIn("master_blink_v1.png", names)           # an expression
        for path in self.rig.asset_paths():
            self.assertTrue(path.is_file(), path)

    # ------------------------------------------------------------- the damage
    def test_a_zero_byte_frame_is_caught_without_decoding(self) -> None:
        # This is the exact shape that shipped: stat alone must catch it.
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "0046.webp"
            broken.write_bytes(b"")
            problems = find_broken_assets([broken], decode=False)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0].name, "0046.webp")
        self.assertIn("0 字节", problems[0][1])

    def test_a_truncated_frame_needs_the_decode_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "truncated.webp"
            broken.write_bytes(self.good_frame.read_bytes()[: len(self.good_frame.read_bytes()) // 2])
            # Non-zero size, so the cheap sweep cannot tell.
            self.assertEqual(find_broken_assets([broken], decode=False), [])
            problems = find_broken_assets([broken], decode=True)
        self.assertEqual(len(problems), 1)
        self.assertIn("无法解码", problems[0][1])

    def test_a_missing_file_is_reported_by_both_passes(self) -> None:
        missing = Path(tempfile.gettempdir()) / "mimi-does-not-exist.webp"
        for decode in (False, True):
            with self.subTest(decode=decode):
                problems = find_broken_assets([missing], decode=decode)
                self.assertEqual(len(problems), 1)
                self.assertIn("无法访问", problems[0][1])

    def test_a_directory_is_not_a_plain_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = find_broken_assets([Path(directory)], decode=False)
        self.assertEqual(len(problems), 1)
        self.assertIn("不是普通文件", problems[0][1])

    def test_every_problem_is_reported_not_just_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ("a.webp", "b.webp", "c.webp"):
                path = Path(directory) / name
                path.write_bytes(b"")
                paths.append(path)
            problems = find_broken_assets(paths, decode=False)
        self.assertEqual([path.name for path, _ in problems], ["a.webp", "b.webp", "c.webp"])

    # ------------------------------------------- agreement with the renderer
    def test_expression_resolution_agrees_with_the_renderer(self) -> None:
        # The check is worthless if it validates a path the renderer never
        # opens, so for every expression the rig actually maps, the resolver
        # and the renderer must name the same file.
        renderer = QtRenderer(ImageCache(), self.rig, (256, 384))
        self.assertTrue(self.rig.expressions)
        for expression in self.rig.expressions:
            with self.subTest(expression=expression):
                resolved = resolve_expression_master(self.rig, expression)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved, renderer._master_expression_file(expression))

    def test_an_unmapped_expression_resolves_to_none(self) -> None:
        # The resolver stays a pure lookup; the neutral fallback belongs to the
        # renderer, which is why the checker can trust a None as "not mapped".
        self.assertIsNone(resolve_expression_master(self.rig, "no_such_expression"))
        self.assertIsNone(resolve_expression_master(self.rig, ""))
        renderer = QtRenderer(ImageCache(), self.rig, (256, 384))
        neutral = renderer._master_expression_file("neutral")
        self.assertIsNotNone(neutral)
        self.assertEqual(renderer._master_expression_file("no_such_expression"), neutral)


if __name__ == "__main__":
    unittest.main()
