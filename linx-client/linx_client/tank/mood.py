"""情绪肢体语言：平台每句回复带的 emotion（llm 消息）→ 小车的小动作 + 车灯颜色。

情绪名沿用小智协议的表情集合。限流规则：两次反应至少间隔 COOLDOWN_S；同一种情绪
REPEAT_S 内不重复（大模型常常每句都标同一个情绪，比如 surprised），否则车会一直抽动。
车正在执行指令时 executor.emote 会自己跳过，只换灯。
"""

import asyncio
import time

from .executor import TankExecutor

GROUPS = {
    "happy": {"happy", "laughing", "funny", "loving", "winking", "cool", "confident", "relaxed",
              "delicious", "kissy", "silly"},
    "angry": {"angry"},
    "sad": {"sad", "crying", "sleepy"},
    "surprised": {"surprised", "shocked"},
    "thinking": {"thinking", "confused", "embarrassed"},
}
GROUP_OF = {e: g for g, names in GROUPS.items() for e in names}
COLORS = {  # 亮度压低，NeoPixel 全亮很刺眼
    "happy": (0, 40, 0),
    "angry": (60, 0, 0),
    "sad": (0, 8, 40),
    "surprised": (40, 30, 0),
    "thinking": (25, 0, 40),
    "neutral": (0, 0, 20),  # 与就绪时的蓝灯一致
}
COOLDOWN_S = 4.0
REPEAT_S = 20.0
_now = time.monotonic  # 单独引用，测试时只替换这里，不影响事件循环的时钟


class Mood:
    def __init__(self, tank: TankExecutor):
        self.tank = tank
        self._last_group: str | None = None
        self._last_t = -1e9

    def on_emotion(self, emotion: str | None) -> str | None:
        """返回本次触发的情绪组（用于日志和测试），没有反应时返回 None。"""
        group = "neutral" if emotion == "neutral" else GROUP_OF.get(emotion or "")
        if group is None:
            return None
        now = _now()
        if now - self._last_t < COOLDOWN_S:
            return None
        if group == self._last_group and now - self._last_t < REPEAT_S:
            return None
        self._last_group, self._last_t = group, now
        asyncio.create_task(self.tank.led(*COLORS[group]))
        if group != "neutral":
            self.tank.emote(group)
        return group
