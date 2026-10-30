"""造型的动作退化表：把任意动作 id 落到这套衣服**自己的**帧上。

设计背景
--------
造型素材每套只有 15 个动作（`gen_outfit_actions.OUTFIT_ACTIONS`），而动作库有
87 个。旧策略（见 `engine._playable_spec` 的历史注释）是"造型没有就明确拒绝"，
目的是避免"换着睡衣挥手时突然变回原衣服"。问题是拒绝得太彻底：摸肚子、击掌、
喂食、拖文件、坐下、玩具、DSH 思考动画……全部静默失效，用户感受就是
"换了造型以后动作都没反应"。

这里给出折中：**造型缺某个动作时，沿语义近似链退化到这套衣服自己的另一个
动作**，永远不借用默认服装的帧。这样：

* 衣服不会突然变回去（原始约束仍然成立）；
* 每个交互仍然有反馈，只是动作是"近似"的；
* 不再需要为 87×10 个组合生成素材。

退化链只在**造型自己拥有的动作**里挑，所以未来某套造型补了真素材，
同一条链会自动选中真素材，不需要改代码。
"""

from __future__ import annotations

#: 造型素材一共只产出这 15 个动作 id（与 `presets.json` 的 `actions` 键一致）。
#: 仅用于文档与校验，运行时不依赖它。
OUTFIT_ACTION_IDS: tuple[str, ...] = (
    "walk_left",
    "walk_right",
    "head_pat",
    "cheek_poke",
    "celebrate",
    "wave",
    "nod",
    "yawn",
    "clap",
    "land_recover",
    "listen",
    "look_up",
    "shy",
    "sit_idle",
    "tilt_head",
)

#: 语义近似链：请求的动作 id → 造型上可接受的替代动作（按优先级）。
#: 只列这 15 个 id；解析时逐项检查造型是否真的有，没有就继续往后找。
OUTFIT_ACTION_FALLBACKS: dict[str, tuple[str, ...]] = {
    # ---- 挥手家族
    "wave_hello": ("wave",),
    "wave_slow": ("wave",),
    "wave_both": ("wave",),
    "wave_both_slow": ("wave",),
    "salute": ("wave",),
    "salute_hold": ("wave",),
    "lantern_off": ("wave", "nod"),
    "turn_show": ("celebrate", "wave"),
    # ---- 点头家族
    "nod_yes": ("nod",),
    "nod_slow": ("nod",),
    "bow": ("nod",),
    "shrug": ("nod", "tilt_head"),
    "shrug_pingpong": ("nod", "tilt_head"),
    "spring_reverse": ("nod", "celebrate"),
    # ---- 头部/视线家族
    "shake_head": ("tilt_head", "nod"),
    "look_up_slow": ("look_up",),
    "peek_left": ("look_up", "tilt_head"),
    "peek_right": ("look_up", "tilt_head"),
    "peek_left_reverse": ("look_up", "tilt_head"),
    "peek_right_reverse": ("look_up", "tilt_head"),
    "peek_note": ("look_up",),
    "point_forward": ("look_up", "nod"),
    "kneel_look": ("look_up", "sit_idle"),
    "search_pocket": ("look_up", "nod"),
    "tidy_desk": ("look_up", "nod"),
    "tidy_desk_reverse": ("look_up", "nod"),
    "tidy_hair": ("tilt_head",),
    "tidy_hair_pingpong": ("tilt_head",),
    "ribbon_adjust": ("tilt_head",),
    "headphone": ("tilt_head",),
    "headphone_hold": ("tilt_head",),
    "think_pencil": ("look_up",),
    "look_down_hold": ("shy", "tilt_head"),
    "look_down_sad": ("shy", "tilt_head"),
    # ---- 害羞/遮脸家族
    "shy_look": ("shy",),
    "cover_mouth": ("shy", "cheek_poke"),
    "cover_mouth_slow": ("shy",),
    "hug_self": ("shy",),
    "hug_self_pingpong": ("shy",),
    "pat_chest_hold": ("shy",),
    "pat_own_chest": ("shy",),
    "fun_facepalm": ("shy",),
    "feed_refuse": ("shy", "tilt_head"),
    # ---- 庆祝/跳跃家族
    "cheer_jump": ("celebrate",),
    "spin_cheer": ("celebrate",),
    "spin_cheer_slow": ("celebrate",),
    "happy_sway": ("celebrate",),
    "happy_sway_pingpong": ("celebrate",),
    "push_ball": ("celebrate", "clap"),
    "catch_star": ("clap", "celebrate"),
    # ---- 鼓掌/击掌家族
    "high_five": ("clap", "celebrate"),
    "clap_happy": ("clap", "celebrate"),
    # ---- 触摸
    "belly_ticklish": ("cheek_poke", "shy"),
    # ---- 收下/吃东西（用点头表示接受）
    "feed_bread": ("nod", "cheek_poke"),
    "file_drop_receive": ("nod",),
    # ---- 喝水/喝茶（手到脸侧）
    "drink_water": ("nod", "cheek_poke"),
    "drink_water_hold": ("nod", "cheek_poke"),
    "drink_tea": ("nod", "cheek_poke"),
    "drink_tea_hold": ("nod", "cheek_poke"),
    # ---- 倾听
    "listen_pingpong": ("listen",),
    "listen_tilt": ("listen", "tilt_head"),
    # ---- 伸展/困倦（哈欠就是最好的伸展）
    "stretch": ("yawn",),
    "stretch_hold": ("yawn",),
    "stretch_back": ("yawn",),
    "sit_stretch": ("yawn", "sit_idle"),
    "sit_stretch_hold": ("yawn", "sit_idle"),
    "sleepy_nod": ("yawn",),
    "sleepy_nod_pingpong": ("yawn",),
    "wake_up": ("yawn",),
    # ---- 坐/睡（造型只有坐姿循环，没有躺下的素材）
    "sit_down": ("sit_idle",),
    "sleep_lie_down": ("yawn", "sit_idle"),
    "sleep_loop": ("sit_idle",),
    # ---- 落地
    "land_recover_v4_12": ("land_recover", "nod"),
    # ---- DSH 工作动画
    "harness_task_thinking_v1_12": ("look_up",),
    "harness_tool_working_v1_12": ("clap", "nod"),
    # ---- 没有合适近似：交给引擎按状态结算（起身 = 收起动作回待机）
    "stand_up": (),
}

#: 造型连近似动作都没有时的兜底：一个最小、任何造型都有的确认动作。
GENERIC_FALLBACK: tuple[str, ...] = ("nod", "head_pat", "wave", "celebrate")

#: 这些动作的语义是"保持某个姿势"，退化到 `sit_idle` 这类循环片段时应当循环播放。
HOLD_WHEN_LOOPED: frozenset[str] = frozenset(
    {"sit_down", "sit_idle", "sleep_lie_down", "sleep_loop"}
)


def fallback_candidates(action_id: str) -> tuple[str, ...]:
    """该动作在造型上可以退化成哪些动作（按优先级，含通用兜底）。"""
    chain = OUTFIT_ACTION_FALLBACKS.get(action_id)
    if chain is None:
        # 未登记的动作：先用同名近似（去掉版本后缀），否则用通用兜底。
        short = action_id.split("_v", 1)[0]
        chain = (short,) if short in OUTFIT_ACTION_IDS else ()
    return tuple(chain) + GENERIC_FALLBACK


def action_is_hold(action_id: str) -> bool:
    """这个动作是不是"保持姿势"类（退化到循环片段时要循环）。"""
    return action_id in HOLD_WHEN_LOOPED
