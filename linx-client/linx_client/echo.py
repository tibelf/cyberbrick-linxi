"""全双工（外放、不戴耳机）时防止小车把自己的声音当成用户输入。

做法（不依赖回声消除库）：
1. 小车说话期间**不把麦克风原声传给平台**，只传静音，平台的语音识别就听不到外放的 TTS。
2. 本地判断“是不是有人插话”：扬声器漏进麦克风的回声，音量大致和正在播放的音量成正比。
   持续统计「麦克风音量 / 播放音量」的比值（取最近约 2 秒的中位数，即回声增益），
   只有麦克风音量明显超过“预期回声”（ECHO_MARGIN 倍）并持续 BARGE_IN_FRAMES 帧，才算插话。
3. 确认插话后打断小车，并把判定插话的那几帧原声补传给平台，用户开头的字不会丢。
"""

import array
import math
import statistics
from collections import deque

BARGE_IN_FRAMES = 6  # 连续 6 帧（120ms）够响才算插话，避免咳嗽、碰桌子误触发
ECHO_MARGIN = 3.0  # 麦克风音量要超过预期回声这么多倍（约 10dB）才算有人说话
MIN_PLAY_RMS = 200  # 播放音量低于这个当作没在出声（句间停顿）
LEARN_FRAMES = 25  # 至少统计这么多帧（0.5 秒）回声增益之后才开始判断插话
PREROLL_FRAMES = BARGE_IN_FRAMES + 2  # 确认插话时补传最近 160ms 的原声（判定插话的那几帧 + 一点余量）


def rms(frame: bytes) -> float:
    samples = array.array("h", frame)
    return math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0.0


class BargeInDetector:
    def __init__(self, level: float, margin: float = ECHO_MARGIN, frames: int = BARGE_IN_FRAMES):
        self.level = level  # 绝对下限（int16 RMS）：再安静的环境，小于这个也不算插话
        self.margin = margin
        self.frames = frames
        self._ratios: deque[float] = deque(maxlen=100)  # 最近 2 秒的 麦克风/播放 音量比
        self._loud = 0

    def reset(self):
        self._loud = 0

    @property
    def echo_gain(self) -> float | None:
        return statistics.median(self._ratios) if len(self._ratios) >= LEARN_FRAMES else None

    def threshold(self, play_rms: float) -> float:
        if play_rms < MIN_PLAY_RMS:
            return self.level
        gain = self.echo_gain
        if gain is None:
            return math.inf  # 还没学会回声有多大，先不判断（只影响第一次说话的头半秒）
        return max(self.level, self.margin * gain * play_rms)

    def feed(self, mic_rms: float, play_rms: float) -> bool:
        """喂一帧麦克风音量和此刻的播放音量，返回是否确认有人插话。"""
        loud = mic_rms > self.threshold(play_rms)
        if play_rms >= MIN_PLAY_RMS:
            self._ratios.append(mic_rms / play_rms)  # 中位数抗干扰：插话的那几帧带不偏它
        self._loud = self._loud + 1 if loud else 0
        if self._loud >= self.frames:
            self._loud = 0
            return True
        return False
