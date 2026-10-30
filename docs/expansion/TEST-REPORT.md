# 测试报告（2026-10-29）

## 一、结果总览

| 套件 | 项目 | 结果 |
|---|---|---|
| 单元测试 `pet/mimi_app/tests/` | 136 项 | ✅ 全部通过 |
| UI 冒烟 `scripts/verify/ui_smoke.py` | 29 项 | ✅ 全部通过（连跑 3 次稳定） |
| 端到端验收 `scripts/verify/acceptance_expansion.py` | 24 项 | ✅ 全部通过 |
| 静态检查（未定义名字） | 全部模块 | ✅ 干净 |

## 二、UI 冒烟覆盖

| 分组 | 项数 | 内容 |
|---|---|---|
| 启动 | 1 | 窗口构建、服务装配 |
| 点击分区 | 5 | 头 / 脸 / 肚子 / 脚 / 手 的命中区域与动作映射 |
| 本体点击 | 2 | 双击击掌、拖拽进入 DRAGGING 并释放 |
| 右键菜单 | 3 | 右键触发、菜单层级、逐个触发全部可用条目 |
| 状态栏 | 3 | 单击打开、双击极简、拖动改位置 |
| 玩具场景 | 5 | 开滚球、推进、收玩具、开/收小空间 |
| 造型 | 2 | 目录完整、切换成功 |
| 主动行为 | 1 | 强制空闲后可触发 |
| 全量动作 | 1 | **87 个动作逐个播放**，无失败 |
| 全量造型 | 1 | **10 套造型逐个切换**，每套 ≥15 个动作 |
| 便签任务 | 5 | 新增便签、建任务、完成一步、撤销、今日收尾 |

## 三、这轮测试发现并修复的 Bug

### 严重：点击宠物必然抛异常
`qt_window.py` 的 `mouseReleaseEvent` 调用了 `touch_region(...)`，
但只导入了 `PetEngine`，**没有导入 `touch_region`**。

后果：任何一次左键点击都会抛 `NameError`，摸头/戳脸/摸肚子/击掌全部失效。
之前的单元测试只测了引擎层，没有覆盖窗口事件处理，所以一直没发现。

修复：`from .engine import PetEngine, touch_region`。

### 中等：`window.bubble_requested` 从未连接
菜单里的「已记下」「收好了」「撤销了」等反馈气泡全部没有出口。
修复：接到 `BubbleLayer.add_bubble`。

## 四、测试方法说明

offscreen 平台下 Qt 的事件派发（`QTest.mouseClick` / `QApplication.sendEvent`）
会段错误，这是平台限制。因此 UI 冒烟**直接调用窗口与状态栏的真实事件处理器**
（`mousePressEvent` / `mouseReleaseEvent` / `mouseDoubleClickEvent`），
传入构造好的 `QMouseEvent`：走的仍是产品代码，只绕开 Qt 的分发器。

## 五、复现命令

```bash
PYTHONPATH=pet/mimi_app/src python -m unittest discover -s pet/mimi_app/tests
python scripts/verify/ui_smoke.py --verbose
PYTHONPATH=pet/mimi_app/src python scripts/verify/acceptance_expansion.py
```
