"""F01–F08 的离线回归测试：不依赖 Qt、不联网、不碰用户真实数据。

运行：
    PYTHONPATH=pet/mimi_app/src python -m unittest pet.mimi_app.tests.test_expansion -v
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from mimi_pet.expansion_store import JsonStore
from mimi_pet.notes import NoteBook, NoteError, BODY_LIMIT
from mimi_pet.task_runs import TaskBoard, TaskError, MAX_STEPS
from mimi_pet.health_plans import HealthPlanner, STATUS_DUE
from mimi_pet.scene_wrapup import SceneService, WrapupService, SceneError
from mimi_pet.toy_box import ToyBox
from mimi_pet.music_companion import MusicCompanion, STATE_LISTENING, STATE_WAITING


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def store(self, name: str) -> JsonStore:
        return JsonStore(self.root / name, schema_version=1)


class NoteBookTests(_Tmp):
    def test_create_and_persist(self):
        book = NoteBook(self.store("notes.json"))
        note, msg = book.create("第一行\n第二行", tags=["工作"])
        self.assertIn("已记下", msg)
        again = NoteBook(self.store("notes.json"))
        loaded = again.get(note.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, "第一行")
        self.assertEqual(loaded.tags, ["工作"])

    def test_empty_and_too_long_rejected(self):
        book = NoteBook(self.store("n.json"))
        with self.assertRaises(NoteError):
            book.create("   ")
        with self.assertRaises(NoteError):
            book.create("x" * (BODY_LIMIT + 1))

    def test_revision_conflict(self):
        """Note 是活对象，调用方必须自己记住提交时的版本号。

        界面上版本号来自快照里的整数，所以这条约定成立；测试同样要先
        把整数取出来，不能等更新完再读 note.revision。
        """
        book = NoteBook(self.store("n.json"))
        note, _ = book.create("内容")
        submit_revision = note.revision
        ok, _ = book.update(note.id, body="新内容", expected_revision=submit_revision)
        self.assertTrue(ok)
        self.assertEqual(book.get(note.id).revision, submit_revision + 1)
        stale, msg = book.update(note.id, body="旧覆盖", expected_revision=submit_revision)
        self.assertFalse(stale)
        self.assertIn("别处", msg)

    def test_trash_restore_purge(self):
        book = NoteBook(self.store("n.json"))
        note, _ = book.create("待删")
        book.delete(note.id)
        self.assertEqual(len(book.trashed()), 1)
        book.restore(note.id)
        self.assertEqual(len(book.list()), 1)
        book.delete(note.id)
        ok, _ = book.purge(note.id)
        self.assertTrue(ok)
        self.assertIsNone(book.get(note.id))

    def test_search_matches_body_and_tags(self):
        book = NoteBook(self.store("n.json"))
        book.create("安装依赖", tags=["文档"])
        book.create("别的事", tags=["杂"])
        self.assertEqual(len(book.search("安装")), 1)
        self.assertEqual(len(book.search("文档")), 1)

    def test_corrupt_file_quarantined(self):
        path = self.root / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        store = JsonStore(path, schema_version=1)
        book = NoteBook(store)
        self.assertEqual(book.count(), 0)
        self.assertIn("备份", store.error)
        self.assertTrue(any(self.root.glob("bad.corrupt-*.json")))


class TaskBoardTests(_Tmp):
    def test_only_one_active(self):
        board = TaskBoard(self.store("t.json"))
        a, _ = board.create("A", ["1"])
        b, _ = board.create("B", ["1"])
        self.assertEqual(board.active().id, b.id)
        self.assertEqual(board.get(a.id).status, "paused")

    def test_step_limit(self):
        board = TaskBoard(self.store("t.json"))
        with self.assertRaises(TaskError):
            board.create("X", ["1"] * (MAX_STEPS + 1))

    def test_complete_and_dedupe(self):
        board = TaskBoard(self.store("t.json"))
        task, _ = board.create("T", ["a", "b"])
        ok, payload = board.complete_step(task.id)
        self.assertTrue(ok)
        self.assertEqual(payload["event"], "task.step.completed")
        first_key = payload["event_key"]
        ok2, payload2 = board.complete_step(task.id, payload["step_id"])
        self.assertTrue(payload2.get("already"))
        self.assertNotEqual(payload2.get("event_key"), first_key)

    def test_undo_restores_current(self):
        board = TaskBoard(self.store("t.json"))
        task, _ = board.create("T", ["a", "b"])
        board.complete_step(task.id)
        board.undo_step(task.id)
        self.assertEqual(board.get(task.id).current_step.text, "a")
        self.assertEqual(board.get(task.id).done_count, 0)

    def test_finish_all(self):
        board = TaskBoard(self.store("t.json"))
        task, _ = board.create("T", ["a"])
        _, payload = board.complete_step(task.id)
        self.assertTrue(payload["finished"])
        self.assertEqual(board.get(task.id).status, "completed")
        self.assertIsNone(board.active())

    def test_cancel_keeps_record(self):
        board = TaskBoard(self.store("t.json"))
        task, _ = board.create("T", ["a"])
        board.cancel(task.id)
        self.assertEqual(board.get(task.id).status, "cancelled")
        self.assertNotIn(task.id, [t.id for t in board.open_tasks()])


class HealthPlannerTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 10, 29, 9, 30).timestamp()
        self.clock = [self.now]
        self.planner = HealthPlanner(self.store("h.json"), clock=lambda: self.clock[0])
        self.planner.ensure_defaults()

    def test_disabled_produces_nothing(self):
        self.assertIsNone(self.planner.due())

    def test_interval_respected(self):
        self.planner.configure("water", enabled=True, interval_minutes=60, daily_display_cap=4)
        first = self.planner.due()
        self.assertIsNotNone(first)
        self.planner.complete(first.id)
        self.clock[0] = self.now + 30 * 60
        self.assertIsNone(self.planner.due())
        self.clock[0] = self.now + 61 * 60
        self.assertIsNotNone(self.planner.due())

    def test_snooze_returns_after_ten_minutes(self):
        self.planner.configure("water", enabled=True, interval_minutes=60)
        occ = self.planner.due()
        self.planner.snooze(occ.id)
        self.clock[0] = self.now + 5 * 60
        self.assertIsNone(self.planner.due())
        self.clock[0] = self.now + 11 * 60
        self.assertIsNotNone(self.planner.due())

    def test_sleep_does_not_flood(self):
        self.planner.configure("water", enabled=True, interval_minutes=60)
        self.planner.complete(self.planner.due().id)
        # 跳过一整天：不应补发
        self.clock[0] = self.now + 26 * 3600
        self.assertIsNone(self.planner.due())

    def test_mute_today(self):
        self.planner.configure("water", enabled=True, interval_minutes=15)
        self.planner.mute_today("water")
        self.clock[0] = self.now + 60
        self.assertIsNone(self.planner.due())

    def test_daily_cap(self):
        self.planner.configure("water", enabled=True, interval_minutes=15, daily_display_cap=1)
        first = self.planner.due()
        self.planner.complete(first.id)
        self.clock[0] = self.now + 20 * 60
        self.assertIsNone(self.planner.due())


class SceneTests(_Tmp):
    def test_edit_cancel_restores(self):
        scene = SceneService(self.store("s.json"))
        scene.add_item("books")
        before = scene.scene.items[0].offset_x
        scene.begin_edit()
        scene.move_item(scene.scene.items[0].id, 40, 10)
        scene.cancel_edit()
        self.assertEqual(scene.scene.items[0].offset_x, before)

    def test_item_limit(self):
        scene = SceneService(self.store("s.json"))
        for asset in ("books", "lamp", "plant"):
            self.assertTrue(scene.add_item(asset)[0])
        self.assertFalse(scene.add_item("cushion")[0])

    def test_wrapup_respects_newer_revision(self):
        scene = SceneService(self.store("s.json"))
        scene.show()
        stale = scene.scene.revision - 1
        ok, _ = scene.apply_wrapup("hide", expected_revision=stale)
        self.assertFalse(ok)
        self.assertTrue(scene.is_visible)


class WrapupTests(_Tmp):
    def test_empty_wrapup_allowed(self):
        wrap = WrapupService(self.store("w.json"))
        item, msg = wrap.save()
        self.assertIn("收好了", msg)
        self.assertEqual(item.local_date, datetime.fromtimestamp(item.created_at).date().isoformat())

    def test_reject_long_next_step(self):
        wrap = WrapupService(self.store("w.json"))
        with self.assertRaises(SceneError):
            wrap.save(next_step="x" * 200)

    def test_pending_next_step(self):
        wrap = WrapupService(self.store("w.json"))
        wrap.save(next_step="先跑测试")
        self.assertEqual(wrap.pending_next_step()[1], "先跑测试")


class ToyBoxTests(_Tmp):
    def test_start_and_timeout(self):
        clock = [100.0]
        box = ToyBox(clock=lambda: clock[0])
        box.bounds = (0.0, 0.0, 320.0, 480.0)
        ok, _ = box.start("ball")
        self.assertTrue(ok)
        clock[0] += 30
        payload = box.tick(1 / 60)
        self.assertFalse(payload["active"])
        self.assertEqual(box.session.objects, [])

    def test_interrupt_is_idempotent(self):
        box = ToyBox(clock=lambda: 100.0)
        box.start("star")
        self.assertTrue(box.interrupt("pickup")[0])
        self.assertFalse(box.interrupt("pickup")[0])

    def test_star_single_on_screen(self):
        box = ToyBox(clock=lambda: 100.0)
        box.bounds = (0.0, 0.0, 320.0, 480.0)
        box.start("star")
        self.assertTrue(box.drop_star()[0])
        self.assertFalse(box.drop_star()[0])

    def test_ball_stays_in_bounds(self):
        clock = [100.0]
        box = ToyBox(clock=lambda: clock[0])
        box.bounds = (0.0, 0.0, 320.0, 480.0)
        box.start("ball")
        for _ in range(180):
            clock[0] += 1 / 60
            payload = box.tick(1 / 60)
            for obj in payload["objects"]:
                self.assertGreaterEqual(obj["x"], -5)
                self.assertLessEqual(obj["x"], 325)


class MusicTests(_Tmp):
    def test_off_by_default(self):
        mc = MusicCompanion(clock=lambda: 0.0)
        self.assertFalse(mc.enabled)
        self.assertEqual(mc.state.state, "off")

    def test_enters_listening_after_stable(self):
        clock = [0.0]
        mc = MusicCompanion(clock=lambda: clock[0])
        mc.toggle(True)
        mc.update(title="A", playing=True)
        clock[0] = 3.0
        mc.update(title="A", playing=True)
        clock[0] = 4.5
        mc.update(title="A", playing=True)
        self.assertEqual(mc.state.state, STATE_LISTENING)

    def test_pause_returns_to_waiting(self):
        clock = [0.0]
        mc = MusicCompanion(clock=lambda: clock[0])
        mc.toggle(True)
        mc.update(title="A", playing=True)
        clock[0] = 3.0
        mc.update(title="A", playing=True)
        clock[0] = 4.5
        mc.update(title="A", playing=True)
        clock[0] = 6.0
        mc.update(title="A", playing=False)
        clock[0] = 13.0
        mc.update(title="A", playing=False)
        self.assertEqual(mc.state.state, STATE_WAITING)
        self.assertTrue(mc.enabled)

    def test_static_strength_no_motion(self):
        clock = [0.0]
        mc = MusicCompanion(clock=lambda: clock[0])
        mc.set_strength("static")
        self.assertFalse(mc.motion()["enabled"])


if __name__ == "__main__":
    unittest.main()


class HubTests(_Tmp):
    """ExpansionHub 的门面行为：动画排队、业务优先。"""

    def setUp(self):
        super().setUp()
        import os
        self._old = {k: os.environ.get(k) for k in (
            "MIMI_NOTES_PATH", "MIMI_TASKS_PATH", "MIMI_SCENE_PATH",
            "MIMI_WRAPUP_PATH", "MIMI_HEALTH_PATH",
        )}
        os.environ["MIMI_NOTES_PATH"] = str(self.root / "n.json")
        os.environ["MIMI_TASKS_PATH"] = str(self.root / "t.json")
        os.environ["MIMI_SCENE_PATH"] = str(self.root / "s.json")
        os.environ["MIMI_WRAPUP_PATH"] = str(self.root / "w.json")
        os.environ["MIMI_HEALTH_PATH"] = str(self.root / "h.json")

    def tearDown(self):
        import os
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def test_note_then_task_flow(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        ok, _msg, note_id = hub.add_note("测试便签")
        self.assertTrue(ok)
        ok, _msg, task_id = hub.start_task("任务", ["a", "b"], note_id=note_id)
        self.assertTrue(ok)
        hub.consume_animation()                     # 丢掉便签那次
        ok, _ = hub.complete_task_step(task_id)
        self.assertTrue(ok)
        anim = hub.consume_animation()
        self.assertEqual(anim.action_id, "catch_star")
        # 同一个事件键不能再排队
        hub.complete_task_step(task_id, hub.tasks.get(task_id).steps[0].id)
        self.assertIsNone(hub.consume_animation())

    def test_snapshot_shape(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        snap = hub.snapshot()
        for key in ("notes", "tasks", "scene", "wrapup", "health", "toys", "music"):
            self.assertIn(key, snap)

    def test_finish_day_keeps_task_records(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        _ok, _msg, task_id = hub.start_task("别取消", ["a"])
        hub.finish_day(next_step="明天")
        self.assertEqual(hub.tasks.get(task_id).status, "active")


class NoteBrowseTests(_Tmp):
    """列表/回收站视图依赖的数据契约。"""

    def test_list_and_search_shape(self):
        book = NoteBook(self.store("n.json"))
        a, _ = book.create("第一条", tags=["标签"])
        b, _ = book.create("第二条")
        book.archive(b.id)
        active = book.list()
        self.assertEqual([n.id for n in active], [a.id])
        archived = book.list(status="archived")
        self.assertEqual([n.id for n in archived], [b.id])
        hits = book.search("标签")
        self.assertEqual([n.id for n in hits], [a.id])

    def test_trash_view_only_shows_trashed(self):
        book = NoteBook(self.store("n.json"))
        keep, _ = book.create("保留")
        gone, _ = book.create("删掉")
        book.delete(gone.id)
        trashed = book.trashed()
        self.assertEqual([n.id for n in trashed], [gone.id])
        self.assertNotIn(gone.id, [n.id for n in book.list()])
        self.assertIn(keep.id, [n.id for n in book.list()])

    def test_task_list_shows_open_only(self):
        board = TaskBoard(self.store("t.json"))
        live, _ = board.create("进行中", ["一"])
        done, _ = board.create("会完成", ["一"])
        board.complete_step(done.id)
        cancelled, _ = board.create("会取消", ["一"])
        board.cancel(cancelled.id)
        ids = [t.id for t in board.open_tasks()]
        self.assertIn(live.id, ids)
        self.assertNotIn(done.id, ids)
        self.assertNotIn(cancelled.id, ids)


class OutfitCatalogTests(_Tmp):
    """造型目录的严格性与降级行为。"""

    def _write(self, payload: dict) -> Path:
        import json
        path = self.root / "presets.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _frame(self, name: str) -> str:
        from PIL import Image
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (512, 768), (0, 0, 0, 0)).save(path)
        return name

    def test_missing_frames_skipped(self):
        from mimi_pet.outfits import OutfitCatalog
        path = self._write({"schema_version": 1, "presets": [{
            "id": "ghost", "name_zh": "幽灵", "mode": "display",
            "status": "usable", "enabled": True,
            "frames": ["nope/0001.webp"],
            "duration_ms": [83],
        }]})
        catalog = OutfitCatalog(path)
        self.assertEqual(len(catalog), 0)
        self.assertTrue(catalog.load_error)

    def test_daily_requires_all_required_actions(self):
        from mimi_pet.outfits import OutfitCatalog
        frame = self._frame("f.webp")
        path = self._write({"schema_version": 1, "presets": [{
            "id": "half", "name_zh": "半套", "mode": "daily",
            "status": "usable", "enabled": True,
            "frames": [frame], "duration_ms": [83],
            "actions": {"walk_right": {"frames": [frame], "duration_ms": [83]}},
        }]})
        catalog = OutfitCatalog(path)
        spec = catalog.get("half")
        self.assertIsNotNone(spec)
        self.assertFalse(spec.is_daily)
        self.assertTrue(spec.missing_actions())

    def test_drag_map_optional(self):
        from mimi_pet.outfits import OutfitCatalog
        frame = self._frame("g.webp")
        path = self._write({"schema_version": 1, "presets": [{
            "id": "nodrag", "name_zh": "无拖拽", "mode": "display",
            "status": "usable", "enabled": True,
            "frames": [frame], "duration_ms": [83],
        }]})
        catalog = OutfitCatalog(path)
        spec = catalog.get("nodrag")
        self.assertFalse(spec.has_drag)
        self.assertEqual(spec.drag_frames("t00"), ())


class SceneDrawTests(_Tmp):
    """布景绘制的坐标契约：物件必须落在窗口内。"""

    def test_default_slots_are_offscreen_safe(self):
        """默认槽位不能把物件放到角色正后方或窗口外。

        角色居中、约 150px 宽，窗口 320px 宽；物件中心必须在
        ±70px 之外（不被身体挡住）且 26..294 之内（不被裁掉）。
        """
        scene = SceneService(self.store("s.json"))
        for asset in ("books", "lamp", "plant"):
            scene.add_item(asset)
        center_x = 160.0
        for item in scene.scene.items:
            if item.asset_id == "cushion":
                continue
            x = center_x + item.offset_x
            self.assertGreater(abs(item.offset_x), 60.0,
                               f"{item.asset_id} 离角色中心太近，会被身体挡住")
            self.assertGreaterEqual(x, 26.0)
            self.assertLessEqual(x, 294.0)

    def test_clamp_keeps_item_visible(self):
        """绘制时的夹取逻辑：极端偏移仍落在窗口内。"""
        def clamp(value, margin, limit):
            return min(max(value, margin), limit)

        origin_x, width = 160.0, 320.0
        for offset in (-999.0, -200.0, 0.0, 200.0, 999.0):
            x = clamp(origin_x + offset, 26.0, width - 26.0)
            self.assertGreaterEqual(x, 26.0)
            self.assertLessEqual(x, width - 26.0)


class FocusLinkTests(_Tmp):
    """任务与专注的联动只做关联，不改对方状态。"""

    def setUp(self):
        super().setUp()
        import os
        self._old = {k: os.environ.get(k) for k in (
            "MIMI_NOTES_PATH", "MIMI_TASKS_PATH", "MIMI_SCENE_PATH",
            "MIMI_WRAPUP_PATH", "MIMI_HEALTH_PATH",
        )}
        os.environ["MIMI_NOTES_PATH"] = str(self.root / "n.json")
        os.environ["MIMI_TASKS_PATH"] = str(self.root / "t.json")
        os.environ["MIMI_SCENE_PATH"] = str(self.root / "s.json")
        os.environ["MIMI_WRAPUP_PATH"] = str(self.root / "w.json")
        os.environ["MIMI_HEALTH_PATH"] = str(self.root / "h.json")

    def tearDown(self):
        import os
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def test_focus_start_links_active_task(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        _ok, _msg, task_id = hub.start_task("写文档", ["列点", "写"])
        ok, message = hub.focus_started("sess-1", minutes=25)
        self.assertTrue(ok)
        self.assertIn("写文档", message)
        self.assertEqual(hub.tasks.get(task_id).focus_session_id, "sess-1")

    def test_focus_start_without_task_is_noop(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        ok, message = hub.focus_started("sess-x", minutes=25)
        self.assertFalse(ok)
        self.assertEqual(message, "")

    def test_focus_finish_does_not_complete_task(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        _ok, _msg, task_id = hub.start_task("任务", ["一", "二"])
        hub.focus_started("sess-2", minutes=25)
        suggestion = hub.focus_finished("sess-2")
        self.assertEqual(suggestion["task_id"], task_id)
        self.assertIn("一", suggestion["current"])
        # 关键：任务状态没被改
        self.assertEqual(hub.tasks.get(task_id).status, "active")
        self.assertEqual(hub.tasks.get(task_id).done_count, 0)

    def test_focus_finish_for_other_session_ignored(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        hub.start_task("任务", ["一"])
        hub.focus_started("sess-3", minutes=25)
        self.assertEqual(hub.focus_finished("sess-other"), {})


class PanelOpsTests(_Tmp):
    """F01/F02 的面板侧操作：白名单、校验、与 hub 一致。"""

    def setUp(self):
        super().setUp()
        import os
        self._old = {k: os.environ.get(k) for k in (
            "MIMI_NOTES_PATH", "MIMI_TASKS_PATH", "MIMI_SCENE_PATH",
            "MIMI_WRAPUP_PATH", "MIMI_HEALTH_PATH",
        )}
        os.environ["MIMI_NOTES_PATH"] = str(self.root / "n.json")
        os.environ["MIMI_TASKS_PATH"] = str(self.root / "t.json")
        os.environ["MIMI_SCENE_PATH"] = str(self.root / "s.json")
        os.environ["MIMI_WRAPUP_PATH"] = str(self.root / "w.json")
        os.environ["MIMI_HEALTH_PATH"] = str(self.root / "h.json")

    def tearDown(self):
        import os
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def _actions(self):
        from mimi_pet.companion import CompanionService
        from mimi_pet.companion_protocol import CompanionActions
        from mimi_pet.companion_store import CompanionStore
        from mimi_pet.expansion_hub import ExpansionHub
        service = CompanionService(CompanionStore(self.root / "c.json"))
        actions = CompanionActions(service)
        actions.hub = ExpansionHub()
        return actions

    def test_note_add_requires_text(self):
        actions = self._actions()
        with self.assertRaises(ValueError):
            actions.execute({"op": "note.add", "text": ""})

    def test_note_add_lands_in_hub(self):
        actions = self._actions()
        result = actions.execute({"op": "note.add", "text": "面板便签"})
        self.assertIn("已记下", result["message"])
        self.assertEqual(actions.hub.notes.count(), 1)

    def test_unknown_op_rejected(self):
        actions = self._actions()
        with self.assertRaises(ValueError):
            actions.execute({"op": "task.complete_everything"})

    def test_task_ops_without_hub_rejected(self):
        from mimi_pet.companion import CompanionService
        from mimi_pet.companion_protocol import CompanionActions
        from mimi_pet.companion_store import CompanionStore
        service = CompanionService(CompanionStore(self.root / "c2.json"))
        actions = CompanionActions(service)
        with self.assertRaises(ValueError):
            actions.execute({"op": "note.add", "text": "x"})


class HealthAnimationTests(_Tmp):
    """健康卡完成时按计划类型选动作，缺素材不阻塞。"""

    def setUp(self):
        super().setUp()
        import os
        self._old = {k: os.environ.get(k) for k in (
            "MIMI_NOTES_PATH", "MIMI_TASKS_PATH", "MIMI_SCENE_PATH",
            "MIMI_WRAPUP_PATH", "MIMI_HEALTH_PATH",
        )}
        os.environ["MIMI_NOTES_PATH"] = str(self.root / "n.json")
        os.environ["MIMI_TASKS_PATH"] = str(self.root / "t.json")
        os.environ["MIMI_SCENE_PATH"] = str(self.root / "s.json")
        os.environ["MIMI_WRAPUP_PATH"] = str(self.root / "w.json")
        os.environ["MIMI_HEALTH_PATH"] = str(self.root / "h.json")

    def tearDown(self):
        import os
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def test_each_kind_has_action(self):
        from mimi_pet.expansion_hub import ANIM_HEALTH_BY_KIND
        for kind in ("water", "eyes", "stretch"):
            self.assertIn(kind, ANIM_HEALTH_BY_KIND)
            self.assertTrue(ANIM_HEALTH_BY_KIND[kind])

    def test_completion_uses_kind_action(self):
        """动作按计划类型选，不写死具体名字（映射会随素材演进）。"""
        from mimi_pet.expansion_hub import ExpansionHub, ANIM_HEALTH_BY_KIND
        hub = ExpansionHub()
        hub.health.configure("stretch", enabled=True, interval_minutes=15)
        due = hub.health_due()
        if due is None:
            self.skipTest("工作时段外，无法产生候选")
        ok, _msg = hub.complete_health(due.id)
        self.assertTrue(ok)
        anim = hub.consume_animation()
        self.assertEqual(anim.action_id, ANIM_HEALTH_BY_KIND["stretch"])


class OutfitPreviewTests(_Tmp):
    """试穿不落盘，取消回原样，保存才记忆。"""

    def _service(self):
        from PIL import Image
        import json
        from mimi_pet.outfits import OutfitCatalog, OutfitService
        frame = self.root / "f.webp"
        Image.new("RGBA", (512, 768), (0, 0, 0, 0)).save(frame, "WEBP")
        payload = {"schema_version": 1, "presets": [
            {"id": pid, "name_zh": pid, "mode": "daily", "status": "usable",
             "enabled": True, "frames": ["f.webp"], "duration_ms": [83],
             "actions": {a: {"frames": ["f.webp"], "duration_ms": [83]}
                         for a in ("walk_right", "head_pat", "cheek_poke",
                                   "celebrate", "land_recover")}}
            for pid in ("a", "b")
        ]}
        path = self.root / "presets.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return OutfitService(
            OutfitCatalog(path), store_path=self.root / "outfit.json"
        )

    def test_preview_does_not_persist(self):
        service = self._service()
        ok, _ = service.preview("a")
        self.assertTrue(ok)
        self.assertEqual(service.selection.id, "")
        self.assertEqual(service.effective().id, "a")

    def test_cancel_restores_default(self):
        service = self._service()
        service.preview("a")
        service.cancel_preview()
        self.assertIsNone(service.effective())
        self.assertEqual(service.preview_id, "")

    def test_select_clears_preview(self):
        service = self._service()
        service.preview("a")
        service.select("b")
        self.assertEqual(service.preview_id, "")
        self.assertEqual(service.selection.id, "b")
        self.assertEqual(service.effective().id, "b")

    def test_preview_rejects_unknown(self):
        service = self._service()
        ok, message = service.preview("nope")
        self.assertFalse(ok)
        self.assertIn("不可用", message)


class TaskQueueTests(_Tmp):
    """排队任务不抢占当前任务。"""

    def setUp(self):
        super().setUp()
        import os
        self._old = {k: os.environ.get(k) for k in (
            "MIMI_NOTES_PATH", "MIMI_TASKS_PATH", "MIMI_SCENE_PATH",
            "MIMI_WRAPUP_PATH", "MIMI_HEALTH_PATH",
        )}
        os.environ["MIMI_NOTES_PATH"] = str(self.root / "n.json")
        os.environ["MIMI_TASKS_PATH"] = str(self.root / "t.json")
        os.environ["MIMI_SCENE_PATH"] = str(self.root / "s.json")
        os.environ["MIMI_WRAPUP_PATH"] = str(self.root / "w.json")
        os.environ["MIMI_HEALTH_PATH"] = str(self.root / "h.json")

    def tearDown(self):
        import os
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def test_queued_task_keeps_active(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        _ok, _msg, first = hub.start_task("当前", ["一"])
        _ok, msg, second = hub.start_task("排队", ["甲"], start=False)
        self.assertIn("先放着", msg)
        self.assertEqual(hub.tasks.active().id, first)
        self.assertEqual(hub.tasks.get(second).status, "queued")

    def test_activate_queued_pauses_previous(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        _ok, _msg, first = hub.start_task("当前", ["一"])
        _ok, _msg, second = hub.start_task("排队", ["甲"], start=False)
        hub.tasks.activate(second)
        self.assertEqual(hub.tasks.get(first).status, "paused")
        self.assertEqual(hub.tasks.active().id, second)

    def test_finish_hint_does_not_open_wrapup(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        _ok, _msg, task = hub.start_task("小任务", ["一"])
        _ok, message = hub.complete_task_step(task)
        self.assertIn("今天到这里", message)
        self.assertIsNone(hub.wrapups.today())


class DerivedActionTests(_Tmp):
    """派生动作的语义约束：只是重排已有帧。"""

    def test_modes_preserve_frame_count_relationship(self):
        """派生只是重排源帧下标：reverse 等长，slow 加倍，hold 前加 2，pingpong 加 n-2。

        hold 以前是「前 4 帧 + 完整动作 + 尾 4 帧定格」（+8）。用户要求动作播完
        就切，尾巴上的重复帧会被看成卡住，所以现在只在前面留 2 帧预备。
        """
        sys_path = str(Path(__file__).resolve().parents[3] / "scripts" / "pipeline")
        import sys
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from derive_actions import _order_indices

        self.assertEqual(len(_order_indices(10, "reverse")), 10)
        self.assertEqual(len(_order_indices(10, "slow")), 20)
        self.assertEqual(len(_order_indices(10, "hold")), 10 + 2)
        self.assertEqual(len(_order_indices(10, "pingpong")), 10 + 8)

    def test_hold_has_no_static_tail(self):
        """hold 变体不该以重复帧收尾（那正是「播完卡住」的观感来源）。"""
        sys_path = str(Path(__file__).resolve().parents[3] / "scripts" / "pipeline")
        import sys
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from derive_actions import _order_indices

        order = _order_indices(10, "hold")
        self.assertEqual(len(set(order[-3:])), 3, f"尾巴出现重复帧：{order[-5:]}")

    def test_reverse_actually_reverses(self):
        import sys
        sys_path = str(Path(__file__).resolve().parents[3] / "scripts" / "pipeline")
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from derive_actions import _order_indices
        self.assertEqual(_order_indices(5, "reverse")[0], 4)
        self.assertEqual(_order_indices(5, "reverse")[-1], 0)

    def test_slow_repeats_each_index(self):
        import sys
        sys_path = str(Path(__file__).resolve().parents[3] / "scripts" / "pipeline")
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from derive_actions import _order_indices
        order = _order_indices(3, "slow")
        self.assertEqual(order, [0, 0, 1, 1, 2, 2])

    def test_derivations_declare_source(self):
        import sys
        sys_path = str(Path(__file__).resolve().parents[3] / "scripts" / "pipeline")
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from derive_actions import DERIVATIONS
        for action_id, spec in DERIVATIONS.items():
            self.assertIn("source", spec)
            self.assertIn("mode", spec)
            self.assertIn(spec["mode"], ("reverse", "slow", "hold", "pingpong"))


class DerivedPlaybackTests(_Tmp):
    """派生动作的播放契约：一次性动作必须能结束，循环动作不应结束。"""

    def test_once_actions_terminate(self):
        import sys
        sys_path = str(Path(__file__).resolve().parents[3] / "scripts" / "pipeline")
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from derive_actions import DERIVATIONS
        once = [k for k, v in DERIVATIONS.items() if not v.get("loop")]
        looped = [k for k, v in DERIVATIONS.items() if v.get("loop")]
        self.assertTrue(once)
        self.assertTrue(looped)

    def test_frame_player_finishes_one_shot(self):
        from mimi_pet.action_library import ActionSpec
        from mimi_pet.frame_player import FramePlayer
        frames = tuple(Path(f"f{i}.webp") for i in range(10))
        spec = ActionSpec(
            id="probe", name_zh="probe", description="", frames=frames,
            duration_ms=(83,) * 10, loop=False, trigger="interaction",
            exit_state="idle",
        )
        player = FramePlayer()
        player.play(spec)
        finished = False
        for _ in range(1000):
            sample = player.advance(1000 / 60)
            if sample and sample.finished:
                finished = True
                break
        self.assertTrue(finished)

    def test_frame_player_loops_forever(self):
        from mimi_pet.action_library import ActionSpec
        from mimi_pet.frame_player import FramePlayer
        frames = tuple(Path(f"f{i}.webp") for i in range(6))
        spec = ActionSpec(
            id="loop", name_zh="loop", description="", frames=frames,
            duration_ms=(83,) * 6, loop=True, trigger="interaction",
            exit_state="idle",
        )
        player = FramePlayer()
        player.play(spec)
        for _ in range(500):
            sample = player.advance(1000 / 60)
            self.assertFalse(sample.finished)


class ContinueTomorrowTests(_Tmp):
    """收尾 → 第二天接着做：建任务且幂等。"""

    def setUp(self):
        super().setUp()
        import os
        self._old = {k: os.environ.get(k) for k in (
            "MIMI_NOTES_PATH", "MIMI_TASKS_PATH", "MIMI_SCENE_PATH",
            "MIMI_WRAPUP_PATH", "MIMI_HEALTH_PATH",
        )}
        os.environ["MIMI_NOTES_PATH"] = str(self.root / "n.json")
        os.environ["MIMI_TASKS_PATH"] = str(self.root / "t.json")
        os.environ["MIMI_SCENE_PATH"] = str(self.root / "s.json")
        os.environ["MIMI_WRAPUP_PATH"] = str(self.root / "w.json")
        os.environ["MIMI_HEALTH_PATH"] = str(self.root / "h.json")

    def tearDown(self):
        import os
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def test_creates_single_step_task(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        hub.finish_day(next_step="先跑测试")
        ok, message, task_id = hub.continue_tomorrow()
        self.assertTrue(ok)
        self.assertIn("先跑测试", message)
        task = hub.tasks.get(task_id)
        self.assertEqual(len(task.steps), 1)
        self.assertEqual(task.status, "active")

    def test_idempotent(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        hub.finish_day(next_step="写文档")
        _ok, _msg, first = hub.continue_tomorrow()
        _ok2, _msg2, second = hub.continue_tomorrow()
        self.assertEqual(first, second)
        self.assertEqual(len(hub.tasks.open_tasks()), 1)

    def test_without_next_step_fails(self):
        from mimi_pet.expansion_hub import ExpansionHub
        hub = ExpansionHub()
        ok, message, task_id = hub.continue_tomorrow()
        self.assertFalse(ok)
        self.assertEqual(task_id, "")


class MusicPollingTests(_Tmp):
    """跟听必须靠持续喂状态推进，不能只喂一次。"""

    def test_single_update_stays_waiting(self):
        from mimi_pet.music_companion import MusicCompanion
        clock = [0.0]
        mc = MusicCompanion(clock=lambda: clock[0])
        mc.toggle(True)
        mc.update(title="歌", playing=True)
        self.assertEqual(mc.state.state, "waiting")

    def test_continuous_updates_reach_listening(self):
        from mimi_pet.music_companion import MusicCompanion
        clock = [0.0]
        mc = MusicCompanion(clock=lambda: clock[0])
        mc.toggle(True)
        for tick in (0.0, 1.0, 2.1, 3.2):
            clock[0] = tick
            mc.update(title="歌", playing=True)
        self.assertEqual(mc.state.state, "listening")
