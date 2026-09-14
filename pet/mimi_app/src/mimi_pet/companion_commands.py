"""Small, explicit local-command grammar; unrelated chat is never intercepted."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class CompanionCommand:
    kind: str
    minutes: int = 0
    text: str = ""
    due_at: float = 0.0
    enabled: bool = False


def _number(value: str) -> int:
    if value.isascii() and value.isdecimal():
        return int(value)
    digits = dict(zip("零一二三四五六七八九两", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2)))
    total = digit = 0
    for char in value:
        if char in digits:
            digit = digits[char]
        elif char in ("十", "百"):
            total += (digit or 1) * (10 if char == "十" else 100)
            digit = 0
        else:
            raise ValueError("请用数字填写时长，例如 25 分钟。")
    return total + digit


NUMBER = r"[0-9零一二三四五六七八九十百两]{1,6}"


def parse_command(text: str, *, now: float) -> CompanionCommand | None:
    value = unicodedata.normalize("NFKC", text).strip().rstrip("。！! ")
    simple = {
        "专注": "focus_start", "开始专注": "focus_start",
        "暂停专注": "focus_pause", "暂停计时": "focus_pause",
        "继续专注": "focus_resume", "继续计时": "focus_resume",
        "取消专注": "focus_cancel", "结束专注": "focus_cancel",
        "取消休息": "focus_cancel", "结束休息": "focus_cancel",
        "专注状态": "status", "还有多久": "status",
        "查看提醒": "show", "提醒列表": "show",
        "查看通知": "show", "最近通知": "show",
        "休息一下": "break_start",
    }
    if value in simple:
        kind = simple[value]
        return CompanionCommand(kind, minutes=5 if kind == "break_start" else 25)
    if value in ("开启勿扰", "打开勿扰", "勿扰模式", "勿扰"):
        return CompanionCommand("quiet", enabled=True)
    if value in ("关闭勿扰", "取消勿扰"):
        return CompanionCommand("quiet", enabled=False)

    match = re.fullmatch(
        rf"(?:陪我|开始)?\s*(专注|休息)\s*({NUMBER})\s*(分钟|分|小时)", value,
    )
    if match:
        minutes = _number(match[2]) * (60 if match[3] == "小时" else 1)
        return CompanionCommand("focus_start" if match[1] == "专注" else "break_start", minutes)

    match = re.fullmatch(rf"({NUMBER}|半)\s*(分钟|小时|天)\s*后提醒我\s*(.+)", value)
    if match:
        amount = 0.5 if match[1] == "半" else _number(match[1])
        seconds = amount * {"分钟": 60, "小时": 3600, "天": 86400}[match[2]]
        if not 60 <= seconds <= 366 * 86400:
            return CompanionCommand("error", text="提醒时间请设在 1 分钟至 366 天以内。")
        return CompanionCommand("reminder_add", text=match[3].strip(), due_at=now + seconds)

    match = re.fullmatch(
        r"(今天|明天|后天|\d{4}-\d{1,2}-\d{1,2})?\s*(\d{1,2}):(\d{2})\s*提醒我\s*(.+)",
        value,
    )
    if match:
        try:
            local_now = datetime.fromtimestamp(now)
            day_text = match[1]
            if day_text and day_text not in ("今天", "明天", "后天"):
                day = datetime.strptime(day_text, "%Y-%m-%d").date()
            else:
                day = (local_now + timedelta(days={None: 0, "今天": 0, "明天": 1, "后天": 2}[day_text])).date()
            due = datetime.combine(day, datetime.min.time()).replace(
                hour=int(match[2]), minute=int(match[3]),
            )
            if day_text is None and due <= local_now:
                due += timedelta(days=1)
            if due.timestamp() <= now:
                return CompanionCommand("error", text="这个时间已经过去啦，请填写将来的日期和时间。")
            return CompanionCommand("reminder_add", text=match[4].strip(), due_at=due.timestamp())
        except ValueError:
            return CompanionCommand("error", text="日期或时间不正确，例如：明天 18:00 提醒我下班。")

    match = re.fullmatch(r"取消提醒\s+(.+)", value)
    if match:
        return CompanionCommand("reminder_cancel", text=match[1].strip())
    # Only unmistakable local requests get a clarification. An ordinary
    # conversation about reminders/focus still belongs to the chat session.
    if re.match(r"^(?:陪我|开始)?(?:专注|休息)\s*[-\d零一二三四五六七八九十百两半]", value):
        return CompanionCommand("error", text="请填写完整时长，例如：陪我专注 25 分钟（1–240 分钟）。")
    if (
        value.startswith("提醒我")
        or re.match(r"^(?:今天|明天|后天|下午|上午|晚上|早上|周[一二三四五六日天]).*提醒我", value)
        or re.match(rf"^(?:{NUMBER}|半).*(?:后提醒我)", value)
    ):
        return CompanionCommand(
            "error", text="请补充明确时间和事项，例如：20 分钟后提醒我喝水，或明天 18:00 提醒我下班。",
        )
    return None
