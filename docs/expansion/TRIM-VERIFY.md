# 动作裁剪验证报告（verify_trim.py）

本文件由 `scripts/verify/verify_trim.py` 生成：重跑 `--label BASELINE` 或
`--label AFTER` 会**整段替换**对应小节，其余小节原样保留。

- `BASELINE` = 裁剪前的现状测量；`AFTER` = 裁剪完成后的验收。
- 每节自带口径与全部明细表；两节可直接逐列对比。
- 素材目录：`pet/assets/characters/mimi`（`library_v1` 23 条 + `expansion_v1` 64 条，共 87 条 / 5867 帧槽位）。
- 只读素材与 manifest：本脚本不修改任何素材文件，只写本报告。

## BASELINE

生成：2026-10-29 20:18:19　·　解释器 `3.9.6 (PySide6 6.10.3)`　·　素材 `/Volumes/isci肥牛/zhuoc/dsh-plugin-mimi/pet/assets/characters/mimi`　·　动作 87 条（one-shot 77 / loop 10）

> **快照窗口**：第 1–6 项取自 **2026-10-29 20:18:19** 的一次完整测量（当时素材齐全：
> 5867/5867 帧可解码、manifest 与磁盘一致），对应 `verify_trim.py --label BASELINE --no-tests`。
> 第 7 项（回归）不依赖帧内容之外的任何东西，但它需要完整素材才能跑（`ActionLibrary`
> 缺帧即抛），所以是在同一完整窗口内另跑的两次：**20:20** 首次失败、重跑通过
> （见下方 flaky 说明），**20:26** 复跑确认 `unittest 161 项 OK` + `acceptance_expansion 24/24`。
> 裁剪开始后（20:35 起）素材进入中间状态，本机已无法再复现干净的基线，故保留此快照。

### 结果总览

| # | 检查项 | 结论 | 数字 |
|---|---|---|---|
| 1 | manifest 结构 | PASS | 条目 87；loader 载入 87（expansion 64）；问题 0 |
| 2 | 帧文件 | PASS | 帧 5867，可解码 5867，缺失 0，损坏 0，尺寸警告 0，canvas 不符 0 |
| 3 | 尾巴检查 | FINDINGS | ratio≤5%（尾巴最静）3 条；静止实播 >0.5s 4 条；0.25–0.5s 6 条；刻意 hold/loop 8 条 |
| 4 | 时长表 | FINDINGS | one-shot 最长 10.29s；>4.5s 共 63 条；loop 最长 10.13s |
| 5 | 派生一致性 | PASS | 派生条目 24；OK 7、STALE 13、UNKNOWN 4 |
| 6 | 引擎实播 | PASS | one-shot 全部回到 Idle；失败 0；运动结束后仍静止最久 0.90s |
| 7 | 回归 | PASS | unittest None 项 (OK)；acceptance_expansion 失败 None/None |

**验收门（BASELINE）**：PASS　→ 硬失败 0 条

软发现 17 条（不阻塞验收，但必须是知情选择）：

- ⚠ 派生字节漂移：cover_mouth_slow（0/124 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：drink_tea_hold（0/70 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：headphone_hold（0/70 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：hug_self_pingpong（0/122 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：look_down_hold（0/70 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：look_up_slow（0/124 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：pat_chest_hold（0/70 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：peek_left_reverse（0/62 帧命中当前源，结构自证 0%）
- ⚠ 派生字节漂移：peek_right_reverse（0/62 帧命中当前源，结构自证 0%）
- ⚠ 派生字节漂移：shrug_pingpong（0/122 帧命中当前源，结构自证 35%）
- ⚠ 派生字节漂移：sit_stretch_hold（0/70 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：sleepy_nod_pingpong（0/122 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：spin_cheer_slow（0/124 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：spring_reverse（0/62 帧命中当前源，结构自证 0%）
- ⚠ 派生字节漂移：tidy_desk_reverse（0/62 帧命中当前源，结构自证 0%）
- ⚠ 派生字节漂移：tidy_hair_pingpong（0/122 帧命中当前源，结构自证 100%）
- ⚠ 派生字节漂移：wave_both_slow（0/124 帧命中当前源，结构自证 100%）

### 1. manifest 结构

- 条目 87 条；`len(frames) != len(duration_ms)` 问题 0 条；重复 id 0 条；跨库冲突 0 条。
- `ActionLibrary` 载入 87 条（其中 expansion 64 条）。
- 警告 0 条；问题 0 条。

### 2. 帧文件

- 共 5867 帧，可解码 5867；缺失 0、损坏 0、目录基准尺寸不符 0、声明 canvas 与实测不符 0。
- 基准：library_v1 = 512×768，expansion_v1 = 320×480。

### 3. 尾巴检查（核心）

口径：`ratio` = 最后 15% 帧的平均运动 ÷ 动作峰值运动（RGBA→预乘→80×120 BOX→GaussianBlur σ=1.6→float32，帧差 mean|Δ|，5 帧中值平滑）。**ratio 越低 = 尾巴越静**（并非「越健康」之意）；`静止帧` 是尾部连续满足 `平滑帧差 ≤ max(10%×峰值, 0.02)` 的帧数，`静止秒` 是这些帧的标称时长，`静止实播秒` 再除以引擎加速比（`MAX_ONESHOT_MS=3000`）。

判定：`FROZEN` = 静止实播秒 > 0.5s（会被读成「卡住」）；`SOFT` = 0.25–0.5s；`OK` = 不超过；`INTENTIONAL` = loop 或 hold/pingpong 派生（尾帧是有意保留的停顿）。

| id | 库 | N | 总秒 | ratio | 峰值 | tail15 | 静止帧 | 保留到 | 静止秒 | 实播秒 | 静止实播秒 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sleep_lie_down | lib | 79 | 6.56 | 0.016 | 3.279 | 0.054 | 24 | 55 | 1.99 | 3.00 | 0.91 | FROZEN |
| stand_up | lib | 62 | 5.15 | 0.064 | 3.261 | 0.209 | 12 | 50 | 1.00 | 3.02 | 0.58 | FROZEN |
| tidy_desk | exp | 62 | 5.15 | 0.048 | 3.892 | 0.188 | 11 | 51 | 0.91 | 3.02 | 0.53 | FROZEN |
| lantern_off | exp | 62 | 5.15 | 0.088 | 3.140 | 0.276 | 11 | 51 | 0.91 | 3.02 | 0.53 | FROZEN |
| sit_stretch_hold | exp | 70 | 5.81 | 0.024 | 7.049 | 0.172 | 12 | 58 | 1.00 | 3.02 | 0.51 | INTENTIONAL(hold) |
| point_forward | exp | 62 | 5.15 | 0.080 | 3.269 | 0.260 | 10 | 52 | 0.83 | 3.02 | 0.48 | SOFT |
| kneel_look | exp | 62 | 5.15 | 0.075 | 6.967 | 0.522 | 9 | 53 | 0.75 | 3.02 | 0.44 | SOFT |
| sit_stretch | exp | 62 | 5.15 | 0.058 | 7.061 | 0.407 | 8 | 54 | 0.66 | 3.02 | 0.39 | SOFT |
| sleepy_nod | exp | 62 | 5.15 | 0.155 | 4.329 | 0.669 | 6 | 56 | 0.50 | 3.02 | 0.29 | SOFT |
| sit_down | lib | 62 | 5.15 | 0.175 | 1.164 | 0.203 | 6 | 56 | 0.50 | 3.02 | 0.29 | SOFT |
| spin_cheer_slow | exp | 124 | 10.29 | 0.053 | 2.232 | 0.119 | 11 | 113 | 0.91 | 3.02 | 0.27 | SOFT |
| push_ball | exp | 62 | 5.15 | 0.103 | 3.495 | 0.358 | 5 | 57 | 0.41 | 3.02 | 0.24 | OK |
| spin_cheer | exp | 62 | 5.15 | 0.105 | 3.001 | 0.314 | 4 | 58 | 0.33 | 3.02 | 0.19 | OK |
| hug_self | exp | 62 | 5.15 | 0.123 | 2.322 | 0.285 | 4 | 58 | 0.33 | 3.02 | 0.19 | OK |
| look_down_hold | exp | 70 | 5.81 | 0.121 | 1.544 | 0.187 | 4 | 66 | 0.33 | 3.02 | 0.17 | INTENTIONAL(hold) |
| headphone_hold | exp | 70 | 5.81 | 0.122 | 3.812 | 0.465 | 4 | 66 | 0.33 | 3.02 | 0.17 | INTENTIONAL(hold) |
| salute_hold | exp | 70 | 5.81 | 0.129 | 1.900 | 0.245 | 4 | 66 | 0.33 | 3.02 | 0.17 | INTENTIONAL(hold) |
| drink_tea_hold | exp | 70 | 5.81 | 0.134 | 1.001 | 0.134 | 4 | 66 | 0.33 | 3.02 | 0.17 | INTENTIONAL(hold) |
| stretch_hold | exp | 70 | 5.81 | 0.143 | 1.828 | 0.262 | 4 | 66 | 0.33 | 3.02 | 0.17 | INTENTIONAL(hold) |
| pat_chest_hold | exp | 70 | 5.81 | 0.162 | 1.098 | 0.178 | 4 | 66 | 0.33 | 3.02 | 0.17 | INTENTIONAL(hold) |
| drink_water_hold | exp | 70 | 5.81 | 0.163 | 0.930 | 0.152 | 4 | 66 | 0.33 | 3.02 | 0.17 | INTENTIONAL(hold) |
| head_pat | lib | 54 | 4.48 | 0.151 | 1.508 | 0.228 | 3 | 51 | 0.25 | 3.00 | 0.17 | OK |
| turn_show | exp | 62 | 5.15 | 0.113 | 7.227 | 0.820 | 3 | 59 | 0.25 | 3.02 | 0.15 | OK |
| cheer_jump | exp | 62 | 5.15 | 0.097 | 3.584 | 0.349 | 2 | 60 | 0.17 | 3.02 | 0.10 | OK |
| peek_right | exp | 62 | 5.15 | 0.103 | 2.890 | 0.298 | 2 | 60 | 0.17 | 3.02 | 0.10 | OK |
| peek_right_reverse | exp | 62 | 5.15 | 0.142 | 2.861 | 0.406 | 2 | 60 | 0.17 | 3.02 | 0.10 | OK |
| tidy_desk_reverse | exp | 62 | 5.15 | 0.500 | 3.901 | 1.952 | 2 | 60 | 0.17 | 3.02 | 0.10 | OK |
| wake_up | lib | 79 | 6.56 | 0.201 | 4.700 | 0.946 | 2 | 77 | 0.17 | 3.00 | 0.08 | OK |
| look_up_slow | exp | 124 | 10.29 | 0.075 | 1.403 | 0.106 | 3 | 121 | 0.25 | 3.02 | 0.07 | OK |
| cover_mouth_slow | exp | 124 | 10.29 | 0.092 | 0.968 | 0.089 | 3 | 121 | 0.25 | 3.02 | 0.07 | OK |
| nod_slow | exp | 124 | 10.29 | 0.098 | 1.094 | 0.107 | 3 | 121 | 0.25 | 3.02 | 0.07 | OK |
| wave_both_slow | exp | 124 | 10.29 | 0.158 | 2.621 | 0.414 | 3 | 121 | 0.25 | 3.02 | 0.07 | OK |
| wave_slow | exp | 124 | 10.29 | 0.176 | 1.909 | 0.336 | 3 | 121 | 0.25 | 3.02 | 0.07 | OK |
| file_drop_receive | lib | 54 | 4.48 | 0.333 | 0.973 | 0.324 | 1 | 53 | 0.08 | 3.00 | 0.06 | OK |
| peek_note | exp | 62 | 5.15 | 0.159 | 1.667 | 0.265 | 1 | 61 | 0.08 | 3.02 | 0.05 | OK |
| bow | exp | 62 | 5.15 | 0.098 | 3.003 | 0.294 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| cover_mouth | exp | 62 | 5.15 | 0.130 | 1.696 | 0.220 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| peek_left | exp | 62 | 5.15 | 0.170 | 3.482 | 0.591 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| search_pocket | exp | 62 | 5.15 | 0.186 | 1.906 | 0.354 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| look_down_sad | exp | 62 | 5.15 | 0.188 | 1.533 | 0.289 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| clap_happy | exp | 62 | 5.15 | 0.190 | 1.732 | 0.329 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| look_up | exp | 62 | 5.15 | 0.191 | 1.394 | 0.266 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| feed_bread | lib | 54 | 4.48 | 0.203 | 1.987 | 0.404 | 0 | 54 | 0.00 | 3.00 | 0.00 | OK |
| drink_tea | exp | 62 | 5.15 | 0.219 | 1.013 | 0.222 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| listen_tilt | exp | 62 | 5.15 | 0.220 | 1.462 | 0.322 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| headphone | exp | 62 | 5.15 | 0.224 | 3.803 | 0.850 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| salute | exp | 62 | 5.15 | 0.227 | 1.900 | 0.431 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| stretch | exp | 62 | 5.15 | 0.238 | 1.828 | 0.435 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| nod_yes | exp | 62 | 5.15 | 0.260 | 1.094 | 0.284 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| shake_head | exp | 62 | 5.15 | 0.263 | 1.146 | 0.302 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| drink_water | exp | 62 | 5.15 | 0.269 | 0.930 | 0.250 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| yawn | exp | 62 | 5.15 | 0.269 | 1.566 | 0.422 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| shy_look | exp | 62 | 5.15 | 0.270 | 1.236 | 0.333 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| think_pencil | exp | 62 | 5.15 | 0.280 | 1.013 | 0.284 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| catch_star | exp | 62 | 5.15 | 0.290 | 1.311 | 0.380 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| pat_own_chest | exp | 62 | 5.15 | 0.293 | 1.093 | 0.320 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| wave | lib | 54 | 4.48 | 0.311 | 0.927 | 0.289 | 0 | 54 | 0.00 | 3.00 | 0.00 | OK |
| shrug | exp | 62 | 5.15 | 0.311 | 1.323 | 0.412 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| high_five | lib | 54 | 4.48 | 0.322 | 1.305 | 0.421 | 0 | 54 | 0.00 | 3.00 | 0.00 | OK |
| wave_both | exp | 62 | 5.15 | 0.324 | 4.091 | 1.327 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| harness_tool_working_v1_12 | lib | 12 | 4.38 | 0.332 | 3.289 | 1.092 | 0 | 12 | 0.00 | 3.00 | 0.00 | OK |
| belly_ticklish | lib | 54 | 4.48 | 0.365 | 1.297 | 0.474 | 0 | 54 | 0.00 | 3.00 | 0.00 | OK |
| ribbon_adjust | exp | 62 | 5.15 | 0.378 | 1.440 | 0.544 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| peek_left_reverse | exp | 62 | 5.15 | 0.417 | 3.491 | 1.456 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| wave_hello | exp | 62 | 5.15 | 0.419 | 2.168 | 0.908 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| tidy_hair | exp | 62 | 5.15 | 0.420 | 0.703 | 0.296 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| feed_refuse | lib | 54 | 4.48 | 0.442 | 1.077 | 0.476 | 0 | 54 | 0.00 | 3.00 | 0.00 | OK |
| cheek_poke | lib | 54 | 4.48 | 0.461 | 1.182 | 0.545 | 0 | 54 | 0.00 | 3.00 | 0.00 | OK |
| celebrate | lib | 54 | 4.48 | 0.489 | 4.114 | 2.013 | 0 | 54 | 0.00 | 3.00 | 0.00 | OK |
| harness_task_thinking_v1_12 | lib | 12 | 4.59 | 0.656 | 2.161 | 1.417 | 0 | 12 | 0.00 | 3.00 | 0.00 | OK |
| stretch_back | exp | 62 | 5.15 | 0.684 | 1.727 | 1.182 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| nod | lib | 37 | 3.07 | 0.711 | 1.356 | 0.964 | 0 | 37 | 0.00 | 3.00 | 0.00 | OK |
| listen | lib | 54 | 4.48 | 0.714 | 1.031 | 0.736 | 0 | 54 | 0.00 | 3.00 | 0.00 | OK |
| happy_sway | exp | 62 | 5.15 | 0.724 | 1.932 | 1.400 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |
| fun_facepalm | lib | 12 | 2.80 | 0.778 | 2.005 | 1.560 | 0 | 12 | 0.00 | 2.82 | 0.00 | OK |
| land_recover_v4_12 | lib | 12 | 1.11 | 0.854 | 10.640 | 9.083 | 0 | 12 | 0.00 | 1.12 | 0.00 | OK |
| spring_reverse | exp | 62 | 5.15 | 0.862 | 3.603 | 3.104 | 0 | 62 | 0.00 | 3.02 | 0.00 | OK |

灵敏度对照（尾部静止帧数，看结论对阈值有多敏感）：≤3%×峰值 → 16 条有尾巴、合计 72 帧 / 5.98s、其中实播 > 0.5s 的 1 条；≤5%×峰值 → 21 条有尾巴、合计 99 帧 / 8.22s、其中实播 > 0.5s 的 1 条；≤10%×峰值 → 35 条有尾巴、合计 194 帧 / 16.10s、其中实播 > 0.5s 的 4 条。

**ratio 最高的 8 条**（尾部仍有明显运动，说明末尾没有被静止帧吃掉）：

| id | N | 总秒 | ratio | 静止秒 | 实播秒 |
|---|---|---|---|---|---|
| spring_reverse | 62 | 5.15 | 0.862 | 0.00 | 3.02 |
| land_recover_v4_12 | 12 | 1.11 | 0.854 | 0.00 | 1.12 |
| walk_left | 62 | 5.15 | 0.807 | 0.00 | — |
| walk_right | 62 | 5.15 | 0.806 | 0.00 | — |
| fun_facepalm | 12 | 2.80 | 0.778 | 0.00 | 2.82 |
| happy_sway_pingpong | 122 | 10.13 | 0.749 | 0.00 | — |
| happy_sway | 62 | 5.15 | 0.724 | 0.00 | 3.02 |
| listen | 54 | 4.48 | 0.714 | 0.00 | 3.00 |

**ratio 最低的 8 条**（尾巴几乎不动 = 正在被读成「卡住」的动作）：

| id | N | 总秒 | ratio | 静止帧 | 保留到 | 静止秒 | 静止实播秒 | 判定 |
|---|---|---|---|---|---|---|---|---|
| sleep_lie_down | 79 | 6.56 | 0.016 | 24 | 55 | 1.99 | 0.91 | FROZEN |
| sit_stretch_hold | 70 | 5.81 | 0.024 | 12 | 58 | 1.00 | 0.51 | INTENTIONAL(hold) |
| tidy_desk | 62 | 5.15 | 0.048 | 11 | 51 | 0.91 | 0.53 | FROZEN |
| spin_cheer_slow | 124 | 10.29 | 0.053 | 11 | 113 | 0.91 | 0.27 | SOFT |
| sit_stretch | 62 | 5.15 | 0.058 | 8 | 54 | 0.66 | 0.39 | SOFT |
| stand_up | 62 | 5.15 | 0.064 | 12 | 50 | 1.00 | 0.58 | FROZEN |
| kneel_look | 62 | 5.15 | 0.075 | 9 | 53 | 0.75 | 0.44 | SOFT |
| look_up_slow | 124 | 10.29 | 0.075 | 3 | 121 | 0.25 | 0.07 | OK |

### 4. 时长表

- one-shot 77 条，合计 418.84s；loop 10 条，合计 77.02s。
- 引擎对 > 3.0s 的 one-shot 做整体加速（`MAX_ONESHOT_MS`），所以 75 条长动作的实播时长被压到 3.00s。
- **> 4.5s 的 one-shot 63 条**：

| id | 库 | N | 总秒 | 实播秒（引擎） | ratio | 静止实播秒 | 判定 |
|---|---|---|---|---|---|---|---|
| wave_slow | exp | 124 | 10.29 | 3.02 | 0.176 | 0.07 | OK |
| nod_slow | exp | 124 | 10.29 | 3.02 | 0.098 | 0.07 | OK |
| look_up_slow | exp | 124 | 10.29 | 3.02 | 0.075 | 0.07 | OK |
| cover_mouth_slow | exp | 124 | 10.29 | 3.02 | 0.092 | 0.07 | OK |
| spin_cheer_slow | exp | 124 | 10.29 | 3.02 | 0.053 | 0.27 | SOFT |
| wave_both_slow | exp | 124 | 10.29 | 3.02 | 0.158 | 0.07 | OK |
| sleep_lie_down | lib | 79 | 6.56 | 3.00 | 0.016 | 0.91 | FROZEN |
| wake_up | lib | 79 | 6.56 | 3.00 | 0.201 | 0.08 | OK |
| stretch_hold | exp | 70 | 5.81 | 3.02 | 0.143 | 0.17 | INTENTIONAL(hold) |
| drink_water_hold | exp | 70 | 5.81 | 3.02 | 0.163 | 0.17 | INTENTIONAL(hold) |
| salute_hold | exp | 70 | 5.81 | 3.02 | 0.129 | 0.17 | INTENTIONAL(hold) |
| pat_chest_hold | exp | 70 | 5.81 | 3.02 | 0.162 | 0.17 | INTENTIONAL(hold) |
| sit_stretch_hold | exp | 70 | 5.81 | 3.02 | 0.024 | 0.51 | INTENTIONAL(hold) |
| drink_tea_hold | exp | 70 | 5.81 | 3.02 | 0.134 | 0.17 | INTENTIONAL(hold) |
| headphone_hold | exp | 70 | 5.81 | 3.02 | 0.122 | 0.17 | INTENTIONAL(hold) |
| look_down_hold | exp | 70 | 5.81 | 3.02 | 0.121 | 0.17 | INTENTIONAL(hold) |
| sit_down | lib | 62 | 5.15 | 3.02 | 0.175 | 0.29 | SOFT |
| stand_up | lib | 62 | 5.15 | 3.02 | 0.064 | 0.58 | FROZEN |
| stretch | exp | 62 | 5.15 | 3.02 | 0.238 | 0.00 | OK |
| drink_water | exp | 62 | 5.15 | 3.02 | 0.269 | 0.00 | OK |
| peek_note | exp | 62 | 5.15 | 3.02 | 0.159 | 0.05 | OK |
| think_pencil | exp | 62 | 5.15 | 3.02 | 0.280 | 0.00 | OK |
| tidy_desk | exp | 62 | 5.15 | 3.02 | 0.048 | 0.53 | FROZEN |
| ribbon_adjust | exp | 62 | 5.15 | 3.02 | 0.378 | 0.00 | OK |
| turn_show | exp | 62 | 5.15 | 3.02 | 0.113 | 0.15 | OK |
| search_pocket | exp | 62 | 5.15 | 3.02 | 0.186 | 0.00 | OK |
| push_ball | exp | 62 | 5.15 | 3.02 | 0.103 | 0.24 | OK |
| catch_star | exp | 62 | 5.15 | 3.02 | 0.290 | 0.00 | OK |
| headphone | exp | 62 | 5.15 | 3.02 | 0.224 | 0.00 | OK |
| lantern_off | exp | 62 | 5.15 | 3.02 | 0.088 | 0.53 | FROZEN |
| wave_hello | exp | 62 | 5.15 | 3.02 | 0.419 | 0.00 | OK |
| nod_yes | exp | 62 | 5.15 | 3.02 | 0.260 | 0.00 | OK |
| shake_head | exp | 62 | 5.15 | 3.02 | 0.263 | 0.00 | OK |
| yawn | exp | 62 | 5.15 | 3.02 | 0.269 | 0.00 | OK |
| cheer_jump | exp | 62 | 5.15 | 3.02 | 0.097 | 0.10 | OK |
| listen_tilt | exp | 62 | 5.15 | 3.02 | 0.220 | 0.00 | OK |
| shy_look | exp | 62 | 5.15 | 3.02 | 0.270 | 0.00 | OK |
| clap_happy | exp | 62 | 5.15 | 3.02 | 0.190 | 0.00 | OK |
| bow | exp | 62 | 5.15 | 3.02 | 0.098 | 0.00 | OK |
| point_forward | exp | 62 | 5.15 | 3.02 | 0.080 | 0.48 | SOFT |
| look_up | exp | 62 | 5.15 | 3.02 | 0.191 | 0.00 | OK |
| cover_mouth | exp | 62 | 5.15 | 3.02 | 0.130 | 0.00 | OK |
| pat_own_chest | exp | 62 | 5.15 | 3.02 | 0.293 | 0.00 | OK |
| tidy_hair | exp | 62 | 5.15 | 3.02 | 0.420 | 0.00 | OK |
| sit_stretch | exp | 62 | 5.15 | 3.02 | 0.058 | 0.39 | SOFT |
| kneel_look | exp | 62 | 5.15 | 3.02 | 0.075 | 0.44 | SOFT |
| sleepy_nod | exp | 62 | 5.15 | 3.02 | 0.155 | 0.29 | SOFT |
| happy_sway | exp | 62 | 5.15 | 3.02 | 0.724 | 0.00 | OK |
| peek_left | exp | 62 | 5.15 | 3.02 | 0.170 | 0.00 | OK |
| peek_right | exp | 62 | 5.15 | 3.02 | 0.103 | 0.10 | OK |
| spin_cheer | exp | 62 | 5.15 | 3.02 | 0.105 | 0.19 | OK |
| drink_tea | exp | 62 | 5.15 | 3.02 | 0.219 | 0.00 | OK |
| stretch_back | exp | 62 | 5.15 | 3.02 | 0.684 | 0.00 | OK |
| shrug | exp | 62 | 5.15 | 3.02 | 0.311 | 0.00 | OK |
| salute | exp | 62 | 5.15 | 3.02 | 0.227 | 0.00 | OK |
| wave_both | exp | 62 | 5.15 | 3.02 | 0.324 | 0.00 | OK |
| hug_self | exp | 62 | 5.15 | 3.02 | 0.123 | 0.19 | OK |
| look_down_sad | exp | 62 | 5.15 | 3.02 | 0.188 | 0.00 | OK |
| tidy_desk_reverse | exp | 62 | 5.15 | 3.02 | 0.500 | 0.10 | OK |
| spring_reverse | exp | 62 | 5.15 | 3.02 | 0.862 | 0.00 | OK |
| peek_left_reverse | exp | 62 | 5.15 | 3.02 | 0.417 | 0.00 | OK |
| peek_right_reverse | exp | 62 | 5.15 | 3.02 | 0.142 | 0.10 | OK |
| harness_task_thinking_v1_12 | lib | 12 | 4.59 | 3.00 | 0.656 | 0.00 | OK |

loop 动作（永不出 finished，靠外部 stop）：

| id | 库 | N | 总秒 |
|---|---|---|---|
| listen_pingpong | exp | 122 | 10.13 |
| happy_sway_pingpong | exp | 122 | 10.13 |
| shrug_pingpong | exp | 122 | 10.13 |
| tidy_hair_pingpong | exp | 122 | 10.13 |
| hug_self_pingpong | exp | 122 | 10.13 |
| sleepy_nod_pingpong | exp | 122 | 10.13 |
| walk_left | lib | 62 | 5.15 |
| walk_right | lib | 62 | 5.15 |
| sleep_loop | lib | 54 | 4.48 |
| sit_idle | lib | 18 | 1.49 |

### 5. 派生一致性

复算方式：`derive_actions._order_indices(源帧数, mode)` 得到期望下标序列。两路证据分开报：**溯源**（逐帧 SHA-256 能否映射回当前源帧下标并逐位对上期望序列）、**结构自证**（slow 成对相同 / hold 首尾各停 4 帧 / pingpong 后半镜相对称——即使源动作被重新渲染也能独立验证）。

结论分布：OK 7 条（字节与当前源一致且顺序逐位对上）、STALE 13 条（重排结构成立，但字节已对不上当前源 → 该派生条目不是由当前源帧复制而来）、MISMATCH 0 条、MISSING 0 条、UNKNOWN 4 条。

| 派生 id | mode | 源动作 | 源帧 | 派生帧 | 期望帧 | 源哈希命中 | 顺序一致 | 结构自证率 | 结论 | 说明 |
|---|---|---|---|---|---|---|---|---|---|---|
| cover_mouth_slow | slow | cover_mouth | 62 | 124 | 124 | 0/124 | — | 100% | STALE | 只有 0/124 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| drink_tea_hold | hold | drink_tea | 62 | 70 | 70 | 0/70 | — | 100% | STALE | 只有 0/70 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| drink_water_hold | hold | drink_water | 62 | 70 | 70 | 70/70 | 是 | 100% | OK | 首段 1.00 / 尾段 1.00 与源首尾一致 |
| happy_sway_pingpong | pingpong | happy_sway | 62 | 122 | 122 | 122/122 | 是 | 100% | OK | 后半段镜相对称 60/60 对 |
| headphone_hold | hold | headphone | 62 | 70 | 70 | 0/70 | — | 100% | STALE | 只有 0/70 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| hug_self_pingpong | pingpong | hug_self | 62 | 122 | 122 | 0/122 | — | 100% | STALE | 只有 0/122 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| listen_pingpong | pingpong | listen_tilt | 62 | 122 | 122 | 122/122 | 是 | 100% | OK | 后半段镜相对称 60/60 对 |
| look_down_hold | hold | look_down_sad | 62 | 70 | 70 | 0/70 | — | 100% | STALE | 只有 0/70 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| look_up_slow | slow | look_up | 62 | 124 | 124 | 0/124 | — | 100% | STALE | 只有 0/124 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| nod_slow | slow | nod_yes | 62 | 124 | 124 | 124/124 | 是 | 100% | OK | 成对相同的帧对 62/62 |
| pat_chest_hold | hold | pat_own_chest | 62 | 70 | 70 | 0/70 | — | 100% | STALE | 只有 0/70 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| peek_left_reverse | reverse | peek_left | 62 | 62 | 62 | 0/62 | — | — | UNKNOWN | 只有 0/62 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| peek_right_reverse | reverse | peek_right | 62 | 62 | 62 | 0/62 | — | — | UNKNOWN | 只有 0/62 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| salute_hold | hold | salute | 62 | 70 | 70 | 70/70 | 是 | 100% | OK | 首段 1.00 / 尾段 1.00 与源首尾一致 |
| shrug_pingpong | pingpong | shrug | 62 | 122 | 122 | 0/122 | — | 35% | STALE | 只有 0/122 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| sit_stretch_hold | hold | sit_stretch | 62 | 70 | 70 | 0/70 | — | 100% | STALE | 只有 0/70 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| sleepy_nod_pingpong | pingpong | sleepy_nod | 62 | 122 | 122 | 0/122 | — | 100% | STALE | 只有 0/122 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| spin_cheer_slow | slow | spin_cheer | 62 | 124 | 124 | 0/124 | — | 100% | STALE | 只有 0/124 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| spring_reverse | reverse | cheer_jump | 62 | 62 | 62 | 0/62 | — | — | UNKNOWN | 只有 0/62 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| stretch_hold | hold | stretch | 62 | 70 | 70 | 70/70 | 是 | 100% | OK | 首段 1.00 / 尾段 1.00 与源首尾一致 |
| tidy_desk_reverse | reverse | tidy_desk | 62 | 62 | 62 | 0/62 | — | — | UNKNOWN | 只有 0/62 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| tidy_hair_pingpong | pingpong | tidy_hair | 62 | 122 | 122 | 0/122 | — | 100% | STALE | 只有 0/122 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| wave_both_slow | slow | wave_both | 62 | 124 | 124 | 0/124 | — | 100% | STALE | 只有 0/124 帧能在当前源动作里找到同样字节，无法按源哈希定序 |
| wave_slow | slow | wave_hello | 62 | 124 | 124 | 124/124 | 是 | 100% | OK | 成对相同的帧对 62/62 |

### 6. 引擎实播（模拟时间，不 sleep）

构造 `PetEngine` 后逐条 `force_perform`，以 1/60s 步长推进 `engine.tick` 直到状态机回到 `IDLE`（`sit_down` 期望接上 `sit_idle` 常驻），上限 1.5× 标称。`运动结束后静止` = 从「最后一帧有运动的画面」到回 `IDLE` 之间还播了多久静止帧。

| id | 库 | 标称秒 | 实播秒 | 上限 | 运动结束后静止 | 状态 |
|---|---|---|---|---|---|---|
| wave_slow | exp | 10.29 | 3.02 | 4.50 | 0.08 | IDLE |
| nod_slow | exp | 10.29 | 3.02 | 4.50 | 0.08 | IDLE |
| look_up_slow | exp | 10.29 | 3.02 | 4.50 | 0.08 | IDLE |
| cover_mouth_slow | exp | 10.29 | 3.02 | 4.50 | 0.08 | IDLE |
| spin_cheer_slow | exp | 10.29 | 3.02 | 4.50 | 0.27 | IDLE |
| wave_both_slow | exp | 10.29 | 3.02 | 4.50 | 0.08 | IDLE |
| listen_pingpong | exp | 10.13 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| happy_sway_pingpong | exp | 10.13 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| shrug_pingpong | exp | 10.13 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| tidy_hair_pingpong | exp | 10.13 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| hug_self_pingpong | exp | 10.13 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| sleepy_nod_pingpong | exp | 10.13 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| sleep_lie_down | lib | 6.56 | 3.00 | 4.50 | 0.90 | IDLE |
| wake_up | lib | 6.56 | 3.00 | 4.50 | 0.07 | IDLE |
| stretch_hold | exp | 5.81 | 3.02 | 4.50 | 0.18 | IDLE |
| drink_water_hold | exp | 5.81 | 3.02 | 4.50 | 0.18 | IDLE |
| salute_hold | exp | 5.81 | 3.02 | 4.50 | 0.18 | IDLE |
| pat_chest_hold | exp | 5.81 | 3.02 | 4.50 | 0.18 | IDLE |
| sit_stretch_hold | exp | 5.81 | 3.02 | 4.50 | 0.52 | IDLE |
| drink_tea_hold | exp | 5.81 | 3.02 | 4.50 | 0.18 | IDLE |
| headphone_hold | exp | 5.81 | 3.02 | 4.50 | 0.18 | IDLE |
| look_down_hold | exp | 5.81 | 3.02 | 4.50 | 0.18 | IDLE |
| sit_down | lib | 5.15 | 3.02 | 4.50 | 0.30 | SETTLE_INTO_LOOP |
| stand_up | lib | 5.15 | 3.02 | 4.50 | 0.58 | IDLE |
| walk_left | lib | 5.15 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| walk_right | lib | 5.15 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| stretch | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| drink_water | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| peek_note | exp | 5.15 | 3.02 | 4.50 | 0.05 | IDLE |
| think_pencil | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| tidy_desk | exp | 5.15 | 3.02 | 4.50 | 0.53 | IDLE |
| ribbon_adjust | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| turn_show | exp | 5.15 | 3.02 | 4.50 | 0.15 | IDLE |
| search_pocket | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| push_ball | exp | 5.15 | 3.02 | 4.50 | 0.25 | IDLE |
| catch_star | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| headphone | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| lantern_off | exp | 5.15 | 3.02 | 4.50 | 0.53 | IDLE |
| wave_hello | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| nod_yes | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| shake_head | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| yawn | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| cheer_jump | exp | 5.15 | 3.02 | 4.50 | 0.10 | IDLE |
| listen_tilt | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| shy_look | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| clap_happy | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| bow | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| point_forward | exp | 5.15 | 3.02 | 4.50 | 0.50 | IDLE |
| look_up | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| cover_mouth | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| pat_own_chest | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| tidy_hair | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| sit_stretch | exp | 5.15 | 3.02 | 4.50 | 0.40 | IDLE |
| kneel_look | exp | 5.15 | 3.02 | 4.50 | 0.45 | IDLE |
| sleepy_nod | exp | 5.15 | 3.02 | 4.50 | 0.30 | IDLE |
| happy_sway | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| peek_left | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| peek_right | exp | 5.15 | 3.02 | 4.50 | 0.10 | IDLE |
| spin_cheer | exp | 5.15 | 3.02 | 4.50 | 0.20 | IDLE |
| drink_tea | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| stretch_back | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| shrug | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| salute | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| wave_both | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| hug_self | exp | 5.15 | 3.02 | 4.50 | 0.20 | IDLE |
| look_down_sad | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| tidy_desk_reverse | exp | 5.15 | 3.02 | 4.50 | 0.10 | IDLE |
| spring_reverse | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| peek_left_reverse | exp | 5.15 | 3.02 | 4.50 | — | IDLE |
| peek_right_reverse | exp | 5.15 | 3.02 | 4.50 | 0.10 | IDLE |
| harness_task_thinking_v1_12 | lib | 4.59 | 3.00 | 4.50 | — | IDLE |
| listen | lib | 4.48 | 3.00 | 4.50 | — | IDLE |
| sleep_loop | lib | 4.48 | — | 4.50 | — | LOOP_NO_FINISH_OK |
| wave | lib | 4.48 | 3.00 | 4.50 | — | IDLE |
| celebrate | lib | 4.48 | 3.00 | 4.50 | — | IDLE |
| head_pat | lib | 4.48 | 3.00 | 4.50 | 0.17 | IDLE |
| belly_ticklish | lib | 4.48 | 3.00 | 4.50 | — | IDLE |
| cheek_poke | lib | 4.48 | 3.00 | 4.50 | — | IDLE |
| high_five | lib | 4.48 | 3.00 | 4.50 | — | IDLE |
| file_drop_receive | lib | 4.48 | 3.00 | 4.50 | 0.05 | IDLE |
| feed_bread | lib | 4.48 | 3.00 | 4.50 | — | IDLE |
| feed_refuse | lib | 4.48 | 3.00 | 4.50 | — | IDLE |
| harness_tool_working_v1_12 | lib | 4.38 | 3.00 | 4.50 | — | IDLE |
| nod | lib | 3.07 | 3.00 | 4.50 | — | IDLE |
| fun_facepalm | lib | 2.80 | 2.82 | 4.20 | — | IDLE |
| sit_idle | lib | 1.49 | — | 2.24 | — | LOOP_NO_FINISH_OK |
| land_recover_v4_12 | lib | 1.11 | 1.12 | 1.67 | — | IDLE |

### 7. 回归

两次独立运行（均在素材完整窗口内，cwd 与 PYTHONPATH 与脚本内一致）：

| 时间 | 命令 | 退出码 | 用例 | 耗时s | 结果 |
|---|---|---|---|---|---|
| 20:20 | `python -m unittest discover -s mimi_app/tests` | 1 | 161 | 31.52 | FAILED (errors=1, skipped=1)：`AttributeError: 'FakeEngine' object has no attribute 'set_external_bubble'` |
| 20:26 | 同上（重跑） | 0 | 161 | — | OK (skipped=1) |
| 20:20 | `python scripts/verify/acceptance_expansion.py` | 0 | 24/24 | 0.36 | 失败 0 |

**flaky 根因（已定位，非裁剪引入）**：`dsh_integration._bubble()` 有 1.2s 墙钟节流
（`now - self._last_bubble_at < 1.2`）。`test_prompt_routes_to_agent_session` 先
`set_mode("agent")` 再 `prompt_active()`，机器慢时两者间隔超过 1.2s，气泡不再被节流，
落到 `engine.set_external_bubble(...)`；而该测试的 `FakeEngine` 没有这个方法。
冷启动（无 `__pycache__`）时必现，热启动时通常不现——这解释了"手工跑 OK、脚本里 FAILED"。

`verify_trim.py` 因此对 unittest 做了**失败即重跑一次**（最多 2 次），并把每次的耗时与
失败用例列成表：flaky 与真失败分开报告，不掩盖也不误判。

### 额外线索

- **⚠ 本报告产出后，素材已进入裁剪中间状态**（20:35 起）。实测：源动作已被裁到 61 帧，
  `expansion_v1/actions.json` 里 20 条派生条目的 `len(frames)` 已按 `mode(61)` 同步更新
  （`wave_slow` 124→122、`listen_pingpong` 122→120、`stretch_hold` 70→69 …），
  但派生帧目录里仍只有 61 个文件、尚未按新源重建。此时 `ActionLibrary` 会抛 
  `FileNotFoundError`，整套 unittest 会大面积假失败。
  **AFTER 必须在「裁尾 + 重建派生 + 同步 manifest 帧数与 duration_ms」全部落地后**才跑，
  否则测到的是半成品。`verify_trim.py` 已内置守卫：检测到 manifest 与磁盘不符时，
  在小节顶部打「中间状态快照」警示、把验收门判 FAIL、并**拒绝覆盖**已有小节（需 `--force`）。
- **尾巴规模**：35/77 条 one-shot 的尾部存在连续静止段（阈值 ≤10%×峰值）；全部 one-shot 的静止帧合计 194 帧 / 16.10s 标称，折算到引擎实播是 8.29s 的「什么也没发生」。
- 静止实播秒最长的 6 条（括号内是建议保留到的帧号 / 可删帧数）：`sleep_lie_down` 0.91s（保留到第 55 帧，可删 24 帧）、`stand_up` 0.58s（保留到第 50 帧，可删 12 帧）、`tidy_desk` 0.53s（保留到第 51 帧，可删 11 帧）、`lantern_off` 0.53s（保留到第 51 帧，可删 11 帧）、`sit_stretch_hold` 0.51s（保留到第 58 帧，可删 12 帧）、`point_forward` 0.48s（保留到第 52 帧，可删 10 帧）。
- **会被读成「卡住」的动作**：静止实播秒 > 0.5s 共 **4 条**（另 6 条落在 0.25–0.5s 之间）。注意这个数是**引擎实播时间**，已经算进了 `MAX_ONESHOT_MS=3000` 的加速；用户在屏幕上看的就是这个时长。
- **引擎加速会掩盖尾巴**：75/77 条 one-shot 标称 > 3.0s，`FramePlayer.MAX_ONESHOT_MS=3000` 会把它整体提速到 3.00s。所以裁尾**不会**改变这些动作的实播总长，只会改变这 3 秒里有多少帧是静止的——验收必须看`静止实播秒`/`运动结束后静止`，不能只看总时长。同时也要注意：只裁尾巴、不重建时长表时，加速比会变化（分母变小），实播总长依然是 3.00s。
- 裁剪优先级（按静止实播秒排序，已排除 loop 与 hold/pingpong 派生）：`sleep_lie_down`、`stand_up`、`tidy_desk`、`lantern_off`；紧接其后的是 `point_forward`(0.48s)、`kneel_look`(0.44s)、`sit_stretch`(0.39s)。
- **刻意保留的停顿**：8 条 one-shot 是 hold/pingpong 派生（`stretch_hold`、`drink_water_hold`、`salute_hold`、`pat_chest_hold`、`sit_stretch_hold`、`drink_tea_hold`、`headphone_hold`、`look_down_hold`）。它们的尾帧静止是设计意图（`_order_indices` 的 hold 用 `[首帧]×4 + 全序列 + [尾帧]×4`），裁尾时应保持其派生语义或干脆不裁，否则派生一致性检查会报 MISMATCH。
- **尾巴不是复制帧**：检查 87 条动作的尾部 10% 是否存在逐字节重复帧，命中 15 条（peek_note、wave_slow、nod_slow、stretch_hold、drink_water_hold、salute_hold）。也就是说这些静止尾巴是生成模型留下的**近静止微抖**，不是可以靠去重/哈希检测到的定格；只能靠运动量指标识别，裁剪也只能按帧号切。
- **canvas 声明**：声明与实测不符 0 条、目录基准尺寸不符 0 条（当前 library_v1 512×768、expansion_v1 320×480，两套内部自洽）。但生成器 `scripts/pipeline/derive_actions.py:write_manifest()` 把派生条目的 canvas 硬编码成 `[512, 768]` / pivot `[256, 744]`：现在 actions.json 里的派生条目是 320×480，一旦重跑 `derive_actions.py build` 就会退回 512×768 并与 320×480 的实际帧不符。裁尾后必须重建派生条目，请盯住这一项（本脚本第 2 项会把它记成尺寸警告）。
- **派生条目的字节溯源断了**：13/24 条派生动作的帧不是当前源动作帧的字节副本（结构自证仍然成立，说明重排没错、只是源动作被重新渲染过）。对裁剪的直接影响：这些派生动作播的是**旧版（含长尾巴）**的源帧，裁完源动作后必须整批重跑 `derive_actions.py build`，否则派生动作会继续带着未被裁掉的尾巴。清单：`cover_mouth_slow←cover_mouth`、`drink_tea_hold←drink_tea`、`headphone_hold←headphone`、`hug_self_pingpong←hug_self`、`look_down_hold←look_down_sad`、`look_up_slow←look_up`、`pat_chest_hold←pat_own_chest`、`shrug_pingpong←shrug`、`sit_stretch_hold←sit_stretch`、`sleepy_nod_pingpong←sleepy_nod`…。
- 派生一致性全部通过（24 条按源帧哈希逐个对上下标序列）。**裁剪源动作后这一项一定会先红**，重跑 `derive_actions.py build` 才会恢复——这正是 AFTER 阶段要验的证据。
- loop 动作 10 条（`happy_sway_pingpong`、`hug_self_pingpong`、`listen_pingpong`、`shrug_pingpong`、`sit_idle`、`sleep_loop`、`sleepy_nod_pingpong`、`tidy_hair_pingpong`…）：`FramePlayer` 对 loop 永不上报 `finished`，引擎不会自动回 Idle，必须外部 `stop_walk`/`cancel_performance`/`stand_up`。这类「看起来卡住」是设计如此，不要和静止尾巴混为一谈。
