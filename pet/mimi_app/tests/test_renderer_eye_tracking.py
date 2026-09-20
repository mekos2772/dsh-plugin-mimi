"""Offscreen checks for the action-matched v5 flat Live rig."""

from __future__ import annotations

from dataclasses import replace
import os
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

from mimi_pet.image_cache import ImageCache  # noqa: E402
from mimi_pet.renderer import QtRenderer, RenderSnapshot, _painting  # noqa: E402
from mimi_pet.rig_model import load_rig_model  # noqa: E402


class ActionMatchedRigRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        rig = load_rig_model(
            ROOT / "assets" / "characters" / "mimi" / "library_v1" / "usable" / "live_rig_v5" / "model.json"
        )
        cls.renderer = QtRenderer(ImageCache(), rig, (512, 768))
        cls.neutral = RenderSnapshot(
            mode="rig",
            frame_path=None,
            expression="neutral",
            state="IDLE",
            action_id=None,
            frame_index=None,
            frame_total=None,
            root_x=0,
            root_y=0,
            vx=0,
            vy=0,
            grab_dx=0,
            grab_dy=0,
            direction="",
            speed_band="",
            pose_set="",
            drag_speed=0,
            body_tilt=0,
            hair_lag=0,
            skirt_lag=0,
            display_size=(512, 768),
            pivot_display=(256, 744),
            ground_y=744,
        )

    def test_rest_renders_and_attention_is_low_amplitude_transform(self) -> None:
        rest = self.renderer.render(self.neutral).toImage()
        attention = self.renderer.render(
            replace(self.neutral, head_x=9.0, head_y=-5.0, head_angle=4.0)
        ).toImage()
        self.assertFalse(rest.isNull())
        self.assertNotEqual(attention, rest)

    def test_flat_rig_tracks_irises_and_swaps_all_face_states(self) -> None:
        rest = self.renderer.render(self.neutral).toImage()
        gaze = self.renderer.render(replace(self.neutral, head_x=9.0)).toImage()
        self.assertNotEqual(gaze, rest)
        for mouth in ("happy", "talk"):
            rendered = self.renderer.render(replace(self.neutral, mouth=mouth)).toImage()
            self.assertNotEqual(rendered, rest, mouth)
        blinked = self.renderer.render(replace(self.neutral, eyes_closed=True)).toImage()
        self.assertNotEqual(blinked, rest)

    def test_mouth_states_never_move_the_tracked_eyes(self) -> None:
        """Root fix: the iris composite runs under every mouth state, so
        switching expressions no longer snaps the eyes (the 6 Hz talk flap
        used to jump the gaze between tracked and rest position)."""
        eye_box = (150, 462, 360, 515)  # display px of master rows ~925-1030
        mouth_box = (150, 551, 360, 570)  # display px of master rows ~1102-1140

        def band(image, box):
            return [
                image.pixelColor(x, y).rgba()
                for y in range(box[1], box[3])
                for x in range(box[0], box[2])
            ]

        for mouth in ("happy", "talk"):
            base = self.renderer.render(replace(self.neutral, head_x=9.0)).toImage()
            mouthed = self.renderer.render(
                replace(self.neutral, mouth=mouth, head_x=9.0)
            ).toImage()
            self.assertEqual(band(base, eye_box), band(mouthed, eye_box), mouth)
            self.assertNotEqual(band(base, mouth_box), band(mouthed, mouth_box), mouth)

    def test_blink_composes_with_the_smile(self) -> None:
        """Root fix: a random blink keeps the active smile instead of showing
        the neutral-mouthed whole-swap blink plate."""
        happy = self.renderer.render(replace(self.neutral, mouth="happy")).toImage()
        plain_blink = self.renderer.render(replace(self.neutral, eyes_closed=True)).toImage()
        blink_happy = self.renderer.render(
            replace(self.neutral, mouth="happy", eyes_closed=True)
        ).toImage()
        self.assertNotEqual(blink_happy, happy)  # the eyes did close
        self.assertNotEqual(blink_happy, plain_blink)  # and the smile survived
        # The composed smile mouth equals the plain smile's mouth band.
        mouth_box = (150, 551, 360, 570)
        self.assertEqual(
            [blink_happy.pixelColor(x, y).rgba() for y in range(mouth_box[1], mouth_box[3]) for x in range(mouth_box[0], mouth_box[2])],
            [happy.pixelColor(x, y).rgba() for y in range(mouth_box[1], mouth_box[3]) for x in range(mouth_box[0], mouth_box[2])],
        )

    def test_breathing_changes_plate_without_moving_ground_registration(self) -> None:
        rest = self.renderer.render(self.neutral).toImage()
        breathed = self.renderer.render(replace(self.neutral, body_breathe=1.0)).toImage()
        self.assertNotEqual(breathed, rest)


_PAINT_ABORT_PROBE = r'''
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, sys.argv[1])
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication

app = QGuiApplication.instance() or QGuiApplication([])

from mimi_pet.action_library import ActionLibrary
from mimi_pet.config import load_config
from mimi_pet.engine import PetEngine
from mimi_pet.image_cache import ImageCache
from mimi_pet.renderer import QtRenderer, build_snapshot
from mimi_pet.rig_model import load_rig_model

cfg = load_config()
lib = ActionLibrary(Path(cfg["asset_manifest"]))
engine = PetEngine(lib, cfg)
engine.place_at(500.0, 800.0)
rig = load_rig_model(Path(cfg["asset_manifest"]).parent / lib.live_rig["model"])
if getattr(rig, "eye_tracking", None) is None:
    print("SKIPPED: this rig has no eye tracking")
    raise SystemExit(0)

# A genuine 0-byte asset, so the real ImageCache._decode raises on its own.
# Nothing about the failure is patched - only the rig points at a broken file.
broken = Path(tempfile.mkdtemp()) / "broken.png"
broken.write_bytes(b"")
object.__setattr__(rig.eye_tracking, "base_file", broken)

renderer = QtRenderer(ImageCache(), rig, (engine.display_w, engine.display_h))

ticks = [0]
def on_tick():                      # unguarded, byte-for-byte like qt_app.on_tick
    ticks[0] += 1
    frame = engine.tick(ticks[0] / 60.0, 1.0 / 60.0, (500.0, 400.0), 800.0, None)
    renderer.render(build_snapshot(frame, engine, (engine.display_w, engine.display_h)))

timer = QTimer()
timer.setInterval(16)
timer.timeout.connect(on_tick)
timer.start()
QTimer.singleShot(1500, app.quit)
app.exec()
print("SURVIVED ticks=%d" % ticks[0])
'''


class PainterLifetimeTests(unittest.TestCase):
    """A failure inside a paint transaction must not abort the process.

    ``QPainter(device)`` begins painting immediately and Qt requires ``end()``
    before the device may be destroyed. An exception escaping mid-transaction
    used to leave the device marked active, and Qt then tore down a device that
    was still being painted. That is not a catchable Python error: it measured
    as ``Fatal Python error: Aborted`` (exit code 3) on the first frame after
    launch, with the pet merely idle. A ``try/except`` around the tick loop
    does not help, because the violated contract lives in the native object
    lifetime rather than in the Python exception.
    """

    def test_the_painter_is_ended_when_the_body_raises(self) -> None:
        canvas = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
        seen: dict = {}
        with self.assertRaises(RuntimeError):
            with _painting(canvas) as painter:
                seen["painter"] = painter
                self.assertTrue(canvas.paintingActive())
                raise RuntimeError("mid-paint failure")
        self.assertFalse(seen["painter"].isActive())
        self.assertFalse(canvas.paintingActive())

    def test_the_painter_is_ended_on_the_normal_path(self) -> None:
        canvas = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
        with _painting(canvas) as painter:
            painter.fillRect(0, 0, 8, 8, Qt.GlobalColor.red)
        self.assertFalse(painter.isActive())
        self.assertFalse(canvas.paintingActive())

    def test_a_decode_failure_mid_paint_does_not_abort_the_process(self) -> None:
        # Subprocess: the pre-fix failure mode is a process abort, which would
        # take this test runner down with it.
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", _PAINT_ABORT_PROBE,
             str(ROOT / "mimi_app" / "src")],
            capture_output=True, text=True, timeout=180,
        )
        tail = (result.stderr or "")[-1500:]
        self.assertNotEqual(
            result.returncode, 3,
            "process aborted (exit 3): the paint device was destroyed while "
            "still active\n" + tail,
        )
        self.assertEqual(result.returncode, 0, tail)
        self.assertIn("SURVIVED", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
