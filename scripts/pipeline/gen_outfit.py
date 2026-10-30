"""预设造型定义与批量生成 CLI。

用法：
    python gen_outfit.py list
    python gen_outfit.py one knit_home
    python gen_outfit.py all

产物：/tmp/mimi_work/img/preset_<id>.png（透明背景，角色一致）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mimi_gen import IMG_DIR, compose_first_frame, edit_image  # noqa: E402

BASE_REF = Path("/tmp/mimivid/first_idle.png")
MASTER = Path(
    "/Volumes/isci肥牛/zhuoc/dsh-plugin-mimi/pet/assets/characters/mimi/library_v1"
    "/usable/live_rig_v5/source/master_action_style_v1.png"
)

KEEP = (
    "Keep her face, eyes, eye color, hairstyle, hair color, ahoge, ear fins, "
    "body proportions, head-to-body ratio, line weight and cel-shading style "
    "EXACTLY identical to the reference. Same neutral expression, same front-facing "
    "standing pose, arms relaxed at her sides. Full body, centered, isolated on a "
    "transparent background, no shadow, no text, no watermark."
)

PRESETS: dict[str, dict] = {
    "knit_home": {
        "name_zh": "居家针织",
        "desc": "奶油色粗针织开衫配淡蓝吊带，格纹短裙与毛绒拖鞋。",
        "prompt": "Change only her outfit into a cozy home roomwear set: soft cream chunky "
                  "knit cardigan over a pale blue camisole, light blue plaid pleated skirt, "
                  "fluffy white slippers with tiny blue bows.",
    },
    "school_uniform": {
        "name_zh": "学院制服",
        "desc": "藏青水手领外套、蓝白格纹百褶裙、白色过膝袜与乐福鞋。",
        "prompt": "Change only her outfit into a neat school uniform: navy sailor-collar "
                  "jacket with white trim, blue and white plaid pleated skirt, white knee "
                  "socks, brown loafers, a small blue ribbon at the collar.",
    },
    "winter_scarf": {
        "name_zh": "冬日围巾",
        "desc": "米白毛呢大衣、厚围巾、毛线手套与短靴。",
        "prompt": "Change only her outfit into a warm winter set: cream wool duffle coat with "
                  "wooden toggles, thick knitted scarf in soft blue, knitted mittens, dark "
                  "brown ankle boots, small knitted beanie sitting behind her ahoge.",
    },
    "rain_coat": {
        "name_zh": "雨天雨衣",
        "desc": "半透明浅蓝雨衣、黄色小雨靴，兜帽放下。",
        "prompt": "Change only her outfit into a rainy-day set: pale blue translucent raincoat "
                  "with a hood resting down on her shoulders, matching short rain skirt, "
                  "bright yellow rain boots.",
    },
    "pajama": {
        "name_zh": "晚安睡衣",
        "desc": "宽松蓝白条纹睡衣、睡帽与软底拖鞋。",
        "prompt": "Change only her outfit into bedtime pajamas: loose blue and white striped "
                  "pajama shirt and shorts, soft nightcap tilted on one side, fluffy slippers.",
    },
    "summer_dress": {
        "name_zh": "夏日连衣裙",
        "desc": "白色棉布吊带连衣裙、草编小帽与凉鞋。",
        "prompt": "Change only her outfit into a light summer set: white cotton sundress with "
                  "thin straps and small blue ribbon at the waist, straw sun hat with a blue "
                  "band, simple sandals.",
    },
    "festive_red": {
        "name_zh": "节日红裙",
        "desc": "酒红丝绒连衣裙、白色蕾丝领、金色小饰品。",
        "prompt": "Change only her outfit into a festive set: deep wine-red velvet dress with "
                  "white lace collar and cuffs, gold ribbon at the waist, small gold hair pin, "
                  "dark mary jane shoes.",
    },
    "sporty": {
        "name_zh": "运动休闲",
        "desc": "浅蓝运动外套、短裤、运动鞋与发带。",
        "prompt": "Change only her outfit into a sporty casual set: pale blue zip-up track "
                  "jacket over a white tee, navy shorts, white sneakers with blue accents, "
                  "blue sports headband.",
    },
    "maid_blue": {
        "name_zh": "深海女仆",
        "desc": "深蓝长裙女仆装、白色荷叶边围裙、鲸鱼刺绣。",
        "prompt": "Change only her outfit into an elegant deep-sea maid dress: navy long dress "
                  "with white ruffled apron embroidered with a tiny whale, white lace headdress, "
                  "small blue bows at the cuffs.",
    },
    "star_night": {
        "name_zh": "星夜披风",
        "desc": "深蓝星空披风、银线刺绣与星星发饰。",
        "prompt": "Change only her outfit into a starry-night cape set: deep navy capelet "
                  "embroidered with tiny silver stars, matching short dress, silver star hair "
                  "clips, dark boots.",
    },
}


def out_path(preset_id: str) -> Path:
    return IMG_DIR / f"preset_{preset_id}.png"


def generate(preset_id: str) -> Path:
    spec = PRESETS[preset_id]
    dest = out_path(preset_id)
    if dest.is_file() and dest.stat().st_size > 10_000:
        print(f"[skip] {preset_id} exists")
        return dest
    prompt = f"{spec['prompt']} {KEEP}"
    ref = BASE_REF if BASE_REF.is_file() else MASTER
    got = edit_image(ref, prompt, out=dest)
    print(f"[ok] {preset_id} -> {got}")
    return got


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "list":
        for k, v in PRESETS.items():
            print(f"{k:16} {v['name_zh']}  {v['desc']}")
        return 0
    cmd = argv[1]
    if cmd == "one":
        generate(argv[2])
        return 0
    if cmd == "all":
        for k in PRESETS:
            try:
                generate(k)
            except Exception as exc:  # noqa: BLE001 - 单套失败不拖垮整批
                print(f"[fail] {k}: {exc}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
