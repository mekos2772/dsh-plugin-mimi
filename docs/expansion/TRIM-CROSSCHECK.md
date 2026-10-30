# 尾部裁剪 · 独立交叉验证（几何指标）

> 由 `scripts/pipeline/pose_tails.py` 一次跑出，**不引用主指标的任何中间量**。
> 主指标 = 80x120 预乘模糊 RGBA 帧差（`tail_motion.py`）；本报告 = alpha 掩膜几何 + 掩膜内 RGB 运动。
> 两者唯一共享的输入是原始 webp 字节与 `alpha > 8` 这个掩膜口径。
> 字段 `N` / `keep` 与主指标同名同义，可直接算 `keep_diff = keep_crosscheck - keep_main`。

## 0. 口径与测量状态

**口径（Lead 2026-10-29）**：不再要求动作连贯自然收尾；目标是「播完一轮就切」，
主体动作做完立即结束，后面静止帧全砍。

```
still[i] ⇔ iou[i] >= 0.9995  且  rgbmov[i] <= P30(rgbmov)
keep     = 静止尾巴起始帧（不加缓冲），下限 max(6, ceil(0.35*N))
loop=True  →  keep = N
```

**测量状态：`pristine`**。仓库已被 `trim_tails.py --apply` 裁剪过（多余帧移到 `.trimmed/`，
manifest 同步截断），所以默认用 `--state pristine` 做**无损重建**再测：
`library_v1` 取自 `git show HEAD:`；`expansion_v1` 源动作 = 现行 `frames/` ∪ `.trimmed/`；
派生动作（reverse/slow/hold/pingpong）按 `derive_actions._order_indices()` 重新映射。
不复制、不改动任何素材。

## 1. 结论摘要

- 全量 **87** 条（library_v1 23 + expansion_v1 64），无抽样。
- 判为可裁 **10** 条；`keep = N` 不动 **77** 条。
- 置信度：high **77** / medium **4** / low **6**。
- **`ends_off_pose`（裁完回不正）28 条** —— 第 3 节，本次重点。
- 与主指标 `keep` 差异 > 15%N 的冲突 **47** 条 —— 第 4 节。

## 2. 主指标最可能踩坑的三处（交叉验证的主要产出）

### 坑 1：`keep_floor` 把"没有静止尾巴"的动作强行砍掉一半

主指标 `keep = clamp(raw_keep_2F + 3, floor, N)`。当它的 `2*F_tail` 判据在很早就触发时，
`raw_keep_2F` 会小到离谱（`happy_sway` 只有 **2**，等于"第 2 帧动作就结束了"），
此时 `keep` 完全由 floor 决定，**与静止尾巴是否存在毫无关系**。
而纯几何平台（只看 `d_i`，不看任何像素）显示这些长动作要到第 49–61 帧才真的不动：

- 主指标 **floor-bound 21 条**：与纯几何平台的 median |diff| = **25.0 帧**，±3 帧内只有 4 条（19%）。
- 主指标 **未 floor-bound 0 条**：median |diff| = **0 帧**，±3 帧内 0 条（0%）。

| id | N | 主 raw_keep_2F | 主 floor | 主最终 keep | 几何平台 keep | 主多裁 |
|---|---:|---:|---:|---:|---:|---:|
| `fun_facepalm` | 12 | 2 | 6 | 12 | 11 | 0 |
| `land_recover_v4_12` | 12 | 2 | 6 | 12 | 11 | 0 |
| `harness_task_thinking_v1_12` | 12 | 2 | 6 | 12 | 11 | 0 |
| `harness_tool_working_v1_12` | 12 | 2 | 6 | 12 | 11 | 0 |
| `listen` | 54 | 2 | 27 | 27 | 52 | 27 |
| `celebrate` | 54 | 2 | 27 | 27 | 53 | 27 |
| `cheek_poke` | 54 | 15 | 27 | 27 | 53 | 27 |
| `nod` | 37 | 2 | 19 | 19 | 35 | 18 |
| `peek_note` | 62 | 15 | 31 | 31 | 55 | 31 |
| `headphone` | 62 | 8 | 31 | 31 | 61 | 31 |
| `wave_hello` | 62 | 9 | 31 | 31 | 61 | 31 |
| `nod_yes` | 62 | 26 | 31 | 31 | 59 | 31 |
| `cheer_jump` | 62 | 18 | 31 | 31 | 61 | 31 |
| `listen_tilt` | 62 | 12 | 31 | 31 | 52 | 31 |
| `shy_look` | 62 | 12 | 31 | 31 | 60 | 31 |
| `point_forward` | 62 | 21 | 31 | 31 | 49 | 31 |
| `tidy_hair` | 62 | 10 | 31 | 31 | 60 | 31 |
| `happy_sway` | 62 | 2 | 31 | 31 | 61 | 31 |
| `stretch_back` | 62 | 2 | 31 | 31 | 61 | 31 |
| `shrug` | 62 | 22 | 31 | 31 | 54 | 31 |
| `look_down_sad` | 62 | 27 | 31 | 31 | 53 | 31 |

典型例子：

- **`happy_sway`**：主 `raw_keep_2F=2`（等于说"第 2 帧动作就结束了"），floor 31 把它救成 31。
  几何平台是 61——整段都在摇摆，根本没有静止尾巴。若按新口径把 floor 降到 `0.35N=22`，
  会裁掉 40 帧**正在摇摆的帧**，循环直接缺一段。
- **`wave_hello` / `cheer_jump` / `headphone`**：同样是 `raw_keep_2F` 个位数、floor 抬到 31、
  几何平台 61。这三个动作都是"一直在轻微动"，`2*F_tail` 因为尾巴自身噪声高而失效。
- **`look_down_sad`**：主 `raw_keep_2F=27`、floor 31、keep 31；几何平台 53，
  且 `ends_off_pose=True`（d_last/peak≈0.93）。裁到 31 会在低头过程中截断，
  既没做完动作、也没回到站姿。

**根因**：`F_tail = median(M[-12:])` 假设"最后 12 帧近似静止"。
对持续运动或循环动作，这个假设不成立，`F_tail` 被真实运动抬高，`2*F_tail` 阈值随之失守。

### 坑 2：`iou >= 0.9995` 在这批素材上接近不可达

`mask = alpha > 8` 的边界逐帧抖动：webp 重编码会让边缘几十个 px 在 0/1 之间翻转，
相邻帧 IoU 常态落在 0.98–0.999。0.9995 基本只有"两帧字节完全一致"才成立。

| 阈值 | 能测到尾巴的动作数 | 平均尾巴长度 |
|---|---|---|
| `iou >= 0.9995` | 10 / 87 | 0.2 帧 |
| `iou >= 0.999` | 16 / 87 | 0.6 帧 |
| `iou >= 0.998` | 25 / 87 | 1.1 帧 |
| `iou >= 0.995` | 38 / 87 | 2.3 帧 |
| `iou >= 0.99` | 55 / 87 | 3.5 帧 |

按 `0.9995` 只能测出 **10** 条尾巴，平均 0.2 帧，
且几乎全是 `slow` / `hold` 变体——它们的帧是被 `derive_actions.py` **复制**出来的，
字节完全相同，IoU 才会等于 1.0。真实源动作只有 `tidy_desk` / `hug_self` /
`peek_note` / `sleep_lie_down` 四条勉强命中 2 帧。
**建议改用 `iou >= 0.999`（可测到 16 条），或换成"掩膜面积变化 <= 0.1%"**。

### 坑 3：残差是"无结构噪声"还是"真微动"，幅度分不出来

`rgbmov` 只给幅度，但 codec 噪声的幅度可以**高于**真实微动：

| 动作尾段 | rgbmov（幅度） | corr（相邻帧残差相干性） | 实况 |
|---|---:|---:|---|
| `tidy_desk` | ≈ 4.3 | **≈ −0.33** | 图像已冻结，只剩编码抖动 |
| `hug_self` | ≈ 4.1 | **≈ −0.45** | 同上 |
| `stand_up` | ≈ 5.0 | **≈ +0.50** | 真实低幅呼吸式运动 |

真运动的残差在相邻帧间**结构一致**（正相关）；codec 噪声互相独立甚至负相关。
只比幅度会把前两者判成"还在动"、把后者判成"已静止"——正好判反。
本报告用 `corr` 列给出这个诊断，但它不参与 Lead 指定的判据。

## 3. ends_off_pose：裁完回不正的动作（28 条）

判据 `d_last / d_peak > 0.6`：结尾帧相对首帧的姿势偏移已达峰值 60% 以上。
这些动作结束时角色停在低头 / 跪姿 / 坐姿 / 躺姿等非站姿，`d_i` 永不回归 0，
**"d 回落到平台"这类回归判据对它们完全失效**，只能靠平台判据（尾巴内 d 振幅 / 梯度）。

比值和绝对量要一起看，所以下面拆成两组。

### 3.1 明显回不正：`d_last >= 0.15`（14 条）

结尾姿势与首帧有实质差异，裁完直接切回 idle 会"跳"一下。这类要么保留到原长，
要么在运行时补一段过渡（或让 idle 首帧接近该姿势）。

| id | 库 | N | d_last | d_peak | 比值 | tail_len | keep | 结论 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `harness_tool_working_v1_12` | library | 12 | 0.1592 | 0.1592 | **1.00** | 0 | 12 | no-static-tail |
| `search_pocket` | expansion | 62 | 0.9644 | 0.9670 | **1.00** | 0 | 62 | no-static-tail |
| `sleep_lie_down` | library | 79 | 0.8678 | 0.8804 | **0.99** | 2 | 77 | trim |
| `stand_up` | library | 62 | 0.4479 | 0.4573 | **0.98** | 0 | 62 | no-static-tail |
| `wake_up` | library | 79 | 0.8814 | 0.9139 | **0.96** | 0 | 79 | no-static-tail |
| `look_down_sad` | expansion | 62 | 0.1857 | 0.2002 | **0.93** | 0 | 62 | no-static-tail |
| `look_down_hold` | expansion | 64 | 0.1857 | 0.2002 | **0.93** | 0 | 64 | no-static-tail |
| `kneel_look` | expansion | 62 | 0.3219 | 0.3530 | **0.91** | 0 | 62 | no-static-tail |
| `sleepy_nod` | expansion | 62 | 0.3293 | 0.3703 | **0.89** | 0 | 62 | no-static-tail |
| `wave_both` | expansion | 62 | 1.1306 | 1.2867 | **0.88** | 0 | 62 | no-static-tail |
| `wave_both_slow` | expansion | 124 | 1.1306 | 1.2867 | **0.88** | 2 | 122 | trim |
| `sit_down` | library | 62 | 0.3539 | 0.4096 | **0.86** | 0 | 62 | no-static-tail |
| `sit_stretch` | expansion | 62 | 0.3941 | 0.4627 | **0.85** | 0 | 62 | no-static-tail |
| `sit_stretch_hold` | expansion | 64 | 0.3941 | 0.4627 | **0.85** | 0 | 64 | no-static-tail |

### 3.2 略有偏差：比值 > 0.6 但 `d_last < 0.15`（14 条）

`d_last/peak` 比值高（多为动作单调偏移、峰值出现在末帧，比值天然接近 1.0），
但绝对偏移小，切回 idle 基本看不出来。**这两类不要混为一谈**——
只看比值会把 3.2 当成 3.1 处理，白留一堆帧。

| id | 库 | N | d_last | d_peak | 比值 | tail_len | keep | 结论 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `shy_look` | expansion | 62 | 0.1480 | 0.1492 | **0.99** | 0 | 62 | no-static-tail |
| `shrug` | expansion | 62 | 0.1138 | 0.1246 | **0.91** | 0 | 62 | no-static-tail |
| `think_pencil` | expansion | 62 | 0.0846 | 0.0930 | **0.91** | 0 | 62 | no-static-tail |
| `yawn` | expansion | 62 | 0.1300 | 0.1444 | **0.90** | 0 | 62 | no-static-tail |
| `shake_head` | expansion | 62 | 0.0968 | 0.1084 | **0.89** | 0 | 62 | no-static-tail |
| `salute` | expansion | 62 | 0.1498 | 0.1722 | **0.87** | 0 | 62 | no-static-tail |
| `salute_hold` | expansion | 64 | 0.1498 | 0.1722 | **0.87** | 0 | 64 | no-static-tail |
| `listen_tilt` | expansion | 62 | 0.1410 | 0.1624 | **0.87** | 0 | 62 | no-static-tail |
| `point_forward` | expansion | 62 | 0.1402 | 0.1652 | **0.85** | 0 | 62 | no-static-tail |
| `peek_note` | expansion | 62 | 0.0961 | 0.1311 | **0.73** | 2 | 60 | trim |
| `fun_facepalm` | library | 12 | 0.1237 | 0.1772 | **0.70** | 0 | 12 | no-static-tail |
| `look_up` | expansion | 62 | 0.0789 | 0.1193 | **0.66** | 0 | 62 | no-static-tail |
| `look_up_slow` | expansion | 124 | 0.0789 | 0.1193 | **0.66** | 2 | 122 | trim |
| `harness_task_thinking_v1_12` | library | 12 | 0.0566 | 0.0877 | **0.65** | 0 | 12 | no-static-tail |

其余 **59** 条结尾回到接近首帧姿势（`d_last/peak <= 0.6`），
裁完仍有收尾感：`land_recover_v4_12`, `listen`, `sit_idle`, `sleep_loop`, `walk_left`, `walk_right`, `wave`, `celebrate`, `head_pat`, `belly_ticklish`, `cheek_poke`, `high_five`, `file_drop_receive`, `feed_bread` …。

## 4. 与主指标的 keep 差异（> 15%N）


| id | 库 | N | 主 keep | 交叉 keep | 差值 | 差值%N | ends_off_pose |
|---|---|---:|---:|---:|---:|---:|---|
| `headphone` | expansion | 62 | 31 | 62 | +31 | +50.0% | 否 |
| `wave_hello` | expansion | 62 | 31 | 62 | +31 | +50.0% | 否 |
| `nod_yes` | expansion | 62 | 31 | 62 | +31 | +50.0% | 否 |
| `cheer_jump` | expansion | 62 | 31 | 62 | +31 | +50.0% | 否 |
| `listen_tilt` | expansion | 62 | 31 | 62 | +31 | +50.0% | 是 |
| `shy_look` | expansion | 62 | 31 | 62 | +31 | +50.0% | 是 |
| `point_forward` | expansion | 62 | 31 | 62 | +31 | +50.0% | 是 |
| `look_up` | expansion | 62 | 31 | 62 | +31 | +50.0% | 是 |
| `tidy_hair` | expansion | 62 | 31 | 62 | +31 | +50.0% | 否 |
| `happy_sway` | expansion | 62 | 31 | 62 | +31 | +50.0% | 否 |
| `stretch_back` | expansion | 62 | 31 | 62 | +31 | +50.0% | 否 |
| `shrug` | expansion | 62 | 31 | 62 | +31 | +50.0% | 是 |
| `look_down_sad` | expansion | 62 | 31 | 62 | +31 | +50.0% | 是 |
| `peek_note` | expansion | 62 | 31 | 60 | +29 | +46.8% | 是 |
| `listen` | library | 54 | 27 | 54 | +27 | +50.0% | 否 |
| `celebrate` | library | 54 | 27 | 54 | +27 | +50.0% | 否 |
| `cheek_poke` | library | 54 | 27 | 54 | +27 | +50.0% | 否 |
| `catch_star` | expansion | 62 | 36 | 62 | +26 | +41.9% | 否 |
| `salute` | expansion | 62 | 36 | 62 | +26 | +41.9% | 是 |
| `cover_mouth` | expansion | 62 | 39 | 62 | +23 | +37.1% | 否 |
| `spin_cheer` | expansion | 62 | 39 | 62 | +23 | +37.1% | 否 |
| `yawn` | expansion | 62 | 41 | 62 | +21 | +33.9% | 是 |
| `peek_right` | expansion | 62 | 41 | 62 | +21 | +33.9% | 否 |
| `ribbon_adjust` | expansion | 62 | 42 | 62 | +20 | +32.3% | 否 |
| `push_ball` | expansion | 62 | 42 | 62 | +20 | +32.3% | 否 |
| `pat_own_chest` | expansion | 62 | 43 | 62 | +19 | +30.6% | 否 |
| `peek_left` | expansion | 62 | 43 | 62 | +19 | +30.6% | 否 |
| `nod` | library | 37 | 19 | 37 | +18 | +48.6% | 否 |
| `bow` | expansion | 62 | 44 | 62 | +18 | +29.0% | 否 |
| `turn_show` | expansion | 62 | 45 | 62 | +17 | +27.4% | 否 |
| `think_pencil` | expansion | 62 | 46 | 62 | +16 | +25.8% | 是 |
| `search_pocket` | expansion | 62 | 46 | 62 | +16 | +25.8% | 是 |
| `lantern_off` | expansion | 62 | 46 | 62 | +16 | +25.8% | 否 |
| `sit_down` | library | 62 | 47 | 62 | +15 | +24.2% | 是 |
| `sleep_lie_down` | library | 79 | 62 | 77 | +15 | +19.0% | 是 |
| `clap_happy` | expansion | 62 | 48 | 62 | +14 | +22.6% | 否 |
| `wave` | library | 54 | 41 | 54 | +13 | +24.1% | 否 |
| `file_drop_receive` | library | 54 | 41 | 54 | +13 | +24.1% | 否 |
| `feed_refuse` | library | 54 | 41 | 54 | +13 | +24.1% | 否 |
| `wake_up` | library | 79 | 67 | 79 | +12 | +15.2% | 是 |
| `head_pat` | library | 54 | 42 | 54 | +12 | +22.2% | 否 |
| `stretch` | expansion | 62 | 50 | 62 | +12 | +19.4% | 否 |
| `stand_up` | library | 62 | 51 | 62 | +11 | +17.7% | 是 |
| `shake_head` | expansion | 62 | 51 | 62 | +11 | +17.7% | 是 |
| `drink_water` | expansion | 62 | 52 | 62 | +10 | +16.1% | 否 |
| `kneel_look` | expansion | 62 | 52 | 62 | +10 | +16.1% | 是 |
| `drink_tea` | expansion | 62 | 52 | 62 | +10 | +16.1% | 否 |

主要原因（也是主指标可能踩坑的地方）：

1. **主指标用 `2*F_tail` 相对噪声地板**：尾巴自身噪声低时 `F_tail` 很小，判据自动变严，
   于是"看起来还在动"，主 keep 偏大。本报告用 `P30(rgbmov)` 自适应分位，与绝对幅度无关。
2. **主指标带 2 帧收尾缓冲**，本报告按新口径不加缓冲，天然更短。
3. **主指标对 loop 动作没有豁免**，本报告按口径 `keep = N`。
4. **主指标在 `dhi` 型整片变化上没有对应量**（`lantern_off` 的灯光渐变会让它误判在动）。

## 5. 尾巴最长的 10 条

| id | 库 | N | tail_len | tail_start | 建议 keep | 裁掉 | 置信度 |
|---|---|---:|---:|---:|---:|---:|---|
| `sleep_lie_down` | library | 79 | 2 | 78 | 77 | 2 | medium |
| `peek_note` | expansion | 62 | 2 | 61 | 60 | 2 | medium |
| `tidy_desk` | expansion | 62 | 2 | 61 | 60 | 2 | medium |
| `hug_self` | expansion | 62 | 2 | 61 | 60 | 2 | medium |
| `wave_slow` | expansion | 124 | 2 | 123 | 122 | 2 | low |
| `nod_slow` | expansion | 124 | 2 | 123 | 122 | 2 | low |
| `look_up_slow` | expansion | 124 | 2 | 123 | 122 | 2 | low |
| `cover_mouth_slow` | expansion | 124 | 2 | 123 | 122 | 2 | low |
| `spin_cheer_slow` | expansion | 124 | 2 | 123 | 122 | 2 | low |
| `wave_both_slow` | expansion | 124 | 2 | 123 | 122 | 2 | low |

## 6. 不裁的动作

### loop=True，按口径保持 N（10 条）

| id | 库 | N | 尾巴长度 | keep |
|---|---|---:|---:|---:|
| `sit_idle` | library | 18 | 0 | 18 |
| `sleep_loop` | library | 54 | 0 | 54 |
| `walk_left` | library | 62 | 0 | 62 |
| `walk_right` | library | 62 | 0 | 62 |
| `listen_pingpong` | expansion | 122 | 0 | 122 |
| `happy_sway_pingpong` | expansion | 122 | 0 | 122 |
| `shrug_pingpong` | expansion | 122 | 0 | 122 |
| `tidy_hair_pingpong` | expansion | 122 | 0 | 122 |
| `hug_self_pingpong` | expansion | 122 | 0 | 122 |
| `sleepy_nod_pingpong` | expansion | 122 | 0 | 122 |

### 测不到静止尾巴（末段仍在动）（67 条）

| id | 库 | N | 尾巴长度 | keep |
|---|---|---:|---:|---:|
| `fun_facepalm` | library | 12 | 0 | 12 |
| `land_recover_v4_12` | library | 12 | 0 | 12 |
| `harness_task_thinking_v1_12` | library | 12 | 0 | 12 |
| `harness_tool_working_v1_12` | library | 12 | 0 | 12 |
| `listen` | library | 54 | 0 | 54 |
| `sit_down` | library | 62 | 0 | 62 |
| `stand_up` | library | 62 | 0 | 62 |
| `wake_up` | library | 79 | 0 | 79 |
| `wave` | library | 54 | 0 | 54 |
| `celebrate` | library | 54 | 0 | 54 |
| `head_pat` | library | 54 | 0 | 54 |
| `belly_ticklish` | library | 54 | 0 | 54 |
| `cheek_poke` | library | 54 | 0 | 54 |
| `high_five` | library | 54 | 0 | 54 |
| `file_drop_receive` | library | 54 | 0 | 54 |
| `feed_bread` | library | 54 | 0 | 54 |
| `feed_refuse` | library | 54 | 0 | 54 |
| `nod` | library | 37 | 0 | 37 |
| `stretch` | expansion | 62 | 0 | 62 |
| `drink_water` | expansion | 62 | 0 | 62 |
| `think_pencil` | expansion | 62 | 0 | 62 |
| `ribbon_adjust` | expansion | 62 | 0 | 62 |
| `turn_show` | expansion | 62 | 0 | 62 |
| `search_pocket` | expansion | 62 | 0 | 62 |
| `push_ball` | expansion | 62 | 0 | 62 |
| `catch_star` | expansion | 62 | 0 | 62 |
| `headphone` | expansion | 62 | 0 | 62 |
| `lantern_off` | expansion | 62 | 0 | 62 |
| `wave_hello` | expansion | 62 | 0 | 62 |
| `nod_yes` | expansion | 62 | 0 | 62 |
| `shake_head` | expansion | 62 | 0 | 62 |
| `yawn` | expansion | 62 | 0 | 62 |
| `cheer_jump` | expansion | 62 | 0 | 62 |
| `listen_tilt` | expansion | 62 | 0 | 62 |
| `shy_look` | expansion | 62 | 0 | 62 |
| `clap_happy` | expansion | 62 | 0 | 62 |
| `bow` | expansion | 62 | 0 | 62 |
| `point_forward` | expansion | 62 | 0 | 62 |
| `look_up` | expansion | 62 | 0 | 62 |
| `cover_mouth` | expansion | 62 | 0 | 62 |
| `pat_own_chest` | expansion | 62 | 0 | 62 |
| `tidy_hair` | expansion | 62 | 0 | 62 |
| `sit_stretch` | expansion | 62 | 0 | 62 |
| `kneel_look` | expansion | 62 | 0 | 62 |
| `sleepy_nod` | expansion | 62 | 0 | 62 |
| `happy_sway` | expansion | 62 | 0 | 62 |
| `peek_left` | expansion | 62 | 0 | 62 |
| `peek_right` | expansion | 62 | 0 | 62 |
| `spin_cheer` | expansion | 62 | 0 | 62 |
| `drink_tea` | expansion | 62 | 0 | 62 |
| `stretch_back` | expansion | 62 | 0 | 62 |
| `shrug` | expansion | 62 | 0 | 62 |
| `salute` | expansion | 62 | 0 | 62 |
| `wave_both` | expansion | 62 | 0 | 62 |
| `look_down_sad` | expansion | 62 | 0 | 62 |
| `tidy_desk_reverse` | expansion | 62 | 0 | 62 |
| `stretch_hold` | expansion | 64 | 0 | 64 |
| `drink_water_hold` | expansion | 64 | 0 | 64 |
| `salute_hold` | expansion | 64 | 0 | 64 |
| `pat_chest_hold` | expansion | 64 | 0 | 64 |
| `spring_reverse` | expansion | 62 | 0 | 62 |
| `sit_stretch_hold` | expansion | 64 | 0 | 64 |
| `peek_left_reverse` | expansion | 62 | 0 | 62 |
| `peek_right_reverse` | expansion | 62 | 0 | 62 |
| `drink_tea_hold` | expansion | 64 | 0 | 64 |
| `headphone_hold` | expansion | 64 | 0 | 64 |
| `look_down_hold` | expansion | 64 | 0 | 64 |


## 7. 全量明细

`rgb` = 尾巴内 rgbmov 中位数；`corr` = 残差相干性（负/零 = codec 噪声，正 = 真微摆）；
`d_l/pk` = ends_off_pose 比值；`pose` = 纯几何平台 keep（完全不看像素，第二意见）。

| 库 | id | N | keep | tail | len | iou | rgb | corr | d_l/pk | pose | 置信度 | 结论 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| library | `fun_facepalm` | 12 | 12 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.70 | 11 | high | no-static-tail |
| library | `land_recover_v4_12` | 12 | 12 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.59 | 11 | high | no-static-tail |
| library | `harness_task_thinking_v1_12` | 12 | 12 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.65 | 11 | high | no-static-tail |
| library | `harness_tool_working_v1_12` | 12 | 12 | - | 0 | 1.0000 | 0.00 | -1.00 | 1.00 | 11 | high | no-static-tail |
| library | `listen` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.17 | 52 | high | no-static-tail |
| library | `sit_down` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.86 | 47 | high | no-static-tail |
| library | `sit_idle` | 18 | 18 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.23 | 16 | high | loop-protected |
| library | `stand_up` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.98 | 51 | high | no-static-tail |
| library | `sleep_lie_down` | 79 | 77 | 78 | 2 | 0.9996 | 1.11 | -0.33 | 0.99 | 53 | medium | trim |
| library | `sleep_loop` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.35 | 49 | high | loop-protected |
| library | `wake_up` | 79 | 79 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.96 | 75 | high | no-static-tail |
| library | `walk_left` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.37 | 61 | high | loop-protected |
| library | `walk_right` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.38 | 61 | high | loop-protected |
| library | `wave` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.06 | 49 | high | no-static-tail |
| library | `celebrate` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.27 | 53 | high | no-static-tail |
| library | `head_pat` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.05 | 51 | high | no-static-tail |
| library | `belly_ticklish` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.08 | 53 | high | no-static-tail |
| library | `cheek_poke` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.09 | 53 | high | no-static-tail |
| library | `high_five` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.35 | 51 | high | no-static-tail |
| library | `file_drop_receive` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.20 | 52 | high | no-static-tail |
| library | `feed_bread` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.08 | 49 | high | no-static-tail |
| library | `feed_refuse` | 54 | 54 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.04 | 53 | high | no-static-tail |
| library | `nod` | 37 | 37 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.15 | 35 | high | no-static-tail |
| expansion | `stretch` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.04 | 60 | high | no-static-tail |
| expansion | `drink_water` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.22 | 54 | high | no-static-tail |
| expansion | `peek_note` | 62 | 60 | 61 | 2 | 1.0000 | 0.00 | -1.00 | 0.73 | 55 | medium | trim |
| expansion | `think_pencil` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.91 | 51 | high | no-static-tail |
| expansion | `tidy_desk` | 62 | 60 | 61 | 2 | 0.9997 | 4.27 | -0.36 | 0.35 | 50 | medium | trim |
| expansion | `ribbon_adjust` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.05 | 59 | high | no-static-tail |
| expansion | `turn_show` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.08 | 61 | high | no-static-tail |
| expansion | `search_pocket` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 1.00 | 49 | high | no-static-tail |
| expansion | `push_ball` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.18 | 58 | high | no-static-tail |
| expansion | `catch_star` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.56 | 57 | high | no-static-tail |
| expansion | `headphone` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.54 | 61 | high | no-static-tail |
| expansion | `lantern_off` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.48 | 52 | high | no-static-tail |
| expansion | `wave_hello` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.07 | 61 | high | no-static-tail |
| expansion | `nod_yes` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.46 | 59 | high | no-static-tail |
| expansion | `shake_head` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.89 | 52 | high | no-static-tail |
| expansion | `yawn` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.90 | 51 | high | no-static-tail |
| expansion | `cheer_jump` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.07 | 61 | high | no-static-tail |
| expansion | `listen_tilt` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.87 | 52 | high | no-static-tail |
| expansion | `shy_look` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.99 | 60 | high | no-static-tail |
| expansion | `clap_happy` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.37 | 51 | high | no-static-tail |
| expansion | `bow` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.06 | 61 | high | no-static-tail |
| expansion | `point_forward` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.85 | 49 | high | no-static-tail |
| expansion | `look_up` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.66 | 57 | high | no-static-tail |
| expansion | `cover_mouth` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.14 | 56 | high | no-static-tail |
| expansion | `pat_own_chest` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.42 | 58 | high | no-static-tail |
| expansion | `tidy_hair` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.06 | 60 | high | no-static-tail |
| expansion | `sit_stretch` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.85 | 38 | high | no-static-tail |
| expansion | `kneel_look` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.91 | 57 | high | no-static-tail |
| expansion | `sleepy_nod` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.89 | 57 | high | no-static-tail |
| expansion | `happy_sway` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.37 | 61 | high | no-static-tail |
| expansion | `peek_left` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.01 | 61 | high | no-static-tail |
| expansion | `peek_right` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.14 | 60 | high | no-static-tail |
| expansion | `spin_cheer` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.01 | 56 | high | no-static-tail |
| expansion | `drink_tea` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.21 | 49 | high | no-static-tail |
| expansion | `stretch_back` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.03 | 61 | high | no-static-tail |
| expansion | `shrug` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.91 | 54 | high | no-static-tail |
| expansion | `salute` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.87 | 54 | high | no-static-tail |
| expansion | `wave_both` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.88 | 61 | high | no-static-tail |
| expansion | `hug_self` | 62 | 60 | 61 | 2 | 0.9997 | 4.09 | -0.46 | 0.03 | 55 | medium | trim |
| expansion | `look_down_sad` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.93 | 53 | high | no-static-tail |
| expansion | `tidy_desk_reverse` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.36 | 61 | high | no-static-tail |
| expansion | `wave_slow` | 124 | 122 | 123 | 2 | 1.0000 | 0.00 | -1.00 | 0.07 | 122 | low | trim |
| expansion | `nod_slow` | 124 | 122 | 123 | 2 | 1.0000 | 0.00 | -1.00 | 0.46 | 118 | low | trim |
| expansion | `listen_pingpong` | 122 | 122 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.01 | 121 | high | loop-protected |
| expansion | `happy_sway_pingpong` | 122 | 122 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.04 | 121 | high | loop-protected |
| expansion | `stretch_hold` | 64 | 64 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.04 | 62 | high | no-static-tail |
| expansion | `drink_water_hold` | 64 | 64 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.22 | 56 | high | no-static-tail |
| expansion | `salute_hold` | 64 | 64 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.87 | 56 | high | no-static-tail |
| expansion | `shrug_pingpong` | 122 | 122 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.04 | 121 | high | loop-protected |
| expansion | `look_up_slow` | 124 | 122 | 123 | 2 | 1.0000 | 0.00 | -1.00 | 0.66 | 114 | low | trim |
| expansion | `cover_mouth_slow` | 124 | 122 | 123 | 2 | 1.0000 | 0.00 | -1.00 | 0.14 | 112 | low | trim |
| expansion | `pat_chest_hold` | 64 | 64 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.42 | 60 | high | no-static-tail |
| expansion | `tidy_hair_pingpong` | 122 | 122 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.01 | 119 | high | loop-protected |
| expansion | `spring_reverse` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.07 | 61 | high | no-static-tail |
| expansion | `sit_stretch_hold` | 64 | 64 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.85 | 40 | high | no-static-tail |
| expansion | `peek_left_reverse` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.01 | 61 | high | no-static-tail |
| expansion | `peek_right_reverse` | 62 | 62 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.14 | 59 | high | no-static-tail |
| expansion | `spin_cheer_slow` | 124 | 122 | 123 | 2 | 1.0000 | 0.00 | -1.00 | 0.01 | 112 | low | trim |
| expansion | `wave_both_slow` | 124 | 122 | 123 | 2 | 1.0000 | 0.00 | -1.00 | 0.88 | 122 | low | trim |
| expansion | `hug_self_pingpong` | 122 | 122 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.01 | 117 | high | loop-protected |
| expansion | `sleepy_nod_pingpong` | 122 | 122 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.02 | 121 | high | loop-protected |
| expansion | `drink_tea_hold` | 64 | 64 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.21 | 51 | high | no-static-tail |
| expansion | `headphone_hold` | 64 | 64 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.54 | 63 | high | no-static-tail |
| expansion | `look_down_hold` | 64 | 64 | - | 0 | 1.0000 | 0.00 | -1.00 | 0.93 | 55 | high | no-static-tail |

## 8. 局限

- `P30(rgbmov)` 是该动作**自身**分布的 30% 分位。动作全程幅度都低时（如 `cover_mouth`），
  阈值同样低，判据自动变严 —— 符合"能看出还在动就算动"，但也意味着**跨动作不可比**。
- `slow` 变体每帧复制两遍：`iou` 在 1.0 与 <0.9995 间交替，`rgbmov` 交替为 0，
  尾部测量不具物理意义，已压到 low 置信度。
- `loop=True` 的动作尾部是循环接缝，按口径不动。
- 整体亮度渐变（`lantern_off`）会让 `rgbmov` 偏高，方向更保守（少裁），不会误裁。
- 本脚本只测不改；`keep` 是建议值，最终取舍由 Lead 合并两份测量后决定。

## 9. 复现

```bash
PY=/tmp/mimienv/bin/python
$PY scripts/pipeline/pose_tails.py                       # pristine，写 JSON + 本文件
$PY scripts/pipeline/pose_tails.py --report              # 只打印表
$PY scripts/pipeline/pose_tails.py --trace tidy_desk     # 单条逐帧明细
$PY scripts/pipeline/pose_tails.py --state live          # 量裁剪后的现状
```
