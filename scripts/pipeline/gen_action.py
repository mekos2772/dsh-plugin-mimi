"""动作视频队列：提交 → 轮询 → 下载 → 抽帧抠像对齐 → 导出 webp。

用法：
    python gen_action.py list
    python gen_action.py one stretch
    python gen_action.py queue            # 按 ACTIONS 顺序跑完所有未完成的
    python gen_action.py status
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mimi_gen import (  # noqa: E402
    IMG_DIR, OUT_DIR, VID_DIR, build_action_from_video, compose_first_frame,
    download_video, submit_video, wait_video,
)

BASE_REF = Path("/tmp/mimivid/first_idle.png")

NO_CAMERA = (
    "Fixed camera, no zoom, no pan, no rotation, no cuts. "
    "The character stays the same size and same position in frame, feet stay on the ground. "
    "Solid chroma green background, flat 2D anime illustration, clean cel shading, "
    "identical face, hairstyle, eye color and outfit as the first frame."
)

ACTIONS: dict[str, dict] = {
    "stretch": {
        "name_zh": "伸懒腰",
        "desc": "双手举起舒展身体，再放松回站姿。",
        "loop": False,
        "prompt": "She stretches: lifts both arms above her head, closes her eyes briefly, "
                  "arches her back a little, then relaxes her arms back down to her sides. "
                  "Gentle sleepy stretch.",
    },
    "drink_water": {
        "name_zh": "抱杯喝水",
        "desc": "捧起小杯子喝一口，再把杯子放下。",
        "loop": False,
        "prompt": "She holds a small white cup with both hands, lifts it to her mouth, takes a "
                  "small sip, lowers it, and smiles slightly.",
    },
    "peek_note": {
        "name_zh": "展开便签",
        "desc": "举起一张折好的小便签，侧身让出内容位置。",
        "loop": False,
        "prompt": "She lifts a small folded pastel note card with both hands up to chest height, "
                  "turns her body slightly to one side to show it, and holds it steady.",
    },
    "think_pencil": {
        "name_zh": "铅笔思考",
        "desc": "拿起铅笔轻点下巴，若有所思。",
        "loop": False,
        "prompt": "She picks up a short pencil, taps it lightly against her chin twice while "
                  "looking up thoughtfully, tilts her head slightly.",
    },
    "tidy_desk": {
        "name_zh": "整理小桌面",
        "desc": "摆正面前的小书和笔，收好道具。",
        "loop": False,
        "prompt": "She reaches down, straightens a small book and a pencil in front of her, "
                  "nods with satisfaction, and puts her hands back at her sides.",
    },
    "ribbon_adjust": {
        "name_zh": "整理装束",
        "desc": "扶正头饰，满意地看一眼。",
        "loop": False,
        "prompt": "She raises one hand and gently adjusts the white lace headdress on her head, "
                  "then lowers her hand and gives a small satisfied nod.",
    },
    "turn_show": {
        "name_zh": "转身展示",
        "desc": "小幅转身展示衣服，再转回来。",
        "loop": False,
        "prompt": "She turns her body about forty degrees to one side to show off her outfit, "
                  "then turns back to face the camera, hands lightly holding her skirt.",
    },
    "search_pocket": {
        "name_zh": "摸口袋找东西",
        "desc": "在围裙口袋里翻找，掏出一个小物件。",
        "loop": False,
        "prompt": "She pats her apron pocket, rummages in it, and pulls out a tiny glowing star, "
                  "holding it up in front of her chest with a happy expression.",
    },
    "push_ball": {
        "name_zh": "推小球",
        "desc": "轻轻推出一颗小球，目光跟着球。",
        "loop": False,
        "prompt": "She bends forward slightly and pushes a small blue ball with one hand, then "
                  "watches it with her eyes following the ball to one side.",
    },
    "catch_star": {
        "name_zh": "接纸星星",
        "desc": "伸手接住落下的纸星星，捧在手里。",
        "loop": False,
        "prompt": "A small paper star falls from above, she reaches up with both hands and "
                  "catches it, then holds it close to her chest smiling.",
    },
    "headphone": {
        "name_zh": "戴耳机听歌",
        "desc": "戴上耳机，轻轻点头。",
        "loop": False,
        "prompt": "She puts on a pair of small blue headphones, closes her eyes, and gently nods "
                  "her head to the music a couple of times.",
    },
    "lantern_off": {
        "name_zh": "收起小灯",
        "desc": "放下小灯，挥手道别。",
        "loop": False,
        "prompt": "She sets down a small warm glowing lantern beside her, its light fades out, "
                  "and she waves goodbye with one hand.",
    },
    "wave_hello": {
        "name_zh": "打招呼",
        "desc": "抬手挥手打招呼。",
        "loop": False,
        "prompt": "She raises her right hand and waves hello cheerfully three times with a warm "
                  "smile.",
    },
    "nod_yes": {
        "name_zh": "点头答应",
        "desc": "认真点头。",
        "loop": False,
        "prompt": "She nods her head twice in a clear, gentle agreement, keeping eye contact "
                  "with the camera.",
    },
    "shake_head": {
        "name_zh": "摇头",
        "desc": "轻轻摇头表示不用。",
        "loop": False,
        "prompt": "She shakes her head slowly from side to side twice with a soft apologetic "
                  "expression.",
    },
    "yawn": {
        "name_zh": "打哈欠",
        "desc": "捂嘴打哈欠，眼角泛泪。",
        "loop": False,
        "prompt": "She covers her mouth with one hand and yawns widely, eyes squeezing shut, "
                  "then blinks and rubs one eye sleepily.",
    },
    "cheer_jump": {
        "name_zh": "开心跳一下",
        "desc": "开心地原地小跳。",
        "loop": False,
        "prompt": "She jumps up once in place with both arms raised in celebration, lands softly, "
                  "and smiles brightly.",
    },
    "listen_tilt": {
        "name_zh": "侧头倾听",
        "desc": "歪头认真听。",
        "loop": False,
        "prompt": "She tilts her head to one side and listens attentively, blinking once, hands "
                  "folded in front of her.",
    },
    "shy_look": {
        "name_zh": "害羞低头",
        "desc": "被夸后害羞地低头。",
        "loop": False,
        "prompt": "She looks down shyly, cheeks turning pink, and clasps her hands together in "
                  "front of her chest.",
    },
    "clap_happy": {
        "name_zh": "开心鼓掌",
        "desc": "双手轻快地拍两下。",
        "loop": False,
        "prompt": "She claps her hands together three times happily with a big smile, then holds "
                  "them together.",
    },
}


ACTIONS.update({
    "bow": {
        "name_zh": "鞠躬",
        "desc": "欠身鞠躬表示感谢。",
        "loop": False,
        "prompt": "She bows politely from the waist with her hands together, holds briefly, "
                  "then straightens back up with a gentle smile.",
    },
    "point_forward": {
        "name_zh": "指一指",
        "desc": "指向正前方某处。",
        "loop": False,
        "prompt": "She raises one arm and points forward with a straight index finger, head "
                  "following the direction, holds for a moment.",
    },
    "look_up": {
        "name_zh": "抬头看",
        "desc": "抬头看向上方，像在看屏幕。",
        "loop": False,
        "prompt": "She tilts her head up and looks toward the upper part of the frame with "
                  "curious eyes, blinks once.",
    },
    "cover_mouth": {
        "name_zh": "捂嘴惊讶",
        "desc": "吃惊地捂住嘴。",
        "loop": False,
        "prompt": "She opens her eyes wide in surprise and covers her mouth with one hand, "
                  "then lowers it slowly.",
    },
    "pat_own_chest": {
        "name_zh": "拍拍胸口",
        "desc": "拍拍胸口安慰自己。",
        "loop": False,
        "prompt": "She pats her own chest twice with one hand and takes a calm breath, as if "
                  "reassuring herself.",
    },
    "tidy_hair": {
        "name_zh": "撩头发",
        "desc": "把一侧长发顺到耳后。",
        "loop": False,
        "prompt": "She brushes one side of her long hair behind her ear with one hand, "
                  "then lets her hand fall back down.",
    },
    "sit_stretch": {
        "name_zh": "坐着伸展",
        "desc": "坐着伸懒腰，活动手臂。",
        "loop": False,
        "prompt": "She is sitting on the ground and stretches both arms up above her head, "
                  "then brings them down and rests hands in her lap.",
    },
    "kneel_look": {
        "name_zh": "跪坐张望",
        "desc": "跪坐四处看看。",
        "loop": False,
        "prompt": "She kneels on the floor and turns her head to look left then right, "
                  "curious and alert.",
    },
    "sleepy_nod": {
        "name_zh": "打瞌睡",
        "desc": "坐着点头打瞌睡。",
        "loop": False,
        "prompt": "She sits and slowly nods off, head dipping down three times, then jerks "
                  "awake with a blink.",
    },
    "happy_sway": {
        "name_zh": "开心摇摆",
        "desc": "轻轻左右摇摆。",
        "loop": False,
        "prompt": "She sways her upper body happily from side to side twice with a bright smile, "
                  "hands lightly swinging.",
    },
    "peek_left": {
        "name_zh": "从左边探头",
        "desc": "从侧边探出头看。",
        "loop": False,
        "prompt": "She leans to the left side of the frame and peeks out from the edge with "
                  "curious eyes, then returns to the center.",
    },
    "peek_right": {
        "name_zh": "从右边探头",
        "desc": "从侧边探出头看。",
        "loop": False,
        "prompt": "She leans to the right side of the frame and peeks out from the edge with "
                  "curious eyes, then returns to the center.",
    },
    "spin_cheer": {
        "name_zh": "转圈欢呼",
        "desc": "开心地转一小圈。",
        "loop": False,
        "prompt": "She turns around once in place with her arms slightly out, skirt flaring, "
                  "then faces the camera smiling.",
    },
    "drink_tea": {
        "name_zh": "喝茶",
        "desc": "端着小茶杯慢慢喝。",
        "loop": False,
        "prompt": "She holds a small teacup with both hands, blows on it gently, takes a sip, "
                  "and lowers it with a contented expression.",
    },
    "stretch_back": {
        "name_zh": "伸展腰背",
        "desc": "双手撑腰后仰伸展。",
        "loop": False,
        "prompt": "She places both hands on her lower back and leans backward in a gentle "
                  "stretch, then returns upright.",
    },
    "shrug": {
        "name_zh": "耸耸肩",
        "desc": "耸耸肩表示不知道。",
        "loop": False,
        "prompt": "She shrugs both shoulders and tilts her head with a small puzzled smile, "
                  "palms turned up.",
    },
    "salute": {
        "name_zh": "敬礼",
        "desc": "俏皮地敬个礼。",
        "loop": False,
        "prompt": "She gives a playful little salute with one hand at her temple and a wink.",
    },
    "wave_both": {
        "name_zh": "双手挥挥",
        "desc": "双手同时挥手。",
        "loop": False,
        "prompt": "She raises both hands and waves them cheerfully twice, smiling brightly.",
    },
    "hug_self": {
        "name_zh": "抱住自己",
        "desc": "抱住自己，很冷的样子。",
        "loop": False,
        "prompt": "She hugs herself with both arms, shivers slightly, then relaxes with a "
                  "sheepish smile.",
    },
    "look_down_sad": {
        "name_zh": "低头失落",
        "desc": "低头，视线下垂。",
        "loop": False,
        "prompt": "She lowers her head and looks down quietly with a small sigh, shoulders "
                  "slightly dropped.",
    },
})

ACTIONS.update({
    "think_deep": {
        "name_zh": "深思", "desc": "托腮沉思。", "loop": False,
        "prompt": "She rests her chin on one hand with a thoughtful expression, eyes moving "
                  "slightly as if considering something, then nods to herself.",
    },
    "wave_small": {
        "name_zh": "小幅摆手", "desc": "轻轻摆手说不用。", "loop": False,
        "prompt": "She raises one hand at chest height and waves it gently side to side twice, "
                  "with a soft 'no need' expression.",
    },
    "point_down": {
        "name_zh": "往下指", "desc": "指向下方某处。", "loop": False,
        "prompt": "She looks down and points downward with one index finger, then glances back "
                  "up at the camera.",
    },
    "hold_hands": {
        "name_zh": "双手交握", "desc": "双手交握在身前。", "loop": False,
        "prompt": "She clasps both hands together in front of her waist and shifts her weight "
                  "slightly, looking at the camera calmly.",
    },
    "peek_shoulder": {
        "name_zh": "回头看", "desc": "侧身回头看。", "loop": False,
        "prompt": "She turns her upper body away then looks back over her shoulder at the "
                  "camera with a small smile.",
    },
    "sit_swing": {
        "name_zh": "坐着晃腿", "desc": "坐着轻轻晃腿。", "loop": False,
        "prompt": "She sits on the ground and swings her legs gently back and forth twice, "
                  "hands resting behind her for support.",
    },
    "rub_eyes": {
        "name_zh": "揉眼睛", "desc": "刚睡醒揉眼睛。", "loop": False,
        "prompt": "She rubs both eyes with her fists like she just woke up, then blinks and "
                  "looks at the camera.",
    },
    "tilt_curious": {
        "name_zh": "好奇歪头", "desc": "好奇地歪头看。", "loop": False,
        "prompt": "She tilts her head far to one side with wide curious eyes, then tilts to the "
                  "other side slightly.",
    },
    "small_jump": {
        "name_zh": "轻跳一下", "desc": "轻轻向上跳一下。", "loop": False,
        "prompt": "She makes a small hop in place with her arms slightly raised, lands softly "
                  "with bent knees, then straightens.",
    },
    "spin_look": {
        "name_zh": "转身张望", "desc": "转身左右看看。", "loop": False,
        "prompt": "She turns around once slowly while looking left and right, then faces the "
                  "camera again.",
    },
    "wave_goodbye": {
        "name_zh": "挥手告别", "desc": "挥手说再见。", "loop": False,
        "prompt": "She waves goodbye with one hand while taking a small step back, smiling "
                  "warmly.",
    },
    "nod_strong": {
        "name_zh": "用力点头", "desc": "用力点头表示明白。", "loop": False,
        "prompt": "She nods her head firmly three times with a determined expression, fists "
                  "lightly clenched.",
    },
    "shrug_sorry": {
        "name_zh": "抱歉耸肩", "desc": "抱歉地耸肩。", "loop": False,
        "prompt": "She shrugs with both palms up and a small apologetic smile, tilting her head.",
    },
    "cover_face": {
        "name_zh": "捂脸", "desc": "不好意思地捂脸。", "loop": False,
        "prompt": "She covers her face with both hands in embarrassment, peeks through her "
                  "fingers, then lowers them.",
    },
    "lean_forward": {
        "name_zh": "凑近看", "desc": "凑近看屏幕。", "loop": False,
        "prompt": "She leans forward toward the camera with curious eyes, as if looking closely "
                  "at something, then leans back.",
    },
    "reach_out": {
        "name_zh": "伸手", "desc": "向镜头伸出手。", "loop": False,
        "prompt": "She reaches one hand forward toward the camera with an open palm and a warm "
                  "smile, holds it, then pulls back.",
    },
    "sit_hug_knees": {
        "name_zh": "抱膝坐", "desc": "抱膝坐着。", "loop": False,
        "prompt": "She sits and hugs her knees to her chest, resting her chin on them, then "
                  "looks up at the camera.",
    },
    "stretch_neck": {
        "name_zh": "转脖子", "desc": "活动一下脖子。", "loop": False,
        "prompt": "She tilts her head slowly to one side then the other, stretching her neck "
                  "gently, then returns to center.",
    },
    "clap_once": {
        "name_zh": "拍一下手", "desc": "拍一下手表示决定。", "loop": False,
        "prompt": "She claps her hands together once decisively with a bright expression, as if "
                  "saying 'let's do it'.",
    },
    "point_self": {
        "name_zh": "指自己", "desc": "指着自己。", "loop": False,
        "prompt": "She points at herself with one finger and tilts her head with a questioning "
                  "smile, as if asking 'me?'.",
    },
    "wave_both_up": {
        "name_zh": "双手高举", "desc": "双手高举欢呼。", "loop": False,
        "prompt": "She raises both arms high above her head and cheers with a big smile, then "
                  "brings them down.",
    },
})

def out_dir(action_id: str) -> Path:
    return OUT_DIR / "actions" / action_id


def done(action_id: str) -> bool:
    d = out_dir(action_id)
    return d.is_dir() and len(list(d.glob("*.webp"))) >= 10


def run_one(action_id: str, *, force: bool = False) -> list[Path]:
    spec = ACTIONS[action_id]
    if done(action_id) and not force:
        print(f"[skip] {action_id}")
        return sorted(out_dir(action_id).glob("*.webp"))

    first = IMG_DIR / f"first_{action_id}.png"
    if not first.is_file():
        compose_first_frame(BASE_REF, first)

    mp4 = VID_DIR / f"{action_id}.mp4"
    if not mp4.is_file():
        prompt = f"{spec['prompt']} {NO_CAMERA}"
        jid = submit_video(first, prompt, seed=abs(hash(action_id)) % 100000)
        print(f"[submit] {action_id} job={jid}", flush=True)
        state = wait_video(jid)
        print(f"[done] {action_id} {state.get('gen_seconds')}s", flush=True)
        download_video(jid, mp4)
    else:
        print(f"[cache] {action_id} mp4 exists", flush=True)

    frames = build_action_from_video(mp4, out_dir(action_id), step=2, loop=spec["loop"])
    print(f"[frames] {action_id} -> {len(frames)} webp", flush=True)
    return frames


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "list":
        for k, v in ACTIONS.items():
            print(f"{k:16} {v['name_zh']}")
        return 0
    cmd = argv[1]
    if cmd == "one":
        run_one(argv[2], force="--force" in argv)
        return 0
    if cmd == "queue":
        for k in ACTIONS:
            try:
                run_one(k)
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] {k}: {exc}", flush=True)
        return 0
    if cmd == "status":
        for k in ACTIONS:
            print(f"{k:16} {'OK' if done(k) else '--'}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
