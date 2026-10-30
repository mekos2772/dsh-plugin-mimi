"""UI 冒烟测试：把每个交互面都点一遍。

说明：offscreen 平台下 Qt 的事件派发（QTest / sendEvent）会崩溃，这是平台
限制，不是产品缺陷。所以这里**直接调用窗口/状态栏的真实事件处理器**
（mousePressEvent / mouseReleaseEvent / mouseDoubleClickEvent），
传入构造好的 QMouseEvent——走的仍然是产品代码，只是绕开 Qt 的分发器。

覆盖：
1. 本体点击分区（头/脸/肚子/脚/手）→ 对应动作
2. 双击 → 击掌；拖拽 → DRAGGING → 释放
3. 右键 → 菜单；菜单里每个可用条目逐个触发
4. 状态栏：单击、双击极简、拖动
5. 玩具、小空间、主动行为

用法：python scripts/verify/ui_smoke.py [--verbose]
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pet" / "mimi_app" / "src"))

RESULT: list[tuple[str, str, str]] = []
VERBOSE = "--verbose" in sys.argv


def record(group: str, name: str, ok: bool, detail: str = "") -> None:
    RESULT.append((group, name, "PASS" if ok else f"FAIL {detail}"))
    if VERBOSE or not ok:
        print(f"  [{'PASS' if ok else 'FAIL'}] {group} · {name} {detail}")


def _env(tmp: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for key, name in (
        ("MIMI_NOTES_PATH", "notes.json"), ("MIMI_TASKS_PATH", "tasks.json"),
        ("MIMI_SCENE_PATH", "scene.json"), ("MIMI_WRAPUP_PATH", "wrapup.json"),
        ("MIMI_HEALTH_PATH", "health.json"), ("MIMI_OUTFIT_STATE_PATH", "outfit.json"),
        ("MIMI_TIME_PATH", "time.json"), ("MIMI_STATS_PATH", "stats.json"),
        ("MIMI_STATUSBAR_POS", "bar.json"), ("MIMI_STATE_PATH", "state.json"),
    ):
        os.environ[key] = str(tmp / name)


def main() -> int:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QMenu

    tmp = Path(tempfile.mkdtemp(prefix="mimi-ui-"))
    _env(tmp)

    import mimi_pet.qt_app as qa
    from mimi_pet.engine import touch_region
    from mimi_pet.state_machine import PetState
    from mimi_pet.task_bar import BarState, TaskStatusBar

    original_exec = QApplication.exec
    QApplication.exec = lambda *a, **k: 0
    try:
        qa.run()
    except Exception:
        record("启动", "运行", False, traceback.format_exc(limit=3))
    finally:
        QApplication.exec = original_exec

    w = next((x for x in QApplication.topLevelWidgets() if hasattr(x, "hub")), None)
    if w is None:
        print("没有找到 MimiWindow，无法继续")
        return 1
    record("启动", "窗口", True)

    def event(kind, pos, button=Qt.MouseButton.LeftButton,
              buttons=Qt.MouseButton.NoButton):
        local = QPointF(float(pos.x()), float(pos.y()))
        glob = QPointF(float(w.mapToGlobal(pos).x()), float(w.mapToGlobal(pos).y()))
        return QMouseEvent(kind, local, glob, button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    # ------------------------------------------------------------ 1 点击分区
    regions = [
        ("摸头", 0.5, 0.45, "head", "head_pat"),
        ("戳脸", 0.5, 0.58, "face", "cheek_poke"),
        ("摸肚子", 0.5, 0.75, "belly", "belly_ticklish"),
        ("摸脚", 0.5, 0.94, "foot", None),
        ("摸手", 0.1, 0.75, "hand", None),
    ]
    for name, xr, yr, expected_region, expected_action in regions:
        actual_region = touch_region(xr, yr)
        if actual_region != expected_region:
            record("点击分区", name, False, f"区域 {actual_region} != {expected_region}")
            continue
        w.engine.states.state = PetState.IDLE
        w._last_release_at = -1e9  # 重置双击判定，确保这里是单击
        pos = QPoint(int(w.width() * xr), int(w.height() * yr))
        w.mousePressEvent(event(QEvent.Type.MouseButtonPress, pos,
                                buttons=Qt.MouseButton.LeftButton))
        w.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, pos))
        actual = w.engine.player.action.id if w.engine.player.action else None
        if expected_action is None:
            record("点击分区", name, True, "（该区域无专属动作，正常）")
        else:
            record("点击分区", name, actual == expected_action, f"期望 {expected_action} 实际 {actual}")

    # 双击 → 击掌
    w.engine.states.state = PetState.IDLE
    pos = QPoint(w.width() // 2, w.height() // 2)
    w._last_release_at = 0.0
    w.mousePressEvent(event(QEvent.Type.MouseButtonPress, pos,
                            buttons=Qt.MouseButton.LeftButton))
    w.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, pos))
    w._last_release_at = __import__("time").perf_counter()  # 制造"刚点过"
    w.mousePressEvent(event(QEvent.Type.MouseButtonPress, pos,
                            buttons=Qt.MouseButton.LeftButton))
    w.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, pos))
    actual = w.engine.player.action.id if w.engine.player.action else None
    record("本体点击", "双击击掌", actual == "high_five", f"实际 {actual}")

    # 拖拽
    w.engine.states.state = PetState.IDLE
    w.engine.trigger_touch = w.engine.trigger_touch  # no-op，保留可读性
    w.mousePressEvent(event(QEvent.Type.MouseButtonPress, pos,
                            buttons=Qt.MouseButton.LeftButton))
    w._begin_drag(float(pos.x()), float(pos.y()))
    dragging = w.engine.states.state is PetState.DRAGGING
    w.engine.release(w.ground_provider(w.engine.root_x, w.engine.root_y),
                     __import__("time").perf_counter())
    record("本体点击", "拖拽", dragging, f"状态 {w.engine.states.state.name}")

    # ------------------------------------------------------------ 2 右键菜单
    recorded = []
    original_show = w._show_context_menu
    w._show_context_menu = lambda p: recorded.append(p)
    w.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, pos,
                              button=Qt.MouseButton.RightButton))
    w._show_context_menu = original_show
    record("右键菜单", "右键触发", len(recorded) == 1, f"调用 {len(recorded)} 次")

    menu = w._build_context_menu()
    leaves: list[tuple[str, object]] = []

    def walk(node, prefix=""):
        keep = list(node.actions())
        for action in keep:
            if action.isSeparator():
                continue
            label = action.text() or "[状态]"
            if action.menu() is not None:
                walk(action.menu(), prefix + label + " / ")
            else:
                leaves.append((prefix + label, action))

    walk(menu)
    record("右键菜单", "条目数", len(leaves) >= 25, f"{len(leaves)} 个叶子")

    skip = {"退出 Mimi", "检查更新"}
    triggered = failed = 0
    for label, action in leaves:
        short = label.split(" / ")[-1]
        if short in skip or not action.isEnabled():
            continue
        try:
            action.trigger()
            triggered += 1
        except Exception:
            failed += 1
            record("右键菜单", label, False, traceback.format_exc(limit=2))
    record("右键菜单", f"触发 {triggered} 项", failed == 0, f"{failed} 项异常")

    # ------------------------------------------------------------ 3 状态栏
    bars = [x for x in QApplication.topLevelWidgets() if isinstance(x, TaskStatusBar)]
    if not bars:
        record("状态栏", "实例", False, "没有状态栏")
    else:
        bar = bars[0]
        bar.set_state(BarState(title="写文档", step="写正文", done=1, total=3))

        hits = []
        bar.open_requested.connect(lambda: hits.append(1))
        bp = QPoint(bar.width() // 2, bar.height() // 2)

        def bar_event(kind, p):
            return QMouseEvent(kind, QPointF(float(p.x()), float(p.y())),
                               QPointF(float(bar.mapToGlobal(p).x()), float(bar.mapToGlobal(p).y())),
                               Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.NoModifier)

        bar.mousePressEvent(bar_event(QEvent.Type.MouseButtonPress, bp))
        bar.mouseReleaseEvent(bar_event(QEvent.Type.MouseButtonRelease, bp))
        record("状态栏", "单击打开", len(hits) == 1, f"信号 {len(hits)} 次")

        normal = bar.height()
        bar.mouseDoubleClickEvent(bar_event(QEvent.Type.MouseButtonDblClick, bp))
        record("状态栏", "双击极简", bar.height() < normal, f"{normal} → {bar.height()}")
        bar.set_minimal(False)

        before = bar.pos()
        start = QPoint(10, 10)
        middle = QPoint(120, 90)
        bar.mousePressEvent(bar_event(QEvent.Type.MouseButtonPress, start))
        bar.mouseMoveEvent(bar_event(QEvent.Type.MouseMove, middle))
        bar.mouseReleaseEvent(bar_event(QEvent.Type.MouseButtonRelease, middle))
        record("状态栏", "拖动", bar.pos() != before,
               f"{before.x()},{before.y()} → {bar.x()},{bar.y()}")

    # ------------------------------------------------------------ 4 玩具 / 场景
    w.engine.player.stop()
    w.engine.states.state = PetState.IDLE
    # 菜单里的「滚小球」可能已经开了一局，先收干净再测。
    if w.hub.toys.active:
        w.hub.stop_toy("test")
    ok, msg = w.hub.start_toy("ball")
    record("玩具场景", "开滚球", ok, str(msg))
    w.hub.toys.bounds = (0.0, 0.0, float(w.width()), float(w.height()))
    for _ in range(30):
        w.hub.tick(1 / 60)
    record("玩具场景", "推进", w.hub.toys.active is not None)
    w.hub.stop_toy("user")
    record("玩具场景", "收玩具", not w.hub.toys.active)

    w.hub.scene.show()
    record("玩具场景", "打开小空间", w.hub.scene.is_visible)
    w.hub.scene.hide()
    record("玩具场景", "收起小空间", not w.hub.scene.is_visible)

    # ------------------------------------------------------------ 5 造型 / 主动
    outfits = list(w.outfits.catalog.presets())
    record("造型", "目录", len(outfits) >= 10, f"{len(outfits)} 套")
    first = outfits[0]
    ok, _ = w.outfits.select(first.id)
    record("造型", "切换", ok, first.id)

    import time
    w.engine.player.stop()
    w.engine.states.state = PetState.IDLE
    w.proactive.state.busy_until = 0.0
    w.proactive.state.last_act_at = -1e9
    w.proactive.state.current = None

    class _AlwaysRng:
        def random(self):
            return 0.0

        def choices(self, population, weights=None, k=1):
            return [population[0]]

    w.proactive.rng = _AlwaysRng()   # 测试里固定概率门，避免偶发不触发
    w.engine._last_input_s = time.perf_counter() - 1000
    w.run_proactive(time.perf_counter())
    w.run_proactive(time.perf_counter() + 8)
    if w.proactive.pending_routine():
        record("主动行为", "可触发", True, w.proactive.pending_routine())
    else:
        import inspect
        _st = w.engine.states.state.name
        _busy = w.integration.is_busy if hasattr(w, "integration") else "?"
        record("主动行为", "可触发", False, f"状态 {_st}")

    # ------------------------------------------------------------ 6 全量动作
    all_actions = sorted(spec.id for spec in w.engine.library.all())
    failed_actions = []
    for action_id in all_actions:
        w.engine.player.stop()
        w.engine.states.state = PetState.IDLE
        try:
            ok = w.engine.force_perform(action_id)
            if not ok:
                failed_actions.append(action_id)
        except Exception:
            failed_actions.append(action_id)
        w.engine.player.stop()
        w.engine.states.state = PetState.IDLE
    record("全量动作", f"{len(all_actions)} 个", not failed_actions,
           f"失败 {failed_actions[:5]}")

    # ------------------------------------------------------------ 7 全量造型
    preset_rows = []
    for spec in w.outfits.catalog.presets():
        w.engine.player.stop()
        w.engine.states.state = PetState.IDLE
        ok, _ = w.outfits.select(spec.id)
        count = len(spec.supported_actions)
        preset_rows.append((spec.id, ok, count))
    bad = [r for r in preset_rows if not r[1] or r[2] < 15]
    record("全量造型", f"{len(preset_rows)} 套", not bad,
           f"异常 {bad[:3]}")

    # ------------------------------------------------------------ 8 便签/任务
    notes_before = w.hub.notes.count()
    ok, msg, _note_id = w.hub.add_note("UI 冒烟测试便签")
    record("便签任务", "新增便签", ok and w.hub.notes.count() == notes_before + 1, str(msg))

    ok, msg, _task_id = w.hub.start_task("UI 冒烟任务", ["第一步", "第二步"])
    record("便签任务", "建任务", ok, str(msg))
    active = w.hub.tasks.active()
    if active is not None:
        ok, msg = w.hub.complete_task_step(active.id)
        record("便签任务", "完成一步", ok, str(msg))
        ok, msg = w.hub.undo_task_step(active.id)
        record("便签任务", "撤销一步", ok, str(msg))

    try:
        wrap, wrap_msg = w.hub.wrapups.save(next_step="明天继续", scene_choice="keep")
        record("便签任务", "今日收尾", wrap is not None, str(wrap_msg))
    except Exception as exc:  # noqa: BLE001
        record("便签任务", "今日收尾", False, str(exc))

    # ------------------------------------------------------------ 汇总
    total = len(RESULT)
    passed = sum(1 for _, _, r in RESULT if r == "PASS")
    print("\n" + "=" * 58)
    for group in dict.fromkeys(g for g, _, _ in RESULT):
        rows = [r for r in RESULT if r[0] == group]
        ok = sum(1 for r in rows if r[2] == "PASS")
        print(f"{group:8} {ok}/{len(rows)}")
        for _, name, result in rows:
            if result != "PASS":
                print(f"    ✗ {name}: {result}")
    print("=" * 58)
    print(f"总计 {passed}/{total} 项通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
