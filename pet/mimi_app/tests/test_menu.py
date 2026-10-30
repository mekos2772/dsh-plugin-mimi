"""右键菜单的结构契约。

这些测试需要一个 QApplication；没有 PySide6 的环境自动跳过，
不影响纯领域层的测试套件。

为什么要有这组测试：菜单曾经长到 16 个顶层条目，功能和调试入口混在一起。
结构一旦失守，用户就要在一屏菜单里找东西。这里把"顶层不超过 9 个"
和"每个功能都有唯一入口"钉死。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pet" / "mimi_app" / "src"))

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover - 无 Qt 环境
    HAVE_QT = False

#: 顶层最多允许多少个可点条目（含状态头）。
MAX_TOP_LEVEL = 9


def _setup_env() -> Path:
    root = Path(tempfile.mkdtemp())
    for key, name in (
        ("MIMI_NOTES_PATH", "n.json"),
        ("MIMI_TASKS_PATH", "t.json"),
        ("MIMI_SCENE_PATH", "s.json"),
        ("MIMI_WRAPUP_PATH", "w.json"),
        ("MIMI_HEALTH_PATH", "h.json"),
        ("MIMI_OUTFIT_STATE_PATH", "o.json"),
    ):
        os.environ[key] = str(root / name)
    return root


@unittest.skipUnless(HAVE_QT, "需要 PySide6")
class ContextMenuTests(unittest.TestCase):
    app = None
    window = None

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _setup_env()
        import mimi_pet.qt_app as qa

        # run() 自己创建 QApplication；把 exec 换成空实现，避免进事件循环。
        original_exec = QApplication.exec
        QApplication.exec = lambda *a, **k: 0
        try:
            qa.run()
        finally:
            QApplication.exec = original_exec
        cls.app = QApplication.instance()
        for widget in QApplication.topLevelWidgets():
            if hasattr(widget, "hub") and hasattr(widget, "_build_context_menu"):
                cls.window = widget
                break
        if cls.window is None:
            raise unittest.SkipTest("窗口未装配")

    def _menu(self):
        return self.window._build_context_menu()

    def _top_labels(self, menu):
        labels = []
        for action in menu.actions():
            if action.isSeparator():
                continue
            labels.append(action.text() or "[状态]")
        return labels

    def test_frame_update_repaints_synchronously(self):
        from unittest.mock import patch

        snapshot = self.window._snapshot
        pixmap = self.window._pixmap
        with patch.object(self.window, "repaint") as repaint:
            self.window.show_frame(snapshot, pixmap)
        repaint.assert_called_once_with()

    def test_context_menu_is_nonblocking_and_cleans_up(self):
        from PySide6.QtCore import QPoint, QTimer

        QTimer.singleShot(0, lambda: self.window._active_context_menu.hide())
        self.window._show_context_menu(QPoint(20, 20))
        self.assertTrue(self.window.menu_open)
        QApplication.processEvents()
        self.assertFalse(self.window.menu_open)
        self.assertIsNone(self.window._active_context_menu)

    def test_top_level_is_short(self):
        labels = self._top_labels(self._menu())
        self.assertLessEqual(len(labels), MAX_TOP_LEVEL, f"顶层过长: {labels}")

    def test_expected_entries_exist(self):
        labels = self._top_labels(self._menu())
        for expected in ("记一下…", "专注与提醒…", "陪她一会儿", "造型",
                         "记录与任务", "设置", "Harness", "退出 Mimi"):
            self.assertIn(expected, labels, f"缺少入口: {expected}")

    def test_header_present(self):
        menu = self._menu()
        # 状态头是 QWidgetAction，text() 为空
        self.assertTrue(any(a.text() == "" and not a.isSeparator() for a in menu.actions()))

    def test_records_menu_has_all_entries(self):
        menu = self._menu()
        acts = list(menu.actions())
        target = [a for a in acts if a.text() == "记录与任务"][0]
        sub = target.menu()
        keep = list(sub.actions())
        labels = [a.text() for a in keep if not a.isSeparator()]
        self.assertTrue(any("便签" in x for x in labels), labels)
        self.assertTrue(any("任务" in x for x in labels), labels)
        self.assertIn("今天到这里…", labels)

    def test_settings_menu_has_size_and_health(self):
        menu = self._menu()
        acts = list(menu.actions())
        target = [a for a in acts if a.text() == "设置"][0]
        sub = target.menu()
        keep = list(sub.actions())
        labels = [a.text() for a in keep if not a.isSeparator()]
        self.assertIn("尺寸与位置", labels)
        self.assertIn("健康小卡", labels)
        self.assertIn("显示调试信息", labels)

    def test_companion_menu_has_feed_and_space(self):
        menu = self._menu()
        acts = list(menu.actions())
        target = [a for a in acts if a.text() == "陪她一会儿"][0]
        sub = target.menu()
        keep = list(sub.actions())
        labels = [a.text() for a in keep if not a.isSeparator()]
        self.assertIn("投喂圆面包", labels)
        self.assertTrue(any("小空间" in x for x in labels), labels)

    def test_no_duplicate_top_level_labels(self):
        labels = self._top_labels(self._menu())
        self.assertEqual(len(labels), len(set(labels)), f"重复项: {labels}")


class DshPanelVisibilityTests(unittest.TestCase):
    """头顶聊天栏必须真的能显示。

    回归背景：v1.0.0 把 ``show_box`` / ``position_at_top`` 掏空成
    ``pass``（注释写"消息栏已停用"），但 ``should_show_panel`` 仍然在悬停
    头顶时返回 True。结果就是条件成立、方法调用也发生了，窗口却从不出现，
    用户看到的是"聊天栏消失了"。
    """

    @unittest.skipUnless(HAVE_QT, "需要 PySide6")
    def test_show_box_makes_panel_visible(self):
        from mimi_pet.dsh_panel import DshPanel

        panel = DshPanel()
        self.assertFalse(panel.isVisible())
        panel.show_box()
        self.assertTrue(panel.isVisible(), "show_box() 之后面板必须可见")
        panel.hide()

    @unittest.skipUnless(HAVE_QT, "需要 PySide6")
    def test_panel_actually_paints(self):
        """面板必须真的画出内容，不能只是"占了个位置"。

        回归背景：构造函数的末尾有
        ``setAttribute(WA_DontShowOnScreen, True)`` + ``move(-10000,-10000)``，
        v1.0.0 停用消息栏时留下的。结果 show() 之后窗口存在但不绘制——
        用户看到的就是"能看到预留位置，但是不显示"。
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter, QColor
        from mimi_pet.dsh_panel import DshPanel

        panel = DshPanel()
        self.assertFalse(
            panel.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen),
            "WA_DontShowOnScreen 会让面板永不绘制",
        )
        panel.position_near(3000.0, 1760.0)
        panel.show_box()
        self.assertTrue(panel.isVisible())
        self.assertGreater(panel.x(), 0, "面板不该被放在屏幕外")
        pixmap = panel.grab()
        image = pixmap.toImage()
        background = QImage(pixmap.size(), QImage.Format.Format_RGB32)
        background.fill(QColor(255, 255, 255))
        painter = QPainter(background)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        # 输入框是浅灰底 + 深色文字，不能整块都是同一种颜色
        colors = set()
        for y in range(0, background.height(), 2):
            for x in range(0, background.width(), 2):
                colors.add(background.pixelColor(x, y).rgb())
                if len(colors) > 8:
                    break
            if len(colors) > 8:
                break
        self.assertGreater(len(colors), 3, "面板渲染出来是一片纯色，说明没画内容")
        panel.hide()

    @unittest.skipUnless(HAVE_QT, "需要 PySide6")
    def test_mode_badge_removed(self):
        """模式徽章已移除：现在只有桌宠模式，徽章是噪音。"""
        from mimi_pet.dsh_panel import DshPanel

        panel = DshPanel()
        self.assertFalse(
            hasattr(panel, "mode_badge"),
            "模式徽章应该已经从面板移除",
        )
        panel.hide()

    @unittest.skipUnless(HAVE_QT, "需要 PySide6")
    def test_position_at_top_moves_panel_on_screen(self):
        from mimi_pet.dsh_panel import DshPanel

        panel = DshPanel()
        panel.position_at_top()
        pos = panel.pos()
        self.assertFalse(
            pos.x() == 0 and pos.y() == 0,
            "position_at_top() 是空实现时面板会停在 (0,0)",
        )
        self.assertGreaterEqual(pos.y(), 0, "面板不该跑到屏幕外")

    @unittest.skipUnless(HAVE_QT, "需要 PySide6")
    def test_hover_head_shows_panel_end_to_end(self):
        """悬停头顶 → should_show_panel 为真 → show_box 真的显示出来。"""
        from mimi_pet.dsh_integration import DshIntegration
        from mimi_pet.dsh_panel import DshPanel
        from mimi_pet.engine import PetEngine
        from mimi_pet.action_library import ActionLibrary
        from mimi_pet.config import load_config
        from pathlib import Path

        config = load_config()
        engine = PetEngine(ActionLibrary(Path(config["asset_manifest"])), config)
        integration = DshIntegration(engine)
        integration.connected = True
        integration.mode = "agent"
        self.assertTrue(
            integration.should_show_panel(head_hovered=True),
            "桌宠模式悬停头顶时应当允许显示输入框",
        )
        panel = DshPanel()
        panel.position_at_top()
        panel.show_box()
        self.assertTrue(panel.isVisible(), "条件成立后输入框必须真的出现")
        panel.hide()


class PanelAnchorAndDprTests(unittest.TestCase):
    """聊天栏贴头顶 + 渲染分辨率全程一致。"""

    @unittest.skipUnless(HAVE_QT, "需要 PySide6")
    def test_panel_sits_above_pet_head(self):
        """输入框必须出现在桌宠头顶，而不是屏幕顶部。"""
        from mimi_pet.dsh_panel import DshPanel

        panel = DshPanel()
        for root_x, head_y in ((3000.0, 1760.0), (200.0, 1760.0)):
            panel.position_near(root_x, head_y)
            bottom = panel.y() + panel.height()
            self.assertAlmostEqual(
                bottom, head_y, delta=20,
                msg=f"面板底边 {bottom} 应贴近头顶 {head_y}",
            )
            center = panel.x() + panel.width() / 2
            self.assertAlmostEqual(
                center, root_x, delta=panel.width() / 2,
                msg=f"面板中心 {center} 应对齐角色 {root_x}",
            )

    @unittest.skipUnless(HAVE_QT, "需要 PySide6")
    def test_render_size_is_stable_across_states(self):
        """待机与落地必须用同一套像素尺寸。

        回归背景：``_render_full_frame`` 用 ``QImage(display_w, display_h)``
        新建画布却忘了 ``setDevicePixelRatio``，2 倍屏上待机是 512×768@2、
        落地却变成 256×384@1 —— 放大插值后观感就是"落地抽搐"。
        """
        from PySide6.QtCore import QSize
        from mimi_pet.engine import PetEngine
        from mimi_pet.action_library import ActionLibrary
        from mimi_pet.config import load_config
        from mimi_pet.rig_model import load_rig_model
        from mimi_pet.renderer import QtRenderer, build_snapshot
        from mimi_pet.image_cache import ImageCache
        from mimi_pet.state_machine import PetState
        from mimi_pet.outfits import OutfitCatalog
        from pathlib import Path

        config = load_config()
        library = ActionLibrary(Path(config["asset_manifest"]))
        catalog = OutfitCatalog(
            Path(config["asset_manifest"]).parent.parent / "expansion_v1" / "presets.json"
        )
        if not len(catalog):
            self.skipTest("造型目录为空")
        rig = load_rig_model(
            Path(config["asset_manifest"]).parent / library.live_rig["model"]
        )
        for dpr in (1.0, 2.0):
            cache = ImageCache(dpr=dpr)
            engine = PetEngine(library, config)
            engine.set_outfit(catalog.presets()[0])
            renderer = QtRenderer(
                cache, rig, (engine.display_w, engine.display_h), config.get("rig")
            )
            t = 1000.0
            frame = engine.tick(t, 0.016)
            snap = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
            idle = renderer.render(snap)
            engine.begin_drag(500.0, 500.0, t)
            engine.root_y = 2200.0
            engine.release(2200.0, t)
            sizes = set()
            for _ in range(200):
                t += 0.01
                frame = engine.tick(t, 0.01, ground_y=2200.0)
                snap = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
                pix = renderer.render(snap)
                sizes.add(
                    (pix.width(), pix.height(), round(pix.devicePixelRatio(), 1))
                )
                if engine.states.state is PetState.IDLE:
                    break
            expected = (idle.width(), idle.height(), round(idle.devicePixelRatio(), 1))
            self.assertEqual(
                sizes, {expected},
                f"dpr={dpr} 渲染尺寸在状态间漂移：{sizes}（待机 {expected}）",
            )


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_QT, "需要 PySide6")
class GlassStyleTests(ContextMenuTests):
    """液体玻璃风格：所有层级同一套材质，且出现时有动画。"""

    @classmethod
    def setUpClass(cls):
        # 复用 ContextMenuTests 已经装配好的窗口，避免重复创建 QApplication。
        if getattr(ContextMenuTests, "window", None) is None:
            ContextMenuTests.setUpClass()
        cls.window = ContextMenuTests.window

    def test_top_level_is_glass(self):
        from mimi_pet.glass_menu import GlassMenu
        menu = self.window._build_context_menu()
        self.assertIsInstance(menu, GlassMenu)

    def test_all_submenus_are_glass(self):
        from mimi_pet.glass_menu import GlassMenu
        menu = self.window._build_context_menu()
        found = []

        def walk(node):
            for action in node.actions():
                child = action.menu()
                if child is not None:
                    found.append(child)
                    walk(child)

        walk(menu)
        self.assertTrue(found, "没有子菜单")
        self.assertTrue(all(isinstance(m, GlassMenu) for m in found),
                        [type(m).__name__ for m in found])

    def test_stylesheet_is_light_glass(self):
        """旧主题是深紫底；浅色玻璃必须有白色底和深色文字。"""
        from mimi_pet.glass_menu import LIQUID_GLASS_QSS
        self.assertIn("rgba(255, 255, 255, 240)", LIQUID_GLASS_QSS)
        self.assertIn("color: #1A2233", LIQUID_GLASS_QSS)
        self.assertNotIn("rgba(25, 30, 48, 248)", LIQUID_GLASS_QSS)

    def test_show_starts_animation(self):
        menu = self.window._build_context_menu()
        menu.adjustSize()
        menu.show()
        QApplication.processEvents()
        self.assertIsNotNone(menu._anim, "弹出动画未挂载")
        menu.hide()

    def test_glass_paints_transparent_corner(self):
        """圆角外必须是全透明的，否则桌面上会出现方角白块。"""
        menu = self.window._build_context_menu()
        menu.adjustSize()
        menu.show()
        QApplication.processEvents()
        pixmap = menu.grab()
        image = pixmap.toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0, "左上角不透明")
        self.assertEqual(image.pixelColor(image.width() - 1, 0).alpha(), 0, "右上角不透明")
        menu.hide()
