# 造型缺失素材清单（101 项：动作 90 + 拖拽 11）

数据来源：`pet/assets/characters/mimi/expansion_v1/presets.json` 的 `pending_actions` 字段，脚本统计，未人工增删。

- `pending_actions` 原始条目合计 **101**
- 其中语义动作 **90** 项（去重 15 个动作）
- 其中拖拽姿态 **11** 项（`@t00 / @t05 / @t10`）

生成前请先看下面的「拖拽部分的实测差异」，那里有 3 项是**接线错误**而不是缺素材。

## 一、总览

| 造型 | id | 就绪动作 | 缺动作 | 缺拖拽档 |
|---|---|---:|---:|---:|
| 学院制服 | `school_uniform` | 15 | 0 | 0 |
| 节日红裙 | `festive_red` | 15 | 0 | 0 |
| 星夜披风 | `star_night` | 15 | 0 | 0 |
| 居家针织 | `knit_home` | 4 | 11 | 0 |
| 冬日围巾 | `winter_scarf` | 6 | 9 | 0 |
| 雨天雨衣 | `rain_coat` | 3 | 12 | 0（但见下方接线错误） |
| 运动休闲 | `sporty` | 2 | 13 | 2 |
| 晚安睡衣 | `pajama` | 0 | 18 | 3 |
| 夏日连衣裙 | `summer_dress` | 0 | 18 | 3 |
| 深海女仆 | `maid_blue` | 0 | 18 | 3 |

## 二、动作缺口（90 项，按动作汇总）

| 动作 | 缺几套 | 缺失的造型 |
|---|---:|---|
| `celebrate` | 7 | knit_home, maid_blue, pajama, rain_coat, sporty, summer_dress, winter_scarf |
| `cheek_poke` | 7 | 同上 |
| `clap` | 7 | 同上 |
| `head_pat` | 7 | 同上 |
| `land_recover` | 7 | 同上 |
| `nod` | 7 | 同上 |
| `wave` | 7 | 同上 |
| `yawn` | 7 | 同上 |
| `listen` | 6 | knit_home, maid_blue, pajama, rain_coat, sporty, summer_dress |
| `look_up` | 6 | knit_home, maid_blue, pajama, rain_coat, sporty, summer_dress |
| `shy` | 5 | maid_blue, pajama, rain_coat, sporty, summer_dress |
| `sit_idle` | 5 | maid_blue, pajama, rain_coat, sporty, summer_dress |
| `walk_left` | 5 | knit_home, maid_blue, pajama, summer_dress, winter_scarf |
| `tilt_head` | 4 | maid_blue, pajama, sporty, summer_dress |
| `walk_right` | 3 | maid_blue, pajama, summer_dress |

合计 90 项 = 8×7 + 2×6 + 3×5 + 4 + 3。

## 三、拖拽缺口（11 项）

拖拽分三档受力，映射见 `engine._outfit_drag_spec()`：

| 档位 | 语义 | 触发 |
|---|---|---|
| `t00` | hold | 慢速拖动 |
| `t05` | medium | 中速拖动 |
| `t10` | fast | 快速拖动 |

缺档时 `_outfit_drag_spec()` 返回 `None`，回退到默认素材（而非错衣服）。

| 造型 | 缺档 |
|---|---|
| 晚安睡衣 `pajama` | `t00` `t05` `t10` |
| 夏日连衣裙 `summer_dress` | `t00` `t05` `t10` |
| 深海女仆 `maid_blue` | `t00` `t05` `t10` |
| 运动休闲 `sporty` | `t00` `t05` |

合计 11 项。

### 拖拽部分的实测差异（重要）

逐字节比对 `presets/<id>/drag/<tier>/frames/*.webp` 后发现：

1. **`pajama` / `summer_dress` / `maid_blue` / `sporty` / `rain_coat` 目录里已有的拖拽帧，内容是 `festive_red`（节日红裙）那一套，逐字节相同。**
   它们是节日红素材的副本，不是各自的造型。
2. **其中 3 档已经被 manifest 声明并会在拖拽时真的用上**，属于接线错误：
   - `rain_coat` 的 `t00`、`t10`
   - `sporty` 的 `t10`
   结果是：**穿雨衣或运动装被拖动时，会突然变成节日红裙** —— 正是 `engine._playable_spec()` 注释里要避免的「换装后突然变回原衣服」。
3. **真正连文件都没有的只有 `rain_coat/t05` 一档**（目录不存在，且 `pending_actions` 也没登记它，属于漏登记）。

所以拖拽这 11 项应拆成：

| 类型 | 数量 | 明细 |
|---|---:|---|
| 需要重新生成素材 | 15 档 | pajama×3、summer_dress×3、maid_blue×3、sporty×3、rain_coat×2，另 `rain_coat/t05` 补登记 |
| 需要先修正 manifest（接线错误） | 3 档 | `rain_coat/t00`、`rain_coat/t10`、`sporty/t10` |

独立且内容正确的拖拽素材只有 5 套：`knit_home`、`school_uniform`、`winter_scarf`、`festive_red`、`star_night`。

## 四、逐套任务表

### knit_home 居家针织 —— 缺 11 动作
`celebrate` `cheek_poke` `clap` `head_pat` `land_recover` `listen` `look_up` `nod` `walk_left` `wave` `yawn`

### winter_scarf 冬日围巾 —— 缺 9 动作
`celebrate` `cheek_poke` `clap` `head_pat` `land_recover` `nod` `walk_left` `wave` `yawn`

### rain_coat 雨天雨衣 —— 缺 12 动作 + 1 拖拽
动作：`celebrate` `cheek_poke` `clap` `head_pat` `land_recover` `listen` `look_up` `nod` `shy` `sit_idle` `wave` `yawn`
拖拽：`t05`（无文件、未登记）；`t00`、`t10` 需先断开错误接线

### sporty 运动休闲 —— 缺 13 动作 + 2 拖拽
动作：`celebrate` `cheek_poke` `clap` `head_pat` `land_recover` `listen` `look_up` `nod` `shy` `sit_idle` `tilt_head` `wave` `yawn`
拖拽：`t00` `t05`；`t10` 需先断开错误接线

### pajama 晚安睡衣 —— 缺 18 项
动作（15）：`celebrate` `cheek_poke` `clap` `head_pat` `land_recover` `listen` `look_up` `nod` `shy` `sit_idle` `tilt_head` `walk_left` `walk_right` `wave` `yawn`
拖拽（3）：`t00` `t05` `t10`

### summer_dress 夏日连衣裙 —— 缺 18 项
动作（15）：同 `pajama`
拖拽（3）：`t00` `t05` `t10`

### maid_blue 深海女仆 —— 缺 18 项
动作（15）：同 `pajama`
拖拽（3）：`t00` `t05` `t10`

### 已完成（0 缺失）
`school_uniform`、`festive_red`、`star_night`：15 动作 + 3 拖拽档全齐。

## 五、三个连带影响

> **2026-10-30 补充：以下 1–2 已在代码层解决，不再依赖补素材。**
> 缺动作现在会沿语义近似链落到这套衣服自己的另一个动作
> （`outfit_actions.OUTFIT_ACTION_FALLBACKS`），所以"缺素材"只影响动作是否
> 精准，不再导致"点了没反应"。本节保留为素材规划依据：下面列的 90 项依然是
> **精度缺口**，补齐后对应交互会用上本动作而不是近似动作。

1. **缺 `walk_right` 的造型无法走路**：`PetEngine.can_walk()` 判 `supports("walk_right")`，
   `maid_blue` / `pajama` / `summer_dress` 三套补齐前 `start_walk()` 恒为 False。
2. **缺动作的造型点击后「没反应」**（已由近似链解决）：`_playable_spec()` 曾经对穿着造型
   缺该动作直接返回 `None`。缺 `wave`/`nod`/`cheek_poke` 的 7 套，握手、点头、戳脸会静默留待机。
3. **拖拽会把雨衣/运动装变成节日红**：见第三节，这是当前唯一会「穿错衣服」的路径。
   （已修：造型没有拖拽档位时改用**这套衣服自己的**待机帧，不再回退默认素材。）

## 六、建议生成顺序

1. 先修 3 处接线错误（零素材成本）：`rain_coat/t00`、`rain_coat/t10`、`sporty/t10` 从 manifest 摘除。
2. 补 8 个全覆盖动作（`celebrate` `cheek_poke` `clap` `head_pat` `land_recover` `nod` `wave` `yawn`）：8 × 7 = 56 项，一次解决 7 套造型的主要交互。
3. 补 `listen` `look_up`（6 × 2 = 12 项）。
4. 补 `shy` `sit_idle` `walk_left`（5 × 3 = 15 项）。
5. 补 `walk_right`（3 项）、`tilt_head`（4 项）。
6. 最后补拖拽素材 15 档 + 登记 `rain_coat/t05`。
