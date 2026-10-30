"""F01–F08 端到端验收：走真实服务、真实文件，不 mock。

用法：
    PYTHONPATH=pet/mimi_app/src python scripts/verify/acceptance_expansion.py

全部通过时退出码 0；任一项失败退出 1，方便接进 CI。
"""
import os, sys, tempfile
from pathlib import Path
root = Path(tempfile.mkdtemp())
for k, name in (("MIMI_NOTES_PATH","n.json"),("MIMI_TASKS_PATH","t.json"),
                ("MIMI_SCENE_PATH","s.json"),("MIMI_WRAPUP_PATH","w.json"),
                ("MIMI_HEALTH_PATH","h.json")):
    os.environ[k] = str(root / name)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pet" / "mimi_app" / "src"))
from mimi_pet.expansion_hub import ExpansionHub

results = []
def check(fid, name, ok, detail=""):
    results.append((fid, name, bool(ok), detail))

hub = ExpansionHub()

# F01
ok, msg, nid = hub.add_note("验收便签\n第二行", tags=["测试"])
check("F01", "新建便签", ok and hub.notes.count() == 1, msg)
check("F01", "搜索命中", len(hub.search_notes("验收")) == 1)
check("F01", "标签生效", hub.notes.get(nid).tags == ["测试"])
hub.notes.delete(nid); check("F01", "删除进回收站", len(hub.notes.trashed()) == 1)
hub.notes.restore(nid); check("F01", "恢复", hub.notes.count() == 1)

# F02
ok, msg, tid = hub.start_task("验收任务", ["一", "二", "三"])
check("F02", "建三步任务", ok and len(hub.tasks.get(tid).steps) == 3, msg)
hub.consume_animation()
ok, msg = hub.complete_task_step(tid)
check("F02", "完成一步", ok and hub.tasks.get(tid).done_count == 1, msg)
ok, msg = hub.undo_task_step(tid)
check("F02", "撤销", ok and hub.tasks.get(tid).done_count == 0, msg)
check("F02", "单活动任务", hub.tasks.active().id == tid)

# F03
ok, msg, wid = hub.finish_day(task_ids=[tid], next_step="明天验收")
check("F03", "收尾保存", ok and bool(wid), msg)
check("F03", "任务未被取消", hub.tasks.get(tid).status == "active")
check("F03", "明天第一步", hub.wrapups.pending_next_step()[1] == "明天验收")

# F04
ok, msg = hub.music.toggle()
check("F04", "默认关→开", ok and hub.music.enabled, msg)
check("F04", "静态无摆动", not hub.music.motion()["enabled"])

# F06
ok, msg = hub.start_toy("ball")
hub.toys.bounds = (0.0, 0.0, 256.0, 384.0)
check("F06", "开始玩具", ok and hub.toys.active, msg)
check("F06", "重复开始被拒", not hub.start_toy("star")[0])
ok, msg = hub.stop_toy("user")
check("F06", "收起并清空", ok and not hub.toys.active and hub.toys.session.objects == [])

# F07
for a in ("books", "lamp"): hub.scene.add_item(a)
hub.scene.show()
check("F07", "布景可见", hub.scene.is_visible)
hub.scene.begin_edit(); hub.scene.cancel_edit()
check("F07", "编辑取消不落盘", len(hub.scene.scene.items) == 2)
hub.scene.hide(); check("F07", "一键隐藏", not hub.scene.is_visible)

# F08
hub.health.configure("water", enabled=True, interval_minutes=15, daily_display_cap=2)
summary = hub.health.summary()
check("F08", "计划已启用", summary["plans"][0]["enabled"])
check("F08", "配额受控", summary["plans"][0]["daily_cap"] == 2)

# 跨功能
snap = hub.snapshot()
check("ALL", "快照完整", all(k in snap for k in
      ("notes","tasks","scene","wrapup","health","toys","music")))
check("ALL", "动画不阻塞业务", hub.tasks.get(tid) is not None)

widths = {r[0] for r in results}
for fid in sorted(widths):
    rows = [r for r in results if r[0] == fid]
    ok_n = sum(1 for r in rows if r[2])
    print(f"{fid}: {ok_n}/{len(rows)} 通过")
    for r in rows:
        if not r[2]:
            print(f"   ✗ {r[1]} {r[3]}")
failed = [r for r in results if not r[2]]
print(f"\n总计 {len(results)} 项，失败 {len(failed)}")
sys.exit(1 if failed else 0)
