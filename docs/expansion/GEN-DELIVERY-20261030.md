# 造型动作补生成 —— 交付记录（2026-10-30）

数据源：[jobs.json](../../mimi-gen/jobs.json)（90 项动作）+ 清单外补齐 4 项。
生成入口：[run_gen.py](../../mimi-gen/run_gen.py)，安装：[install.py](../../mimi-gen/install.py)。

## 一、结果

| 造型 | 动作数 | 帧数 | pending | mode |
|---|---:|---:|---:|---|
| knit_home 居家针织 | 15 | 1,127 | 0 | daily |
| school_uniform 学院制服 | 15 | 1,170 | 0 | daily |
| winter_scarf 冬日围巾 | 15 | 1,135 | 0 | daily |
| rain_coat 雨天雨衣 | 15 | 1,088 | 0 | daily |
| pajama 晚安睡衣 | 15 | 1,131 | 0 | daily |
| summer_dress 夏日连衣裙 | 15 | 1,074 | 0 | daily |
| festive_red 节日红裙 | 15 | 1,170 | 0 | daily |
| sporty 运动休闲 | 15 | 1,093 | 0 | daily |
| maid_blue 深海女仆 | 15 | 1,139 | 0 | daily |
| star_night 星夜披风 | 15 | 1,163 | 0 | daily |

**150 个动作槽位全部就位，0 处断链，0 失败。** 90 项缺口全部补齐，
`pending_actions` 里的动作项已全部清空（只剩 11 项拖拽 `@tXX` 未做）。

## 二、生成规格

- 接口：H3 turbo，`task=fl2va` 图生视频，首帧 = 各造型待机首帧合成的绿幕参考
- `quality=turbo8`、`short_edge=480`、`aspect_ratio=3:4`、`duration_seconds=5`
- 输出 640×480 → 抽隔帧（step=2）= 62 帧；循环动作走 pingpong = 122 帧
- 后处理：绿幕抠像 → 统一缩放到角色高 267px → 脚底对齐到 `(160,465)` → 320×480 画布 → webp q95
- 每动作帧时长 `FRAME_MS = 83`

与既有 `expansion_v1` 素材的口径完全一致（画布、脚底、角色高、帧时长均对齐）。

## 三、两个自动化质检（本次新增）

### 1. 残手检测

fl2va 会让模型把提示词里的"看不见的手"具象化，抠像后残留一只真实的手。
先改写提示词（只描述她的反应 + 显式声明画面无手），再加自动判据：

- 判据：**头顶以上**、比该造型待机帧多出来的不透明像素
- 阈值 400。实测分离度：真手 832 / 举手庆祝 231 / 摸头 14 / 戳脸 87
- 超阈值自动换种子重生成，每动作最多 3 次

> 先试过「右上整块区域」的口径，会把 celebrate 举手误判为手（1369-1822），弃用。

### 2. 几何畸变检测

`land_recover`（落地）原提示词会让模型把角色**整体缩小俯冲**，
角色高度从 267px 掉到 126px（比值 0.53），相当于画面走样。
改写提示词为「原地屈膝缓冲、不缩小、不靠近镜头」后，比值回到 0.95-0.96。

- 判据：最矮帧 / 中位高度，阈值 0.72
- 最终全部动作最低 0.947

## 四、尾部裁切

按项目既有口径 `scripts/pipeline/tail_motion.py` 的 **v2** 实现（自适应阈值，
不需要每个动作手工设参数）：

    descriptor = RGBA → 预乘 RGB → BOX 缩到 80×120 → GaussianBlur(1.6)
    M[i] = mean(|desc[i+1] − desc[i]|)
    S[i] = median(M[i−2 .. i+2])
    thr  = max(0.20 × max(S), 0.008)      ← 每个动作按自己的峰值定阈值
    keep = clamp(last{i: S[i] > thr} + 1, max(6, ceil(0.35×N)), N)

- 循环动作（walk_left / walk_right / listen / sit_idle）**不裁**，砍尾会断回环接缝
- 共裁掉 **390 个静止尾帧**；单动作保留 28–122 帧，中位数 61 帧
- 裁掉的帧不删除，隔离在 `mimi-gen/trimmed/<outfit>/<action>/`

裁切效果已抽检：`nod`（62→28）、`celebrate`（62→51）动作完整，
最后一帧回到站姿，没有把动作本身切坏。

## 五、清单外的 4 项修复

`presets.json` 原本把这几个标成"已就绪"，实际内容是 `festive_red` 的红裙副本
（按整目录 sha256 与逐帧比对确认）：

| 造型 | 动作 | 问题 | 处理 |
|---|---|---|---|
| rain_coat | walk_right | 红裙副本 | 重新生成 |
| rain_coat | tilt_head | 红裙副本 | 重新生成 |
| sporty | walk_right | 红裙副本 | 重新生成 |
| knit_home | walk_right | 红裙副本（无逐字节重合，是重画的红裙） | 重新生成 |

`rain_coat/walk_left`、`sporty/walk_left` 由各自新的 `walk_right` 水平镜像补齐
（项目既有做法，不占视频额度）。

> 注意：`knit_home/walk_right` 的 122 帧与 festive_red **没有任何一帧逐字节相同**，
> 但画面确实是红裙。所以"逐字节比对"会漏掉这类重画副本，本次改用逐帧视觉抽检复核。

## 六、仍未做

- 拖拽 `@t00/@t05/@t10` 共 11 项（如 `pajama`/`summer_dress`/`maid_blue` 各 3 档、
  `sporty` 2 档）没做，磁盘上这些目录里的内容仍是 festive_red 副本。
  **其中 `rain_coat` t00/t10、`sporty` t10 已在 manifest 里接线，拖拽时会穿错衣服**，
  建议先把这 3 个键从 `presets.json` 的 `drag` 摘除（零素材成本），详见
  [DRAG-IMAGE-ASSETS.md](./DRAG-IMAGE-ASSETS.md)。
- `rain_coat/t05` 目录不存在，且 `pending_actions` 漏登记。

## 七、产物位置

| 内容 | 路径 |
|---|---|
| 装好的帧 | `pet/assets/characters/mimi/expansion_v1/presets/<outfit>/actions/<action>/frames/` |
| manifest | `pet/assets/characters/mimi/expansion_v1/presets.json` |
| 生成状态/审计 | `mimi-gen/state.json` |
| 源视频缓存 | `mimi-gen/mp4/<outfit>/<action>.mp4` |
| 裁掉的尾帧 | `mimi-gen/trimmed/<outfit>/<action>/` |
| 生成日志 | `mimi-gen/gen.out`、`mimi-gen/gen.log` |
| 备份 | `backup-gen-final-20261030-053230/`（manifest + 全量 presets tar） |

manifest 旧版备份在 `presets.json.bak-20261030`。
