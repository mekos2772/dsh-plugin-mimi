# 拖拽图片素材清单

拖拽不是语义动作，是三档力度的**图片序列**。本文件只登记拖拽图片，动作缺口见
[MISSING-OUTFIT-ACTIONS.md](./MISSING-OUTFIT-ACTIONS.md)。

## 一、结构

| 项 | 默认造型（library_v1） | 预设造型（expansion_v1） |
|---|---|---|
| 组织方式 | `drag_poses.sets`，22 个姿势 | `presets/<id>/drag/<tier>/frames/` |
| 档位 | 11 档 `t00`–`t10` × 左右方向 | 只有 3 档：`t00` `t05` `t10` |
| 每档图片 | 1 张静态图 | 31 张 `0001.webp`–`0031.webp` |
| 循环 | 否 | 是 |
| 分方向 | 是（left / right） | 否（左右共用） |
| 帧时长 | — | `FRAME_MS = 83`（约 12 fps） |

- 档位映射：`engine._outfit_drag_spec()` → `hold→t00`、`medium→t05`、`fast→t10`
- spec id 是 `drag_{direction}_{tier}`，但帧来自同一档，所以造型拖拽图天生不分左右
- 缺档时返回 `None`，回退默认素材（不会串衣服）

路径模板：`pet/assets/characters/mimi/expansion_v1/presets/<id>/drag/<tier>/frames/`

## 二、全部 30 档位状态（2026-10-30 修复后）

> 下表「manifest 声明」列已更新为修复后的实际状态；磁盘上的节日红副本已隔离到
> `.trimmed/festive-drag-copies/`，不再位于 `presets/`。详见第六节。


| 造型 | t00 | t05 | t10 | manifest 声明 | 图片内容 |
|---|---|---|---|---|---|
| `knit_home` 居家针织 | 31 | 31 | 31 | t00 t05 t10 | 自己 ✅ |
| `school_uniform` 学院制服 | 31 | 31 | 31 | t00 t05 t10 | 自己 ✅ |
| `winter_scarf` 冬日围巾 | 31 | 31 | 31 | t00 t05 t10 | 自己 ✅ |
| `festive_red` 节日红裙 | 31 | 31 | 31 | t00 t05 t10 | 自己 ✅（副本源头） |
| `star_night` 星夜披风 | 31 | 31 | 31 | t00 t05 t10 | 自己 ✅ |
| `rain_coat` 雨天雨衣 | 0 | 0 | 0 | 未声明（已摘除） | 待重做 |
| `sporty` 运动休闲 | 0 | 0 | 0 | 未声明（已摘除） | 待重做 |
| `pajama` 晚安睡衣 | 0 | 0 | 0 | 未声明 | 待重做 |
| `summer_dress` 夏日连衣裙 | 0 | 0 | 0 | 未声明 | 待重做 |
| `maid_blue` 深海女仆 | 0 | 0 | 0 | 未声明 | **回退默认=自身造型 ✅** |

「磁盘」列为**保留在 `presets/` 的帧数**；被判为节日红副本的已移出，故显示 0。

## 三、14 档待重做清单（434 张）

按 `sha256` 对每档 31 张整体取指纹，得到 15 组内容。其中 3 组跨造型重复，
重复成员**全部是 `festive_red` 的图**。排除项已核对：每档 31 张互不相同
（不是重复单帧凑数）；与各自造型待机帧 0/31 重合（不是拿待机图冒充）。

### 需要重做的 15 档

| # | 造型 | 档位 | 现状 | 张数 |
|---|---|---|---|---:|
| 1 | `rain_coat` | t00 | 节日红副本，**已声明**（会穿错） | 31 |
| 2 | `rain_coat` | t05 | **目录完全缺失** | 31 |
| 3 | `rain_coat` | t10 | 节日红副本，**已声明**（会穿错） | 31 |
| 4 | `sporty` | t00 | 节日红副本，未声明 | 31 |
| 5 | `sporty` | t05 | 节日红副本，未声明 | 31 |
| 6 | `sporty` | t10 | 节日红副本，**已声明**（会穿错） | 31 |
| 7 | `pajama` | t00 | 节日红副本，未声明 | 31 |
| 8 | `pajama` | t05 | 节日红副本，未声明 | 31 |
| 9 | `pajama` | t10 | 节日红副本，未声明 | 31 |
| 10 | `summer_dress` | t00 | 节日红副本，未声明 | 31 |
| 11 | `summer_dress` | t05 | 节日红副本，未声明 | 31 |
| 12 | `summer_dress` | t10 | 节日红副本，未声明 | 31 |
| 13 | `maid_blue` | t00 | 节日红副本，未声明 | 31 |
| 14 | `maid_blue` | t05 | 节日红副本，未声明 | 31 |
| 15 | `maid_blue` | t10 | 节日红副本，未声明 | 31 |

合计 **14 档 × 31 张 = 434 张**（`maid_blue` 3 档已证实为假缺口、免做）。
即：`rain_coat` `sporty` `pajama` `summer_dress` 四套的拖拽图要重做，另 5 套已可用。

### 已可用的 15 档（465 张）

`knit_home`、`school_uniform`、`winter_scarf`、`festive_red`、`star_night` 各 3 档。

## 四、按影响分级

### A. 已接线 → 拖拽时真的变节日红（3 档，零成本可先修）

| 造型 | 档位 | 触发 |
|---|---|---|
| `rain_coat` | t00 | 慢速拖动 |
| `rain_coat` | t10 | 快速拖动 |
| `sporty` | t10 | 快速拖动 |

这 3 档在 `presets.json` 里已登记，运行时必然触发。**无需等新图，先把这 3 个键从
`presets.json` 的 `drag` 摘除**，立刻消除可见 bug。

### B. 磁盘有图但未登记 → 现在不触发，一登记就穿错（11 档）

`pajama` t00/t05/t10、`summer_dress` t00/t05/t10、`maid_blue` t00/t05/t10、`sporty` t00/t05

这 11 档正对应 `presets.json` 里 11 条 `@t00/@t05/@t10` 的 `pending_actions` 登记，
内容全部是节日红副本，不能直接接线。

### C. 连图片都没有（1 档）

`rain_coat` t05：目录不存在。且 `rain_coat` 的 `drag_pending` 为空，
`pending_actions` 里**没有登记这一档**，属漏登记，补图时须一并补登记。

## 五、执行顺序

1. ~~**摘除 3 处错误接线**（A 类，零素材成本）：`rain_coat.drag.t00`、`rain_coat.drag.t10`、`sporty.drag.t10`~~
2. **重做 14 档拖拽图**（434 张，每档 31 帧、`FRAME_MS=83`、循环、不分左右）
3. **逐档登记**进 `presets.json` 的 `drag`，同步清空 `pending_actions` 里对应 `@t00/@t05/@t10`
4. **补 `rain_coat/t05`** 时同时补上 `pending_actions` 登记

## 六、2026-10-30 已执行的修复

**穿错衣服的路径已经全部消除，节日红副本已清零。**

| 项 | 处理 |
|---|---|
| `rain_coat` t00 / t10、`sporty` t10 错误接线 | **已从 manifest 摘除**，改为回退默认素材 |
| 14 档节日红副本（`maid_blue`×3、`pajama`×3、`rain_coat`×2、`sporty`×3、`summer_dress`×3） | **已移出 `presets/`**，隔离到 `pet/assets/characters/mimi/.trimmed/festive-drag-copies/`（未删除） |
| `rain_coat` t05 漏登记 | **已补 `@t05` 到 `pending_actions`** |

### 关键更正：`maid_blue` 的拖拽是假缺口

实测确认：**默认外观（`library_v1`）画的就是深海女仆那套衣服**
（调色板最邻近匹配 `maid_blue` 0.2304，其余造型 0.2545+）。

所以 `maid_blue` 缺的 3 档拖拽**根本不用重做** —— 走 `_outfit_drag_spec()`
回退到默认素材时，显示的就是她自己的衣服。这 3 条 `@t00/@t05/@t10`
已从 `pending_actions` 清除，不再计入缺口。

因此真实待做量从 15 档降为 **14 档（434 张）+ `rain_coat/t05` 补图**：

| 造型 | 待做档位 |
|---|---|
| `pajama` | t00 t05 t10 |
| `summer_dress` | t00 t05 t10 |
| `sporty` | t00 t05 t10 |
| `rain_coat` | t00 t05 t10（t05 连目录都没有） |

### 当前拖拽可用状态

| 造型 | 拖拽 | 说明 |
|---|---|---|
| `knit_home` `school_uniform` `winter_scarf` `festive_red` `star_night` | 自己的 3 档 | 完整可用 |
| `maid_blue` | 回退默认 | **回退即自身造型**，视觉正确 |
| `rain_coat` `sporty` `pajama` `summer_dress` | 回退默认 | 拖拽时短暂显示女仆装，不会串成节日红；待补 14 档后消失 |

## 七、2026-10-30 拖拽补齐完成（生图模型）

拖拽不走视频，走**生图模型**：一张图里排多个姿态，切出来直接当档位。
每档就是 **1 张静态图**（`duration_ms=[34]`、`loop=True`），和默认素材同构。

### 最终状态

| 造型 | 档位 | 来源 |
|---|---|---|
| `knit_home` `school_uniform` `winter_scarf` `festive_red` `star_night` | t00/t05/t10（3 档） | 原有正确素材 |
| `rain_coat` | **t00–t10（11 档）** | 本次生成，4 张 3-up 图切出 12 姿态，按纵横比排序取 11 |
| `sporty` `pajama` `summer_dress` | t00/t05/t10（3 档） | 本次生成，各 1 张 3-up 图 |
| `maid_blue` | 不声明 | 回退默认素材 = 自身造型，视觉正确 |

`pending_actions` 已清零，`presets.json` 里所有 `@tXX` 全部消除。

### 引擎改动

`engine._outfit_drag_spec()` 原来只会把拖拽压成 3 档（hold→t00 / medium→t05 / fast→t10）。
现在改为**优先精确匹配 `pose_index`**：造型提供了 `t00`–`t10` 中的某一档就直接用，
没有才退回三档映射。所以 `rain_coat` 的 11 档能真正逐档加力，其余造型行为不变。

### 切图方法（踩过的坑）

| 方法 | 结果 |
|---|---|
| 等分切割 | 角色宽度不等，切断头发 |
| 列投影取谷 | 发丝把间隙填到非零，谷不够深 |
| seam / 最小能量路径 | 深蓝发色与透明底对比不足，会穿脸 |
| 连通域归属 | 跨界发丝被分给相邻角色，右侧被直线削掉 |
| **完全透明空列 + 人工校验** | ✅ 采用 |

关键是**生成时把角色画小**：提示词要求"figure occupies only a small part of the canvas,
at least one full body width of empty space between neighbours"。角色缩小后角色之间必然
存在整列全透明的空档，在空档中点下刀绝不碰角色。

另外两条经验：
- **6 个一排不可行**：越靠右的角色头发越长，会压住邻居，找不到干净空档。**3 个一排**最稳。
- `normalize()` 不能只按高度缩放：拖拽越往后越横躺，只按高度算会让宽度超出 512 画布并被裁掉；
  改成同时约束宽高（`scale = min(max_h/h, max_w/w)`）。

### 素材位置

- 生成源图：`mimi-gen/drag/<outfit>_3up_<n>.png`
- 切图工具：`mimi-gen/cut.py`（按显式像素切割线切）
- 安装脚本：`mimi-gen/install_drag.py`（11 档）、`mimi-gen/install_drag3.py`（3 档）
