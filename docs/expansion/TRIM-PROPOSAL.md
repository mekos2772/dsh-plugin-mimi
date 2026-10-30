# 动作尾巴裁剪提案（tail_motion v2 — 播完一轮就切）

> 桌宠「播完后卡住回不去」的根因在素材：视频模型生成的帧序列，尾巴上挂着一段几乎静止的画面——动作早就做完了，播放器还在按 duration 一帧帧耗时间。本文给出**每个动作实测的静止尾巴有多长**，以及建议保留的帧数。只测量，不动素材。
>
> 全量实测：**87 个动作 / 4934 个帧槽位**（library_v1 23 条 + expansion_v1 64 条），一条不落。原始总时长 416.7s，按本方案砍掉 **11.1s**（97% 的动作时长被去掉）。

## 0. 这一版和上一版的区别

Lead 定的方向是**「播完一轮就切」**，不追求自然收尾。所以 v2 比 v1 激进得多：

> **执行状态**：v2 口径（0.20）已经在素材上落盘两轮。第一轮用错了系数（0.03，只砍 136 帧），第二轮按 0.20 落盘 59 条决策、砍 460 帧。本文所有数字都在这两轮落盘**之后**的素材上测得，`pose_ratio` 是运行时真实终态。

| | v1（旧） | **v2（当前）** |
|---|---|---|
| 静止判据 | `S > 2.0 × F_tail` | **`S > max(0.20 × P, 0.008)`** |
| 收尾缓冲 | +2 帧 | **+0 帧缓冲（只留 `last_motion + 1`）** |
| keep 下限 | `max(16, ceil(0.5N))` | **`max(6, ceil(0.35N))`** |
| loop / hold / pingpong | 一视同仁 | **`loop=True` 不裁；其余一律裁** |

v1 被换掉的原因是个真实缺陷：它拿 `2 × F_tail` 当静止判据，而 `F_tail` 量到的
**正是尾巴上残留的低幅运动本身**（呼吸、发丝、裙摆），判据因此退化成「尾巴跟自己比」——
在 5 条动作上把「开头十帧之后」就判成了静止。v2 改用「相对峰值 3% + 绝对 0.008」双下限，
不再拿尾巴自比。v1 的数字仍保留在 JSON 和 §5 对照表里，方便回看。

## 1. 结果总览

| 裁剪比例 | 动作数 |
|---|---|
| **> 30%** | **1** |
| 10–30% | 8 |
| < 10% | 78（其中 10 条不动刀） |

- 可裁的 one-shot：**77** 条；`loop=True` 不裁：**10** 条（sit_idle, sleep_loop, walk_left, walk_right, listen_pingpong, happy_sway_pingpong, shrug_pingpong, tidy_hair_pingpong, hug_self_pingpong, sleepy_nod_pingpong）。
- 按本表口径还能再砍 **11.1s / 416.7s**（即 §2.2 的残留尾巴，要不要做第三轮由 Lead 定）。
- 需要交叉淡入（`ends_off_pose`）：**41 / 87** 条，清单见 §2。

### 1.1 砍得最狠的 15 个（按省下的秒数）

```
 #  id                          library          N  keep     原s   裁后s     省s     裁%  ends_off_pose
-------------------------------------------------------------------------------------------------------
 1  peek_right_reverse          expansion_v1    39    29    3.24    2.41    0.83   25.6%          False
 2  cheer_jump                  expansion_v1    22    14    1.83    1.16    0.66   36.4%          False
 3  sit_down                    library_v1      60    54    4.98    4.48    0.50   10.0%           True
 4  wave                        library_v1      52    47    4.32    3.90    0.41    9.6%          False
 5  harness_task_thinking_v1_12  library_v1      10     9    3.84    3.48    0.36   10.0%           True
 6  tidy_desk_reverse           expansion_v1    30    26    2.49    2.16    0.33   13.3%          False
 7  peek_left_reverse           expansion_v1    45    41    3.73    3.40    0.33    8.9%          False
 8  wake_up                     library_v1      77    73    6.39    6.06    0.33    5.2%          False
 9  look_up_slow                expansion_v1   102    98    8.47    8.13    0.33    3.9%           True
10  nod_slow                    expansion_v1   120   116    9.96    9.63    0.33    3.3%          False
11  harness_tool_working_v1_12  library_v1      10     9    3.57    3.27    0.30   10.0%           True
12  fun_facepalm                library_v1      10     9    2.24    1.98    0.26   10.0%           True
13  point_forward               expansion_v1    22    19    1.83    1.58    0.25   13.6%           True
14  head_pat                    library_v1      47    45    3.90    3.73    0.17    4.3%          False
15  feed_bread                  library_v1      47    45    3.90    3.73    0.17    4.3%          False
```

### 1.2 几乎没有尾巴（< 10%）

```
id                          library          N  keep     裁%     省s  ends_off_pose
-----------------------------------------------------------------------------------
sit_idle                    library_v1      18    18    0.0%    0.00           True
sleep_loop                  library_v1      54    54    0.0%    0.00           True
walk_left                   library_v1      62    62    0.0%    0.00           True
walk_right                  library_v1      62    62    0.0%    0.00           True
listen_pingpong             expansion_v1   118   118    0.0%    0.00          False
happy_sway_pingpong         expansion_v1   118   118    0.0%    0.00          False
shrug_pingpong              expansion_v1   118   118    0.0%    0.00          False
tidy_hair_pingpong          expansion_v1   118   118    0.0%    0.00          False
hug_self_pingpong           expansion_v1   102   102    0.0%    0.00          False
sleepy_nod_pingpong         expansion_v1   108   108    0.0%    0.00           True
stretch_hold                expansion_v1    62    61    1.6%    0.08          False
drink_water_hold            expansion_v1    62    61    1.6%    0.08          False
pat_chest_hold              expansion_v1    62    61    1.6%    0.08           True
salute_hold                 expansion_v1    61    60    1.6%    0.08           True
stretch                     expansion_v1    60    59    1.7%    0.08          False
drink_water                 expansion_v1    60    59    1.7%    0.08          False
think_pencil                expansion_v1    60    59    1.7%    0.08           True
ribbon_adjust               expansion_v1    60    59    1.7%    0.08          False
catch_star                  expansion_v1    60    59    1.7%    0.08           True
wave_hello                  expansion_v1    60    59    1.7%    0.08          False
nod_yes                     expansion_v1    60    59    1.7%    0.08          False
shake_head                  expansion_v1    60    59    1.7%    0.08           True
yawn                        expansion_v1    60    59    1.7%    0.08           True
listen_tilt                 expansion_v1    60    59    1.7%    0.08           True
shy_look                    expansion_v1    60    59    1.7%    0.08           True
pat_own_chest               expansion_v1    60    59    1.7%    0.08           True
tidy_hair                   expansion_v1    60    59    1.7%    0.08          False
happy_sway                  expansion_v1    60    59    1.7%    0.08          False
stretch_back                expansion_v1    60    59    1.7%    0.08           True
shrug                       expansion_v1    60    59    1.7%    0.08           True
wave_both                   expansion_v1    60    59    1.7%    0.08           True
wave_slow                   expansion_v1   120   118    1.7%    0.17          False
wave_both_slow              expansion_v1   120   118    1.7%    0.17           True
salute                      expansion_v1    59    58    1.7%    0.08           True
look_down_hold              expansion_v1    59    58    1.7%    0.08           True
headphone_hold              expansion_v1    58    57    1.7%    0.08           True
look_down_sad               expansion_v1    57    56    1.8%    0.08           True
drink_tea_hold              expansion_v1    57    56    1.8%    0.08          False
headphone                   expansion_v1    56    55    1.8%    0.08           True
clap_happy                  expansion_v1    56    55    1.8%    0.08          False
sleepy_nod                  expansion_v1    55    54    1.8%    0.08           True
drink_tea                   expansion_v1    55    54    1.8%    0.08          False
listen                      library_v1      52    51    1.9%    0.08          False
celebrate                   library_v1      52    51    1.9%    0.08          False
belly_ticklish              library_v1      52    51    1.9%    0.08          False
cheek_poke                  library_v1      52    51    1.9%    0.08           True
file_drop_receive           library_v1      52    51    1.9%    0.08          False
feed_refuse                 library_v1      52    51    1.9%    0.08          False
hug_self                    expansion_v1    52    51    1.9%    0.08          False
high_five                   library_v1      51    50    2.0%    0.08          False
search_pocket               expansion_v1    51    50    2.0%    0.08           True
look_up                     expansion_v1    51    50    2.0%    0.08           True
sleep_lie_down              library_v1      50    49    2.0%    0.08           True
kneel_look                  expansion_v1    50    49    2.0%    0.08           True
peek_left                   expansion_v1    45    44    2.2%    0.08          False
stand_up                    library_v1      44    43    2.3%    0.08          False
turn_show                   expansion_v1    43    42    2.3%    0.08           True
lantern_off                 expansion_v1    42    41    2.4%    0.08           True
bow                         expansion_v1    42    41    2.4%    0.08          False
peek_note                   expansion_v1    41    40    2.4%    0.08           True
peek_right                  expansion_v1    39    38    2.6%    0.08          False
cover_mouth                 expansion_v1    38    37    2.6%    0.08          False
cover_mouth_slow            expansion_v1    76    74    2.6%    0.17          False
push_ball                   expansion_v1    37    36    2.7%    0.08          False
spin_cheer                  expansion_v1    37    36    2.7%    0.08          False
sit_stretch_hold            expansion_v1    37    36    2.7%    0.08           True
spin_cheer_slow             expansion_v1    74    72    2.7%    0.17          False
nod                         library_v1      35    34    2.9%    0.08          False
sit_stretch                 expansion_v1    35    34    2.9%    0.08           True
tidy_desk                   expansion_v1    30    29    3.3%    0.08          False
nod_slow                    expansion_v1   120   116    3.3%    0.33          False
look_up_slow                expansion_v1   102    98    3.9%    0.33           True
head_pat                    library_v1      47    45    4.3%    0.17          False
feed_bread                  library_v1      47    45    4.3%    0.17          False
spring_reverse              expansion_v1    22    21    4.5%    0.08          False
wake_up                     library_v1      77    73    5.2%    0.33          False
peek_left_reverse           expansion_v1    45    41    8.9%    0.33          False
wave                        library_v1      52    47    9.6%    0.41          False
```

## 2. ends_off_pose：裁完停不到站姿的动作

运行时要把动作接回 idle，如果裁完最后一帧不是站姿，衔接会跳一下。这一列就是给**交叉淡入**用的：淡入时长和优先级按 `pose_ratio` 分档。

锚点是**站姿参考帧** `wave/frames/0001.webp`（manifest 的 `live_rig.identity_source`），不是动作自己的第一帧——有些动作起手就已经不是站姿（低头、跪着、侧身），拿首帧当基准会把「本来就歪」算成「收得正好」。

**本表按落盘后的终态算**：`d_last = d[N-1]`，N 是素材现在的真实帧数。也就是说 `pose_ratio` 就是运行时播完这一条、准备接 idle 时看到的那个姿势。§2.2 另外给了「如果再把残留尾巴裁掉一刀」的值，供你判断要不要第三轮。

```
d[i]   = mean(|desc[i] − desc_ref|)      逐帧相对站姿的姿势偏移
d_peak = max(d)                          该动作离站姿最远的时刻
d_last = d[N−1]                          素材现在的最后一帧（运行时真实终点）
pose_ratio = d_last / d_peak             收得多接近站姿（0 = 完全站姿）
ends_off_pose = pose_ratio > 0.6
```

`d_last` 用 `frames[N-1]`：素材已经落盘修剪过，N 就是现在的真实帧数，所以这一帧就是运行时播完、准备接 idle 时看到的画面。（若还要第三轮，再按 `frames[keep-1]` 取值，见 §2.2 的 `若再裁 pose_ratio` 列。）

共 **41 / 87** 条命中（one-shot 36 条 + loop 5 条）。按 `pose_ratio` 从高到低排，越靠前越需要用长一点的淡入。

```
id                          library          N   d_last   d_peak  pose_ratio    档  loop
----------------------------------------------------------------------------------------
sleep_lie_down              library_v1      50   20.945   20.945       1.000    T1     -
turn_show                   expansion_v1    43   14.673   14.673       1.000    T1     -
listen_tilt                 expansion_v1    60    7.476    7.504       0.996    T1     -
yawn                        expansion_v1    60    5.419    5.465       0.992    T1     -
sleep_loop                  library_v1      54   20.680   20.907       0.989    T1     Y
walk_right                  library_v1      62   11.762   11.936       0.985    T1     Y
walk_left                   library_v1      62   10.896   11.143       0.978    T1     Y
shy_look                    expansion_v1    60    5.230    5.397       0.969    T1     -
think_pencil                expansion_v1    60    9.125    9.487       0.962    T1     -
harness_tool_working_v1_12  library_v1      10   18.187   19.155       0.950    T2     -
search_pocket               expansion_v1    51    9.416    9.934       0.948    T2     -
wave_both                   expansion_v1    60   13.425   14.483       0.927    T2     -
wave_both_slow              expansion_v1   120   13.425   14.483       0.927    T2     -
look_down_sad               expansion_v1    57    5.891    6.359       0.926    T2     -
look_down_hold              expansion_v1    59    5.891    6.359       0.926    T2     -
sit_idle                    library_v1      18    9.535   10.295       0.926    T2     Y
shake_head                  expansion_v1    60    3.987    4.351       0.916    T2     -
kneel_look                  expansion_v1    50   11.162   12.300       0.907    T2     -
sit_down                    library_v1      60   10.561   11.652       0.906    T2     -
harness_task_thinking_v1_12  library_v1      10   18.669   21.027       0.888    T2     -
sleepy_nod_pingpong         expansion_v1   108    9.778   11.119       0.879    T2     Y
shrug                       expansion_v1    60    4.536    5.163       0.878    T2     -
point_forward               expansion_v1    22    7.048    8.281       0.851    T2     -
salute                      expansion_v1    59    5.526    6.498       0.850    T2     -
salute_hold                 expansion_v1    61    5.526    6.498       0.850    T2     -
sleepy_nod                  expansion_v1    55    9.185   11.119       0.826    T2     -
peek_note                   expansion_v1    41    6.112    7.542       0.810    T2     -
catch_star                  expansion_v1    60    3.614    4.626       0.781    T3     -
cheek_poke                  library_v1      52    2.606    3.400       0.766    T3     -
land_recover_v4_12          library_v1      10   14.870   20.198       0.736    T3     -
fun_facepalm                library_v1      10    3.772    5.131       0.735    T3     -
headphone                   expansion_v1    56    5.289    7.458       0.709    T3     -
headphone_hold              expansion_v1    58    5.289    7.458       0.709    T3     -
pat_own_chest               expansion_v1    60    2.902    4.237       0.685    T3     -
pat_chest_hold              expansion_v1    62    2.902    4.237       0.685    T3     -
sit_stretch                 expansion_v1    35   10.493   15.424       0.680    T3     -
sit_stretch_hold            expansion_v1    37   10.493   15.424       0.680    T3     -
lantern_off                 expansion_v1    42    6.839   10.286       0.665    T3     -
look_up                     expansion_v1    51    3.879    5.871       0.661    T3     -
look_up_slow                expansion_v1   102    3.879    5.871       0.661    T3     -
stretch_back                expansion_v1    60    5.903    9.815       0.601    T3     -
```

### 2.1 交叉淡入分档建议

| 档 | pose_ratio | 含义 | 建议淡入 |
|---|---|---|---|
| **T1** | ≥ 0.95 | 几乎原样停在动作里（躺着、跪着、走着） | 最长，约 200–260ms |
| **T2** | 0.80–0.95 | 明显非站姿 | 长，约 140–200ms |
| **T3** | 0.60–0.80 | 轻微偏离站姿 | 中，约 80–140ms |
| **–** | < 0.60 | 已接近站姿 | 可硬切，或 ≤60ms |

本批分布：- 46 条，T1 9 条，T2 18 条，T3 14 条。

### 2.2 残留尾巴（如果还要第三轮）

第二轮落盘后，仍有 **77 条**可按同一口径再裁，合计 **125 帧 / 11.1s**。注意其中 `*_hold` 的 5 帧静止尾巴是**构造语义**（「尾帧停住」），不算漂移，要不要裁是产品取舍，不是测量问题。

```
id                          library          N  keep   裁     裁%     省s   若再裁 pose_ratio
---------------------------------------------------------------------------------------------
peek_right_reverse          expansion_v1    39    29   10   25.6%    0.83               0.341
cheer_jump                  expansion_v1    22    14    8   36.4%    0.66               0.268
sit_down                    library_v1      60    54    6   10.0%    0.50               0.906
wave                        library_v1      52    47    5    9.6%    0.41               0.270
harness_task_thinking_v1_12  library_v1      10     9    1   10.0%    0.36               0.896
wake_up                     library_v1      77    73    4    5.2%    0.33               0.523
tidy_desk_reverse           expansion_v1    30    26    4   13.3%    0.33               0.422
nod_slow                    expansion_v1   120   116    4    3.3%    0.33               0.598
look_up_slow                expansion_v1   102    98    4    3.9%    0.33               0.669
peek_left_reverse           expansion_v1    45    41    4    8.9%    0.33               0.205
harness_tool_working_v1_12  library_v1      10     9    1   10.0%    0.30               0.914
fun_facepalm                library_v1      10     9    1   10.0%    0.26               0.854
point_forward               expansion_v1    22    19    3   13.6%    0.25               0.908
head_pat                    library_v1      47    45    2    4.3%    0.17               0.389
feed_bread                  library_v1      47    45    2    4.3%    0.17               0.396
wave_slow                   expansion_v1   120   118    2    1.7%    0.17               0.607
cover_mouth_slow            expansion_v1    76    74    2    2.6%    0.17               0.407
spin_cheer_slow             expansion_v1    74    72    2    2.7%    0.17               0.230
wave_both_slow              expansion_v1   120   118    2    1.7%    0.17               0.920
land_recover_v4_12          library_v1      10     9    1   10.0%    0.09               0.643
listen                      library_v1      52    51    1    1.9%    0.08               0.416
stand_up                    library_v1      44    43    1    2.3%    0.08               0.163
sleep_lie_down              library_v1      50    49    1    2.0%    0.08               0.976
celebrate                   library_v1      52    51    1    1.9%    0.08               0.671
belly_ticklish              library_v1      52    51    1    1.9%    0.08               0.202
cheek_poke                  library_v1      52    51    1    1.9%    0.08               0.840
high_five                   library_v1      51    50    1    2.0%    0.08               0.554
file_drop_receive           library_v1      52    51    1    1.9%    0.08               0.251
feed_refuse                 library_v1      52    51    1    1.9%    0.08               0.652
nod                         library_v1      35    34    1    2.9%    0.08               0.450
stretch                     expansion_v1    60    59    1    1.7%    0.08               0.389
drink_water                 expansion_v1    60    59    1    1.7%    0.08               0.444
peek_note                   expansion_v1    41    40    1    2.4%    0.08               0.805
think_pencil                expansion_v1    60    59    1    1.7%    0.08               0.959
tidy_desk                   expansion_v1    30    29    1    3.3%    0.08               0.432
ribbon_adjust               expansion_v1    60    59    1    1.7%    0.08               0.505
turn_show                   expansion_v1    43    42    1    2.3%    0.08               0.673
search_pocket               expansion_v1    51    50    1    2.0%    0.08               0.936
push_ball                   expansion_v1    37    36    1    2.7%    0.08               0.399
catch_star                  expansion_v1    60    59    1    1.7%    0.08               0.820
headphone                   expansion_v1    56    55    1    1.8%    0.08               0.708
lantern_off                 expansion_v1    42    41    1    2.4%    0.08               0.687
wave_hello                  expansion_v1    60    59    1    1.7%    0.08               0.607
nod_yes                     expansion_v1    60    59    1    1.7%    0.08               0.583
shake_head                  expansion_v1    60    59    1    1.7%    0.08               0.949
yawn                        expansion_v1    60    59    1    1.7%    0.08               0.989
listen_tilt                 expansion_v1    60    59    1    1.7%    0.08               0.994
shy_look                    expansion_v1    60    59    1    1.7%    0.08               0.964
clap_happy                  expansion_v1    56    55    1    1.8%    0.08               0.577
bow                         expansion_v1    42    41    1    2.4%    0.08               0.340
look_up                     expansion_v1    51    50    1    2.0%    0.08               0.661
cover_mouth                 expansion_v1    38    37    1    2.6%    0.08               0.407
pat_own_chest               expansion_v1    60    59    1    1.7%    0.08               0.722
tidy_hair                   expansion_v1    60    59    1    1.7%    0.08               0.495
sit_stretch                 expansion_v1    35    34    1    2.9%    0.08               0.676
kneel_look                  expansion_v1    50    49    1    2.0%    0.08               0.852
sleepy_nod                  expansion_v1    55    54    1    1.8%    0.08               0.896
happy_sway                  expansion_v1    60    59    1    1.7%    0.08               0.416
peek_left                   expansion_v1    45    44    1    2.2%    0.08               0.223
peek_right                  expansion_v1    39    38    1    2.6%    0.08               0.218
spin_cheer                  expansion_v1    37    36    1    2.7%    0.08               0.230
drink_tea                   expansion_v1    55    54    1    1.8%    0.08               0.353
stretch_back                expansion_v1    60    59    1    1.7%    0.08               0.569
shrug                       expansion_v1    60    59    1    1.7%    0.08               0.910
salute                      expansion_v1    59    58    1    1.7%    0.08               0.855
wave_both                   expansion_v1    60    59    1    1.7%    0.08               0.920
hug_self                    expansion_v1    52    51    1    1.9%    0.08               0.371
look_down_sad               expansion_v1    57    56    1    1.8%    0.08               0.915
stretch_hold                expansion_v1    62    61    1    1.6%    0.08               0.389
drink_water_hold            expansion_v1    62    61    1    1.6%    0.08               0.444
salute_hold                 expansion_v1    61    60    1    1.6%    0.08               0.855
pat_chest_hold              expansion_v1    62    61    1    1.6%    0.08               0.722
spring_reverse              expansion_v1    22    21    1    4.5%    0.08               0.502
sit_stretch_hold            expansion_v1    37    36    1    2.7%    0.08               0.676
drink_tea_hold              expansion_v1    57    56    1    1.8%    0.08               0.353
headphone_hold              expansion_v1    58    57    1    1.7%    0.08               0.708
look_down_hold              expansion_v1    59    58    1    1.7%    0.08               0.915
```

### 2.3 没有命中的（裁完已接近站姿，可硬切）

```
id                          library          N   d_last   d_peak  pose_ratio
----------------------------------------------------------------------------
wave                        library_v1      52    0.310    3.642       0.085
stand_up                    library_v1      44    1.198   13.732       0.087
peek_left_reverse           expansion_v1    45    3.045   19.171       0.159
peek_right_reverse          expansion_v1    39    1.554    9.071       0.171
spin_cheer                  expansion_v1    37    2.430   12.633       0.192
spin_cheer_slow             expansion_v1    74    2.430   12.633       0.192
peek_right                  expansion_v1    39    1.776    9.071       0.196
belly_ticklish              library_v1      52    0.976    4.661       0.209
cheer_jump                  expansion_v1    22    2.699   12.366       0.218
peek_left                   expansion_v1    45    4.228   19.171       0.221
happy_sway_pingpong         expansion_v1   118    1.521    6.702       0.227
feed_bread                  library_v1      47    1.333    5.737       0.233
file_drop_receive           library_v1      52    0.890    3.560       0.250
shrug_pingpong              expansion_v1   118    1.337    5.163       0.259
spring_reverse              expansion_v1    22    3.649   12.366       0.295
listen                      library_v1      52    1.157    3.859       0.300
hug_self_pingpong           expansion_v1   102    1.726    5.684       0.304
bow                         expansion_v1    42    3.087    9.616       0.321
hug_self                    expansion_v1    52    1.843    5.684       0.324
cover_mouth                 expansion_v1    38    1.728    4.999       0.346
cover_mouth_slow            expansion_v1    76    1.728    4.999       0.346
drink_tea                   expansion_v1    55    2.113    5.990       0.353
drink_tea_hold              expansion_v1    57    2.113    5.990       0.353
stretch                     expansion_v1    60    3.078    8.532       0.361
stretch_hold                expansion_v1    62    3.078    8.532       0.361
nod                         library_v1      35    2.109    5.732       0.368
head_pat                    library_v1      47    2.299    6.197       0.371
push_ball                   expansion_v1    37   10.692   27.345       0.391
tidy_desk                   expansion_v1    30    5.358   13.058       0.410
tidy_desk_reverse           expansion_v1    30    5.643   13.058       0.432
drink_water                 expansion_v1    60    2.426    5.452       0.445
drink_water_hold            expansion_v1    62    2.426    5.452       0.445
tidy_hair_pingpong          expansion_v1   118    2.148    4.779       0.449
celebrate                   library_v1      52    2.831    6.170       0.459
tidy_hair                   expansion_v1    60    2.254    4.779       0.472
ribbon_adjust               expansion_v1    60    2.393    5.059       0.473
wave_hello                  expansion_v1    60    2.954    6.130       0.482
wave_slow                   expansion_v1   120    2.954    6.130       0.482
nod_yes                     expansion_v1    60    2.957    5.460       0.541
nod_slow                    expansion_v1   120    2.957    5.460       0.541
wake_up                     library_v1      77   10.579   19.264       0.549
listen_pingpong             expansion_v1   118    4.153    7.504       0.553
high_five                   library_v1      51    2.511    4.468       0.562
clap_happy                  expansion_v1    56    3.078    5.378       0.572
happy_sway                  expansion_v1    60    3.906    6.702       0.583
feed_refuse                 library_v1      52    3.119    5.313       0.587
```

## 3. 口径（v2 生效，改这里等于改口径）

```
descriptor(frame) = RGBA → RGB × alpha/255（alpha 预乘）
                    → Image.BOX 缩到 80×120
                    → GaussianBlur(sigma=1.6) → float32
M[i] = mean(|desc[i+1] - desc[i]|)      i = 0..N-2
S[i] = median(M[max(0,i-2) .. i+2])     5 帧滑动中值
P    = max(S)                           峰值运动
thr  = max(0.20 × P, 0.008)             S 低于此即静止
last_motion = max{ i : S[i] > thr }     最后一个还在动的帧（无则 -1）
keep = clamp(last_motion + 1, max(6, ceil(0.35×N)), N)
loop=True → keep = N（不裁）
d[i] = mean(|desc[i] - desc_ref|)      站姿参考帧的偏移
ends_off_pose = d[keep-1] / max(d) > 0.60
```

- `keep` 是**保留前 keep 帧**的帧数（`frames[0:keep]`），不是索引。
- `+1` 只让最后一个有动的帧自己留下，**不留缓冲**——这是 v2 的核心。
- `thr` 双下限的作用：`0.20 × P` 让阈值随动作幅度缩放（大动作容忍大残留），`0.008` 兜住 `P` 本身很小的动作（否则 3% 的峰值会低到被噪声淹没）。
- `F_tail` / `last_motion_2F` / `last_motion_30P` / `keep_2F` / `keep_30P` 是 v1 对照列，**不参与决策**。

两个工程细节，直接决定数字对不对：

- **必须预乘 alpha**。这些帧是透明背景立绘，直接拿 RGB 算，透明区的编码残留会被当成运动，底噪虚高，尾巴反而量不出来。预乘后透明区恒为 0，M 只反映看得见的像素变化。
- **必须按帧序算，不能按唯一文件去重**。hold / slow / pingpong 会重复引用同一个 webp（本次 20 条动作有重复引用）。重复引用在时间轴上是两帧、中间隔着别的帧，当一帧算就错了。脚本只按路径缓存「解码结果」（同一文件解出来必然一样，缓存不改语义），索引一律走帧序。

## 4. 规则的已知行为（不是 bug，是取舍）

### 4.1 `loop=True` 整段保留：10 条

闭环素材（walk / sleep_loop / pingpong / sit_idle）砍了尾巴，回环接缝就断，播放器从尾接回头会跳帧。按 Lead 口径这批准许不动。

```
id                          library          N  keep     裁%     省s  ends_off_pose
-----------------------------------------------------------------------------------
sit_idle                    library_v1      18    18    0.0%    0.00           True
sleep_loop                  library_v1      54    54    0.0%    0.00           True
walk_left                   library_v1      62    62    0.0%    0.00           True
walk_right                  library_v1      62    62    0.0%    0.00           True
listen_pingpong             expansion_v1   118   118    0.0%    0.00          False
happy_sway_pingpong         expansion_v1   118   118    0.0%    0.00          False
shrug_pingpong              expansion_v1   118   118    0.0%    0.00          False
tidy_hair_pingpong          expansion_v1   118   118    0.0%    0.00          False
hug_self_pingpong           expansion_v1   102   102    0.0%    0.00          False
sleepy_nod_pingpong         expansion_v1   108   108    0.0%    0.00           True
```

### 4.2 被 keep 下限挡住：0 条 `floor-bound`

`last_motion + 1` 比 `max(6, ceil(0.35N))` 还短，下限把它们抬回来。`keep_raw` 是放下限的长度——差额就是被规则吃掉的收益。这批要再短就得动下限。


### 4.3 N < 6 的动作裁不动：0 条

下限 `max(6, ceil(0.35N))` 高于上限 `N`，clamp 回落到 N。不是没有尾巴，是规则不允许裁。


### 4.4 hold 族尾巴是逐帧完全相同的死图：0 条

这批全是 `*_hold`，构造方式是「首帧复制 5 份 + 主体 + 末帧复制 5 份」。尾部差分**严格等于 0**，不依赖阈值。`exact_tail_frames` 是这段死图的长度。


### 4.5 全片都没有超过 thr 的帧：0 条

（无。没有整段静止的动作。）

## 5. 全量表

列：`N` = 原帧数；`keep` = 建议保留帧数；`砍` = 砍掉帧数；`裁%` = 裁剪比例；`省s` = 按 duration_ms 实际省下的秒数；`ends_off_pose` = 结尾不是站姿；`P` = 峰值运动；`thr` = 静止阈值；`last` = `last_motion`（最后一个还在动的帧索引）；`loop` = 该动作是否是闭环素材（闭环不裁）。

### 5.1 library_v1 — manifest.json · usable_actions（23 条）

```
id                             N  keep   砍     裁%     省s  ends_off_pose       P     thr  last  loop
------------------------------------------------------------------------------------------------------
fun_facepalm                  10     9    1   10.0%    0.26           True   2.190  0.4381     8     -
land_recover_v4_12            10     9    1   10.0%    0.09           True  14.389  2.8778     8     -
harness_task_thinking_v1_12   10     9    1   10.0%    0.36           True   3.086  0.6173     8     -
harness_tool_working_v1_12    10     9    1   10.0%    0.30           True   3.288  0.6575     8     -
listen                        52    51    1    1.9%    0.08          False   1.031  0.2062    50     -
sit_down                      60    54    6   10.0%    0.50           True   1.163  0.2327    53     -
sit_idle                      18    18    0    0.0%    0.00           True   0.759  0.1519    16     Y
stand_up                      44    43    1    2.3%    0.08          False   3.260  0.6521    42     -
sleep_lie_down                50    49    1    2.0%    0.08           True   3.279  0.6557    48     -
sleep_loop                    54    54    0    0.0%    0.00           True   0.875  0.1750    52     Y
wake_up                       77    73    4    5.2%    0.33          False   4.699  0.9398    72     -
walk_left                     62    62    0    0.0%    0.00           True   1.473  0.2945    60     Y
walk_right                    62    62    0    0.0%    0.00           True   1.471  0.2941    60     Y
wave                          52    47    5    9.6%    0.41          False   0.927  0.1854    46     -
celebrate                     52    51    1    1.9%    0.08          False   4.114  0.8228    50     -
head_pat                      47    45    2    4.3%    0.17          False   1.508  0.3016    44     -
belly_ticklish                52    51    1    1.9%    0.08          False   1.297  0.2594    50     -
cheek_poke                    52    51    1    1.9%    0.08           True   1.182  0.2365    50     -
high_five                     51    50    1    2.0%    0.08          False   1.306  0.2612    49     -
file_drop_receive             52    51    1    1.9%    0.08          False   0.975  0.1950    50     -
feed_bread                    47    45    2    4.3%    0.17          False   1.986  0.3971    44     -
feed_refuse                   52    51    1    1.9%    0.08          False   1.077  0.2155    50     -
nod                           35    34    1    2.9%    0.08          False   1.357  0.2714    33     -
```

### 5.2 expansion_v1 — actions.json · actions（64 条）

```
id                     N  keep   砍     裁%     省s  ends_off_pose       P     thr  last  loop
----------------------------------------------------------------------------------------------
stretch               60    59    1    1.7%    0.08          False   1.827  0.3653    58     -
drink_water           60    59    1    1.7%    0.08          False   0.935  0.1870    58     -
peek_note             41    40    1    2.4%    0.08           True   1.667  0.3334    39     -
think_pencil          60    59    1    1.7%    0.08           True   1.019  0.2038    58     -
tidy_desk             30    29    1    3.3%    0.08          False   3.895  0.7790    28     -
ribbon_adjust         60    59    1    1.7%    0.08          False   1.439  0.2878    58     -
turn_show             43    42    1    2.3%    0.08           True   7.196  1.4391    41     -
search_pocket         51    50    1    2.0%    0.08           True   1.907  0.3814    49     -
push_ball             37    36    1    2.7%    0.08          False   3.502  0.7004    35     -
catch_star            60    59    1    1.7%    0.08           True   1.311  0.2621    58     -
headphone             56    55    1    1.8%    0.08           True   3.809  0.7618    54     -
lantern_off           42    41    1    2.4%    0.08           True   3.141  0.6281    40     -
wave_hello            60    59    1    1.7%    0.08          False   2.171  0.4342    58     -
nod_yes               60    59    1    1.7%    0.08          False   1.104  0.2208    58     -
shake_head            60    59    1    1.7%    0.08           True   1.148  0.2297    58     -
yawn                  60    59    1    1.7%    0.08           True   1.567  0.3135    58     -
cheer_jump            22    14    8   36.4%    0.66          False   5.774  1.1548    13     -
listen_tilt           60    59    1    1.7%    0.08           True   1.460  0.2921    58     -
shy_look              60    59    1    1.7%    0.08           True   1.238  0.2477    58     -
clap_happy            56    55    1    1.8%    0.08          False   1.732  0.3465    54     -
bow                   42    41    1    2.4%    0.08          False   3.004  0.6007    40     -
point_forward         22    19    3   13.6%    0.25           True   3.270  0.6540    18     -
look_up               51    50    1    2.0%    0.08           True   1.396  0.2792    49     -
cover_mouth           38    37    1    2.6%    0.08          False   1.696  0.3391    36     -
pat_own_chest         60    59    1    1.7%    0.08           True   1.093  0.2187    58     -
tidy_hair             60    59    1    1.7%    0.08          False   0.705  0.1409    58     -
sit_stretch           35    34    1    2.9%    0.08           True   7.064  1.4127    33     -
kneel_look            50    49    1    2.0%    0.08           True   6.966  1.3932    48     -
sleepy_nod            55    54    1    1.8%    0.08           True   4.331  0.8661    53     -
happy_sway            60    59    1    1.7%    0.08          False   1.935  0.3870    58     -
peek_left             45    44    1    2.2%    0.08          False   3.483  0.6965    43     -
peek_right            39    38    1    2.6%    0.08          False   2.891  0.5782    37     -
spin_cheer            37    36    1    2.7%    0.08          False   3.001  0.6001    35     -
drink_tea             55    54    1    1.8%    0.08          False   1.013  0.2026    53     -
stretch_back          60    59    1    1.7%    0.08           True   1.728  0.3456    58     -
shrug                 60    59    1    1.7%    0.08           True   1.326  0.2652    58     -
salute                59    58    1    1.7%    0.08           True   1.899  0.3798    57     -
wave_both             60    59    1    1.7%    0.08           True   4.096  0.8193    58     -
hug_self              52    51    1    1.9%    0.08          False   2.322  0.4645    50     -
look_down_sad         57    56    1    1.8%    0.08           True   1.532  0.3065    55     -
tidy_desk_reverse     30    26    4   13.3%    0.33          False   3.895  0.7790    25     -
wave_slow            120   118    2    1.7%    0.17          False   1.907  0.3815   117     -
nod_slow             120   116    4    3.3%    0.33          False   1.095  0.2190   115     -
listen_pingpong      118   118    0    0.0%    0.00          False   1.460  0.2921   116     Y
happy_sway_pingpong  118   118    0    0.0%    0.00          False   1.935  0.3870   116     Y
stretch_hold          62    61    1    1.6%    0.08          False   1.827  0.3653    60     -
drink_water_hold      62    61    1    1.6%    0.08          False   0.935  0.1870    60     -
salute_hold           61    60    1    1.6%    0.08           True   1.899  0.3798    59     -
shrug_pingpong       118   118    0    0.0%    0.00          False   1.326  0.2652   116     Y
look_up_slow         102    98    4    3.9%    0.33           True   1.396  0.2792    97     -
cover_mouth_slow      76    74    2    2.6%    0.17          False   0.972  0.1944    73     -
pat_chest_hold        62    61    1    1.6%    0.08           True   1.093  0.2187    60     -
tidy_hair_pingpong   118   118    0    0.0%    0.00          False   0.705  0.1409   116     Y
spring_reverse        22    21    1    4.5%    0.08          False   5.774  1.1548    20     -
sit_stretch_hold      37    36    1    2.7%    0.08           True   7.064  1.4127    35     -
peek_left_reverse     45    41    4    8.9%    0.33          False   3.483  0.6965    40     -
peek_right_reverse    39    29   10   25.6%    0.83          False   2.891  0.5782    28     -
spin_cheer_slow       74    72    2    2.7%    0.17          False   2.235  0.4470    71     -
wave_both_slow       120   118    2    1.7%    0.17           True   2.340  0.4681   117     -
hug_self_pingpong    102   102    0    0.0%    0.00          False   2.322  0.4645    96     Y
sleepy_nod_pingpong  108   108    0    0.0%    0.00           True   4.331  0.8661   106     Y
drink_tea_hold        57    56    1    1.8%    0.08          False   1.013  0.2026    55     -
headphone_hold        58    57    1    1.7%    0.08           True   3.809  0.7618    56     -
look_down_hold        59    58    1    1.7%    0.08           True   1.532  0.3065    57     -
```

### 5.3 S 曲线尾部 12 值（人工复核用）

`thr` 一列是对应动作的静止阈值；`S[-12:]` 里掉到 `thr` 以下的位置就是尾巴起点。

```
id                          library           thr  last  S[-12:]
----------------------------------------------------------------
fun_facepalm                library_v1     0.4381     8   0.65  0.92  1.18  1.67  1.89  1.89  2.00  2.19  2.00
land_recover_v4_12          library_v1     2.8778     8  10.29 14.39 10.29 10.29 10.29 10.64  9.83 10.09 10.64
harness_task_thinking_v1_12  library_v1     0.6173     8   1.20  1.38  1.56  1.75  2.16  2.16  2.16  3.09  1.87
harness_tool_working_v1_12  library_v1     0.6575     8   1.67  1.90  2.12  2.20  2.20  3.29  3.18  2.69  3.18
listen                      library_v1     0.2062    50   0.49  0.47  0.47  0.61  0.61  1.03  1.03  1.03  0.60  0.60  0.58  0.55
sit_down                    library_v1     0.2327    53   0.47  0.46  0.46  0.47  0.32  0.44  0.44  0.21  0.11  0.11  0.11  0.11
sit_idle                    library_v1     0.1519    16   0.38  0.38  0.38  0.46  0.51  0.57  0.57  0.58  0.57  0.37  0.31  0.24
stand_up                    library_v1     0.6521    42   1.36  1.91  1.42  2.60  1.54  3.26  1.54  3.06  1.54  1.64  1.38  1.64
sleep_lie_down              library_v1     0.6557    48   1.19  2.21  1.36  2.21  1.47  2.21  1.61  3.28  1.61  1.61  1.48  1.36
sleep_loop                  library_v1     0.1750    52   0.50  0.53  0.70  0.70  0.70  0.79  0.79  0.53  0.36  0.33  0.30  0.29
wake_up                     library_v1     0.9398    72   2.90  1.72  1.37  1.16  1.16  1.14  1.16  1.14  1.15  0.91  0.86  0.58
walk_left                   library_v1     0.2945    60   1.23  1.23  1.23  1.23  1.10  1.10  1.10  1.10  1.05  1.05  1.26  1.47
walk_right                  library_v1     0.2941    60   1.23  1.23  1.23  1.23  1.10  1.09  1.09  1.09  1.04  1.04  1.26  1.47
wave                        library_v1     0.1854    46   0.42  0.42  0.42  0.55  0.55  0.55  0.32  0.32  0.18  0.17  0.17  0.17
celebrate                   library_v1     0.8228    50   2.06  2.48  2.71  4.11  2.71  2.48  2.31  2.31  1.79  1.96  1.87  1.96
head_pat                    library_v1     0.3016    44   0.87  0.53  1.48  0.53  0.84  0.53  0.50  0.34  0.50  0.28  0.39  0.28
belly_ticklish              library_v1     0.2594    50   0.58  0.67  0.72  1.30  1.30  1.30  0.54  0.49  0.48  0.32  0.30  0.29
cheek_poke                  library_v1     0.2365    50   0.17  0.35  0.60  0.81  0.81  0.97  0.81  0.76  0.57  0.49  0.48  0.47
high_five                   library_v1     0.2612    49   0.61  0.26  0.52  0.28  0.52  0.38  0.52  0.39  1.00  0.39  0.70  0.39
file_drop_receive           library_v1     0.1950    50   0.70  0.70  0.58  0.65  0.58  0.58  0.39  0.36  0.30  0.30  0.30  0.30
feed_bread                  library_v1     0.3971    44   0.41  0.35  0.68  0.46  0.68  0.46  0.88  0.46  0.88  0.30  0.59  0.30
feed_refuse                 library_v1     0.2155    50   0.84  0.84  0.55  0.53  0.53  0.55  0.48  0.64  0.64  0.59  0.53  0.59
nod                         library_v1     0.2714    33   1.36  1.27  1.27  1.27  1.27  1.31  1.33  1.33  1.33  0.91  0.74  0.58
stretch                     expansion_v1   0.3653    58   0.98  0.79  0.66  0.49  0.49  0.49  0.48  0.48  0.48  0.48  0.45  0.42
drink_water                 expansion_v1   0.1870    58   0.57  0.57  0.57  0.43  0.31  0.26  0.26  0.25  0.25  0.25  0.25  0.25
peek_note                   expansion_v1   0.3334    39   0.31  0.31  0.31  0.32  0.31  0.30  0.28  0.28  0.28  0.33  0.33  0.34
think_pencil                expansion_v1   0.2038    58   0.37  0.37  0.37  0.37  0.37  0.34  0.29  0.29  0.29  0.28  0.26  0.28
tidy_desk                   expansion_v1   0.7790    28   0.35  0.25  0.25  0.25  0.40  0.98  2.64  2.84  2.84  2.84  2.50  2.17
ribbon_adjust               expansion_v1   0.2878    58   0.60  0.60  0.81  0.81  0.81  0.61  0.61  0.61  0.61  0.61  0.70  0.55
turn_show                   expansion_v1   1.4391    41   0.32  0.33  0.35  0.36  0.72  0.73  1.08  1.10  1.60  2.41  4.80  7.20
search_pocket               expansion_v1   0.3814    49   1.40  1.08  1.07  0.93  0.82  0.79  0.69  0.65  0.45  0.45  0.44  0.45
push_ball                   expansion_v1   0.7004    35   0.96  0.97  3.50  3.50  3.50  1.24  1.14  1.14  1.24  1.31  1.88  2.46
catch_star                  expansion_v1   0.2621    58   0.47  0.47  0.45  0.38  0.38  0.37  0.37  0.39  0.39  0.38  0.39  0.38
headphone                   expansion_v1   0.7618    54   1.18  0.96  0.73  0.66  0.66  0.66  0.98  1.06  1.08  1.06  1.07  1.06
lantern_off                 expansion_v1   0.6281    40   1.62  1.39  1.04  0.69  0.69  0.69  0.75  0.86  0.86  0.86  0.85  0.84
wave_hello                  expansion_v1   0.4342    58   0.55  0.42  0.40  0.40  0.40  0.40  0.57  0.89  1.20  1.31  1.34  1.36
nod_yes                     expansion_v1   0.2208    58   0.27  0.27  0.27  0.27  0.23  0.23  0.23  0.27  0.27  0.27  0.30  0.33
shake_head                  expansion_v1   0.2297    58   0.75  0.68  0.60  0.44  0.35  0.30  0.27  0.27  0.27  0.28  0.29  0.31
yawn                        expansion_v1   0.3135    58   0.42  0.47  0.47  0.47  0.41  0.41  0.41  0.47  0.47  0.47  0.42  0.37
cheer_jump                  expansion_v1   1.1548    13   2.96  2.96  2.73  1.80  1.40  0.91  0.85  0.63  0.46  0.46  0.43  0.41
listen_tilt                 expansion_v1   0.2921    58   0.44  0.44  0.39  0.34  0.34  0.34  0.33  0.35  0.35  0.33  0.33  0.33
shy_look                    expansion_v1   0.2477    58   0.27  0.27  0.27  0.30  0.34  0.36  0.36  0.36  0.36  0.35  0.33  0.31
clap_happy                  expansion_v1   0.3465    54   1.05  1.05  0.81  0.58  0.58  0.56  0.56  0.56  0.37  0.37  0.35  0.37
bow                         expansion_v1   0.6007    40   0.66  1.87  2.27  2.27  2.27  1.17  0.92  0.88  0.88  0.66  0.66  0.65
point_forward               expansion_v1   0.6540    18   2.84  3.27  2.84  2.84  1.89  1.48  0.91  0.83  0.71  0.71  0.60  0.50
look_up                     expansion_v1   0.2792    49   0.33  0.32  0.26  0.26  0.26  0.26  0.27  0.29  0.30  0.32  0.32  0.32
cover_mouth                 expansion_v1   0.3391    36   0.21  0.21  0.21  0.21  0.21  0.21  0.46  0.51  0.75  0.75  0.79  0.75
pat_own_chest               expansion_v1   0.2187    58   0.39  0.45  0.45  0.45  0.40  0.38  0.33  0.27  0.27  0.27  0.25  0.27
tidy_hair                   expansion_v1   0.1409    58   0.27  0.35  0.35  0.35  0.34  0.34  0.29  0.28  0.28  0.28  0.28  0.28
sit_stretch                 expansion_v1   1.4127    33   0.88  0.88  0.95  0.95  0.95  0.70  0.70  0.70  0.83  1.55  1.72  1.88
kneel_look                  expansion_v1   1.3932    48   0.28  0.28  0.27  0.27  0.27  0.31  0.74  1.74  2.10  2.27  2.44  2.60
sleepy_nod                  expansion_v1   0.8661    53   0.89  0.89  0.89  0.64  0.45  0.35  0.35  0.35  0.50  1.28  1.28  1.28
happy_sway                  expansion_v1   0.3870    58   1.16  1.13  1.13  1.16  1.72  1.48  1.48  1.48  1.48  1.41  1.39  1.41
peek_left                   expansion_v1   0.6965    43   2.50  2.57  2.57  3.48  2.90  2.90  2.90  1.60  1.57  0.94  0.86  0.79
peek_right                  expansion_v1   0.5782    37   0.34  0.34  0.29  0.29  0.29  2.89  2.89  2.89  1.51  0.97  0.96  0.95
spin_cheer                  expansion_v1   0.6001    35   2.04  1.76  2.04  1.78  2.18  1.92  2.44  1.98  2.47  1.98  2.23  1.98
drink_tea                   expansion_v1   0.2026    53   0.30  0.40  0.42  0.47  0.61  0.61  0.61  0.49  0.41  0.33  0.28  0.24
stretch_back                expansion_v1   0.3456    58   0.95  1.10  1.65  1.70  1.70  1.70  1.70  1.70  1.62  1.05  0.95  0.85
shrug                       expansion_v1   0.2652    58   0.40  0.41  0.42  0.42  0.42  0.36  0.36  0.36  0.36  0.45  0.45  0.45
salute                      expansion_v1   0.3798    57   0.44  0.47  0.44  0.47  0.42  0.47  0.47  0.48  0.47  0.47  0.43  0.40
wave_both                   expansion_v1   0.8193    58   0.89  0.89  0.95  0.95  0.95  0.95  0.95  0.93  0.93  1.01  1.12  1.35
hug_self                    expansion_v1   0.4645    50   0.44  0.34  0.33  0.33  0.33  0.90  2.32  2.32  2.32  1.22  1.01  0.80
look_down_sad               expansion_v1   0.3065    55   0.30  0.27  0.24  0.24  0.24  0.24  0.28  0.28  0.29  0.30  0.31  0.32
tidy_desk_reverse           expansion_v1   0.7790    25   1.85  3.11  3.90  3.90  3.90  2.82  1.93  1.16  0.92  0.57  0.39  0.22
wave_slow                   expansion_v1   0.3815   117   0.37  0.00  0.57  0.00  0.89  0.00  1.20  0.00  1.31  0.00  0.68  0.00
nod_slow                    expansion_v1   0.2190   115   0.21  0.00  0.23  0.00  0.27  0.00  0.27  0.00  0.27  0.00  0.14  0.00
listen_pingpong             expansion_v1   0.2921   116   0.35  0.38  0.41  0.78  1.40  1.46  1.46  1.46  1.24  1.21  1.03  0.85
happy_sway_pingpong         expansion_v1   0.3870   116   1.87  1.49  1.32  1.28  1.21  1.28  1.32  1.41  1.43  1.43  1.61  1.43
stretch_hold                expansion_v1   0.3653    60   0.98  0.79  0.66  0.49  0.49  0.49  0.48  0.48  0.48  0.48  0.45  0.42
drink_water_hold            expansion_v1   0.1870    60   0.57  0.57  0.57  0.43  0.31  0.26  0.26  0.25  0.25  0.25  0.25  0.25
salute_hold                 expansion_v1   0.3798    59   0.44  0.47  0.44  0.47  0.42  0.47  0.47  0.48  0.47  0.47  0.43  0.40
shrug_pingpong              expansion_v1   0.2652   116   0.52  0.45  0.42  0.33  0.32  0.32  0.32  0.43  0.50  0.50  0.50  0.50
look_up_slow                expansion_v1   0.2792    97   0.26  0.00  0.27  0.00  0.29  0.00  0.30  0.00  0.30  0.00  0.16  0.00
cover_mouth_slow            expansion_v1   0.1944    73   0.20  0.00  0.46  0.00  0.46  0.00  0.75  0.00  0.58  0.00  0.29  0.00
pat_chest_hold              expansion_v1   0.2187    60   0.39  0.45  0.45  0.45  0.40  0.38  0.33  0.27  0.27  0.27  0.25  0.27
tidy_hair_pingpong          expansion_v1   0.1409   116   0.39  0.41  0.45  0.47  0.56  0.70  0.70  0.70  0.68  0.61  0.60  0.58
spring_reverse              expansion_v1   1.1548    20   2.73  2.96  2.96  2.96  2.44  2.44  2.44  3.59  3.59  3.59  5.77  3.56
sit_stretch_hold            expansion_v1   1.4127    35   0.88  0.88  0.95  0.95  0.95  0.70  0.70  0.70  0.83  1.55  1.72  1.88
peek_left_reverse           expansion_v1   0.6965    40   2.79  2.79  2.79  2.52  2.38  2.09  1.19  0.92  0.84  0.65  0.65  0.65
peek_right_reverse          expansion_v1   0.5782    28   1.14  0.98  0.86  0.37  0.37  0.41  0.41  0.40  0.40  0.38  0.31  0.24
spin_cheer_slow             expansion_v1   0.4470    71   1.78  0.00  1.92  0.00  1.92  0.00  1.98  0.00  1.24  0.00  0.62  0.00
wave_both_slow              expansion_v1   0.4681   117   0.93  0.00  0.79  0.00  0.79  0.00  0.79  0.00  0.90  0.00  0.67  0.00
hug_self_pingpong           expansion_v1   0.4645    96   0.61  0.62  0.62  0.75  1.10  1.80  1.80  1.80  0.31  0.21  0.21  0.21
sleepy_nod_pingpong         expansion_v1   0.8661   106   0.33  0.33  0.33  0.70  1.41  1.68  1.74  4.33  4.33  4.33  3.36  2.40
drink_tea_hold              expansion_v1   0.2026    55   0.30  0.40  0.42  0.47  0.61  0.61  0.61  0.49  0.41  0.33  0.28  0.24
headphone_hold              expansion_v1   0.7618    56   1.18  0.96  0.73  0.66  0.66  0.66  0.98  1.06  1.08  1.06  1.07  1.06
look_down_hold              expansion_v1   0.3065    57   0.30  0.27  0.24  0.24  0.24  0.24  0.28  0.28  0.29  0.30  0.31  0.32
```

## 6. v1 → v2 差异（哪些动作被改判）

`keep_2F` 是 v1 的结论，`keep` 是 v2 的结论。`Δ` = v2 − v1，负数表示 v2 裁得更狠。

```
id                          library          N  keep_2F  keep      Δ  变化    
------------------------------------------------------------------------------
cheer_jump                  expansion_v1    22       16    14     -2  更狠    
wave_slow                   expansion_v1   120      120   118     -2  更狠    
nod_slow                    expansion_v1   120      118   116     -2  更狠    
look_up_slow                expansion_v1   102      100    98     -2  更狠    
peek_right_reverse          expansion_v1    39       31    29     -2  更狠    
fun_facepalm                library_v1      10       10     9     -1  更狠    
land_recover_v4_12          library_v1      10       10     9     -1  更狠    
harness_task_thinking_v1_12  library_v1      10       10     9     -1  更狠    
harness_tool_working_v1_12  library_v1      10       10     9     -1  更狠    
turn_show                   expansion_v1    43       43    42     -1  更狠    
wave_hello                  expansion_v1    60       60    59     -1  更狠    
cover_mouth                 expansion_v1    38       38    37     -1  更狠    
sit_stretch                 expansion_v1    35       35    34     -1  更狠    
kneel_look                  expansion_v1    50       50    49     -1  更狠    
sit_stretch_hold            expansion_v1    37       37    36     -1  更狠    
tidy_desk                   expansion_v1    30       29    29      0  同      
cover_mouth_slow            expansion_v1    76       74    74      0  同      
wave_both_slow              expansion_v1   120      118   118      0  同      
sleepy_nod_pingpong         expansion_v1   108      108   108      0  同      
high_five                   library_v1      51       49    50      1  更松    
hug_self                    expansion_v1    52       50    51      1  更松    
sit_idle                    library_v1      18       16    18      2  更松    
sleep_lie_down              library_v1      50       47    49      2  更松    
peek_right                  expansion_v1    39       36    38      2  更松    
tidy_desk_reverse           expansion_v1    30       24    26      2  更松    
spin_cheer_slow             expansion_v1    74       70    72      2  更松    
point_forward               expansion_v1    22       16    19      3  更松    
hug_self_pingpong           expansion_v1   102       99   102      3  更松    
stand_up                    library_v1      44       39    43      4  更松    
belly_ticklish              library_v1      52       47    51      4  更松    
push_ball                   expansion_v1    37       31    36      5  更松    
bow                         expansion_v1    42       36    41      5  更松    
spring_reverse              expansion_v1    22       16    21      5  更松    
wake_up                     library_v1      77       67    73      6  更松    
wave                        library_v1      52       41    47      6  更松    
head_pat                    library_v1      47       39    45      6  更松    
sit_down                    library_v1      60       47    54      7  更松    
drink_water                 expansion_v1    60       52    59      7  更松    
drink_water_hold            expansion_v1    62       54    61      7  更松    
shake_head                  expansion_v1    60       51    59      8  更松    
stretch                     expansion_v1    60       50    59      9  更松    
stretch_hold                expansion_v1    62       52    61      9  更松    
file_drop_receive           library_v1      52       41    51     10  更松    
shrug_pingpong              expansion_v1   118      105   118     13  更松    
nod                         library_v1      35       18    34     16  更松    
pat_own_chest               expansion_v1    60       43    59     16  更松    
pat_chest_hold              expansion_v1    62       45    61     16  更松    
ribbon_adjust               expansion_v1    60       42    59     17  更松    
spin_cheer                  expansion_v1    37       19    36     17  更松    
yawn                        expansion_v1    60       41    59     18  更松    
peek_left_reverse           expansion_v1    45       23    41     18  更松    
peek_note                   expansion_v1    41       21    40     19  更松    
lantern_off                 expansion_v1    42       21    41     20  更松    
look_up                     expansion_v1    51       30    50     20  更松    
feed_bread                  library_v1      47       24    45     21  更松    
peek_left                   expansion_v1    45       23    44     21  更松    
wave_both                   expansion_v1    60       38    59     21  更松    
catch_star                  expansion_v1    60       36    59     23  更松    
salute                      expansion_v1    59       35    58     23  更松    
salute_hold                 expansion_v1    61       37    60     23  更松    
search_pocket               expansion_v1    51       26    50     24  更松    
listen                      library_v1      52       26    51     25  更松    
celebrate                   library_v1      52       26    51     25  更松    
cheek_poke                  library_v1      52       26    51     25  更松    
feed_refuse                 library_v1      52       26    51     25  更松    
sleepy_nod                  expansion_v1    55       28    54     26  更松    
drink_tea                   expansion_v1    55       28    54     26  更松    
sleep_loop                  library_v1      54       27    54     27  更松    
headphone                   expansion_v1    56       28    55     27  更松    
clap_happy                  expansion_v1    56       28    55     27  更松    
look_down_sad               expansion_v1    57       29    56     27  更松    
drink_tea_hold              expansion_v1    57       29    56     27  更松    
headphone_hold              expansion_v1    58       29    57     28  更松    
look_down_hold              expansion_v1    59       30    58     28  更松    
think_pencil                expansion_v1    60       30    59     29  更松    
nod_yes                     expansion_v1    60       30    59     29  更松    
listen_tilt                 expansion_v1    60       30    59     29  更松    
shy_look                    expansion_v1    60       30    59     29  更松    
tidy_hair                   expansion_v1    60       30    59     29  更松    
happy_sway                  expansion_v1    60       30    59     29  更松    
stretch_back                expansion_v1    60       30    59     29  更松    
shrug                       expansion_v1    60       30    59     29  更松    
walk_left                   library_v1      62       31    62     31  更松    
walk_right                  library_v1      62       31    62     31  更松    
listen_pingpong             expansion_v1   118       59   118     59  更松    
happy_sway_pingpong         expansion_v1   118       59   118     59  更松    
tidy_hair_pingpong          expansion_v1   118       59   118     59  更松    
```

## 7. 复现

```bash
/tmp/mimienv/bin/python scripts/pipeline/tail_motion.py --report
```

生成器 `scripts/pipeline/tail_motion.py`，机器可读版 `docs/expansion/trim_proposal.json`。口径常量：BOX=(80, 120)，sigma=1.6，平滑半径=2，thr=max(0.2×P, 0.008)，keep=last+1，下限=max(6, 0.35N)，ends_off_pose 阈值=pose_ratio>0.6，站姿锚点=wave/frames/0001.webp。
