# Mimi 主动交互设计

目标：让 Mimi 像一个**有自己节奏的活物**，而不是一个等指令的播放器。

## 一、四条原则

1. **先看场合，再决定动**
   用户在敲键盘时突然转圈跳舞是打扰；连续两小时没动时还在发呆才是"没活着"。
   每个行为都带前提：静止时长、时段、是否专注、是否在放音乐。

2. **成套，而不是单发**
   一次"念头"由 2–4 个动作加停顿组成（抬头 → 伸懒腰 → 挥手），比随机抽一帧更像有意图。
   步骤之间有 0.4–2.5 秒的停顿，节奏接近真人。

3. **有注意力预算**
   30 分钟最多主动 4 次，同一套行为不连续出现，每套还有自己的冷却（25–120 分钟）。
   宁可少做，也不要让人觉得"这破宠物一直在动"。

4. **永远让位于人**
   用户一动鼠标/键盘，当前行为立即取消，并进入 6 秒静默期。
   拖拽、DSH 正在跑任务、菜单打开时不发起。不排队、不补播。

## 二、判断依据（Context）

| 信号 | 来源 | 作用 |
|---|---|---|
| `idle_s` | 距最后一次用户输入 | 行为的最低门槛；越久越容易触发 |
| `hour` | 本地时间 | 早晨/深夜专属行为 |
| `quiet` | 勿扰开关 | 默认屏蔽全部主动行为 |
| `focus_running` | 专注计时 | 解锁"专注陪伴" |
| `music_playing` | SMTC 播放状态 | 解锁"跟着听" |
| `dsh_busy` | DSH 任务/工具进行中 | 直接屏蔽 |
| `dragging` | 状态机 | 直接屏蔽 |
| `available` | 当前造型支持的动作 | 造型缺某动作时跳过该步 |

## 三、行为目录（32 套）

| 行为 | 触发条件 | 动作序列 | 冷却 |
|---|---|---|---|
| 早上打招呼 | 06:00–11:00，静止≥8s | look_up_slow → stretch_hold → stretch → wave_both_slow | 45 分钟 |
| 犯困 | 22:00–05:00，静止≥10s | yawn → sleepy_nod_pingpong → stretch_back | 45 分钟 |
| 久坐伸展 | 静止≥6s | sit_stretch_hold → sit_stretch → stretch_hold → stretch | 40 分钟 |
| 好奇张望 | 静止≥5s | peek_left → peek_right_reverse → look_up_slow → shrug | 45 分钟 |
| 自娱自乐 | 静止≥14s | spin_cheer → spin_cheer_slow → happy_sway → clap_happy | 60 分钟 |
| 整理自己 | 静止≥7s | tidy_hair_pingpong → ribbon_adjust → tidy_desk_reverse | 45 分钟 |
| 喝口茶 | 静止≥9s | search_pocket → drink_tea_hold → drink_tea | 70 分钟 |
| 跟着听 | 音乐播放中，静止≥3s | headphone_hold → headphone → happy_sway_pingpong | 35 分钟 |
| 打个招呼 | 静止≥3s | wave_hello → bow → nod_slow | 25 分钟 |
| 害羞 | 静止≥6s | shy_look → cover_mouth_slow → hug_self → hug_self_pingpong | 55 分钟 |
| 得意一下 | 静止≥8s | salute_hold → salute → cheer_jump | 50 分钟 |
| 想事情 | 静止≥4s | think_pencil → look_up_slow → nod_slow | 45 分钟 |
| 轻轻挥手 | 静止≥5s | wave_slow → wave_both_slow → nod_slow | 45 分钟 |
| 推球玩 | 静止≥11s | push_ball → spin_cheer_slow | 45 分钟 |
| 接星星 | 静止≥13s | catch_star → clap_happy | 50 分钟 |
| 有点尴尬 | 静止≥7s | fun_facepalm → shrug_pingpong | 65 分钟 |
| 指给你看 | 静止≥6s | point_forward → look_up → nod_yes | 40 分钟 |
| 不太同意 | 静止≥8s | shake_head → look_down_sad → pat_chest_hold → pat_own_chest | 60 分钟 |
| 专注陪伴 | 专注中，静止≥20s | listen_pingpong → nod_slow | 30 分钟 |
| 认真听 | 静止≥4s | listen_tilt → nod_yes | 45 分钟 |
| 等着你 | 静止≥16s | pat_chest_hold → happy_sway_pingpong | 45 分钟 |
| 打盹 | 静止≥45s | sleepy_nod → yawn | 90 分钟 |
| 热情招手 | 静止≥25s | wave_hello → wave_both → cheer_jump | 80 分钟 |
| 喝口水 | 静止≥12s | drink_water_hold → drink_water → nod | 55 分钟 |
| 展示新衣服 | 静止≥10s | turn_show → spin_cheer → clap_happy | 75 分钟 |
| 招招手 | 静止≥4s | wave → nod | 30 分钟 |
| 看看便签 | 静止≥15s | peek_note → look_up_slow → nod_slow | 65 分钟 |
| 有点惊讶 | 静止≥9s | cover_mouth → look_up → nod_yes | 70 分钟 |
| 跪坐着看 | 静止≥18s | kneel_look → peek_right → peek_left_reverse | 85 分钟 |
| 收拾角落 | 静止≥11s | tidy_desk → tidy_hair → shrug | 70 分钟 |
| 蹦一下 | 静止≥16s | spring_reverse → cheer_jump → happy_sway | 60 分钟 |
| 有点低落 | 静止≥25s | look_down_hold → look_down_sad → pat_chest_hold | 120 分钟 |

## 四、触发概率

不是"到点必播"，而是**随静止时间上升的概率门**：

```
p = min(0.55, 0.06 + 静止小时数 × 0.5)
```

刚停下时几乎不触发；一小时没动时约 0.56（封顶 0.55）。
这样同一套逻辑在不同使用强度下都不会显得机械。

## 五、动作覆盖

- 行为目录引用 **66 / 87** 个动作。
- 剩下 21 个不属于主动行为，是**事件/状态驱动**的：
  - 交互反应：摸头、戳脸、摸肚子、击掌、投喂、投喂拒绝、接收拖入
  - 状态过渡：坐下、坐姿待机、站起、躺下、睡着、睡醒、落地恢复
  - 位移：向左走、向右走
  - DSH 事件：任务思考、执行工具、庆祝
  - 其余：`listen`（DSH 倾听态）、`lantern_off`（收尾仪式专用）

这个分工是刻意的：**主动行为只做"没有外部输入时也成立的事"**。

## 六、实现与开关

- 模块：`pet/mimi_app/src/mimi_pet/proactive.py`（Qt-free，可独立测试）
- 接入：`qt_app.py` 的 `on_tick` → `_run_proactive(now)`
- 开关：插件配置 `proactiveEnabled`（默认 true），或环境变量 `MIMI_PROACTIVE=0`
- 测试：`pet/mimi_app/tests/test_proactive.py`（17 项）

## 七、可调参数

| 参数 | 位置 | 默认 |
|---|---|---|
| 两次行为最小间隔 | `MIN_GAP_S` | 90 秒 |
| 预算窗口 / 上限 | `BUDGET_WINDOW_S` / `BUDGET_MAX` | 30 分钟 / 4 次 |
| 单套冷却 | `Routine.cooldown_s` | 25–120 分钟 |
| 概率上限 | `next_action` | 0.55 |

---

# 扩展：时间、桌面与状态栏（第二轮）

## 八、时间感知

`time_sense.py` 把"现在几点"翻译成语义信号，行为目录只关心语义：

| 信号 | 含义 | 用途 |
|---|---|---|
| `phase` | 清晨/上午/中午/下午/傍晚/深夜 | 时段专属行为 |
| `is_weekend` | 周六日 | 周末懒散 |
| `first_seen_today` | 今天第一次见（每天一次） | 见面问候 |
| `welcome_back` | 离开 ≥45 分钟后回来 | 欢迎回来 |
| `hour_changed` | 刚跨过整点 | 整点提醒 |

状态持久化到 `time_sense.json`，否则每次启动都会重新"今天第一次见"。

新增行为：清晨醒来、午间休息、傍晚收工、周末懒散、今天第一次见、欢迎回来、整点提醒。

## 九、桌面交互

`desktop_sense.py` 只暴露**几何信息**（工作区、鼠标位置、前台窗口矩形），
不截图、不读标题、不读内容。

行为目录里用 `@` 前缀表示桌面动作，由接入层翻译成引擎调用：

| 步骤 | 含义 |
|---|---|
| `@walk:left:0.06` | 走到屏幕左侧 6% 处 |
| `@walk:right:0.88` | 走到右侧 88% 处 |
| `@walk:random` | 随机走到 18%–82% |
| `@walk:cursor` | 走到鼠标附近 |
| `@walk:home` | 走回屏幕中央 |
| `@sit` / `@stand` | 坐下 / 站起 |
| `@sleep` | 躺下睡觉 |

新增行为：走到左边探头、走到右边探头、到处走走、坐在边上晃腿、角落里打盹、凑过去看看。

## 十、桌面任务状态条

`task_bar.py`：置顶玻璃条，显示任务标题、当前步骤、`1/3` 进度和专注倒计时。

- 纯 Qt 绘制，**Windows / macOS 同一套代码**
- 不抢焦点（`WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating`）
- 无任务且无专注时自动隐藏
- 点击打开 DSH 面板的对应页
- 位置：屏幕右上角，避让菜单栏/Dock（用 availableGeometry）

## 十一、关掉 DSH 面板

既然桌面状态条已经承载进度显示，DSH 侧的面板可以完全关掉：

```yaml
- id: mimi-pet
  config:
    panelEnabled: false     # DSH 侧不再放入口
    statusBarEnabled: true  # 进度改由桌面条显示
```

对应状态字段 `panel_enabled` 已透传到客户端，`HeaderAction` 会直接返回 null。
