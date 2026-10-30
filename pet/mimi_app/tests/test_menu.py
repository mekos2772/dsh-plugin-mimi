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
