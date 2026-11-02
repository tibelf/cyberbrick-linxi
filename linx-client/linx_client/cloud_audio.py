"""浏览器音频与灵矽会话的适配层（尚未接入公网模拟站点）。"""

import asyncio
import time
from collections.abc import Callable

from .audio import IN_FRAME_SAMPLES, IN_RATE
from .session import LinxSession

PCM_FRAME_BYTES = IN_FRAME_SAMPLES * 2  # 16 kHz、单声道、16 位、20 ms
MIC_QUEUE_FRAMES = 25  # 积压时丢弃最旧音频，避免语音指令延迟后才执行
PLAYBACK_TAIL_S = 0.4


class BrowserMicrophone:
    def __init__(self):
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MIC_QUEUE_FRAMES)
        self.active = True

    def feed(self, frame: bytes):
        if not isinstance(frame, bytes) or len(frame) != PCM_FRAME_BYTES:
            raise ValueError("麦克风音频必须是 16kHz 单声道 20ms PCM 帧")
        if not self.active:
            return
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(frame)

    def stop(self):
        self.active = False
        while not self.queue.empty():
            self.queue.get_nowait()


class _SilentMeter:
    def level(self):
        return 0


class BrowserSpeaker:
    """将 PCM 交给网页播放，估算播放尾音以保留半双工保护。"""

    def __init__(self, rate: int, on_pcm: Callable[[bytes], None], on_flush: Callable[[], None]):
        self.rate = rate
        self.on_pcm = on_pcm
        self.on_flush = on_flush
        self.meter = _SilentMeter()
        self._playback_end = 0.0
        self.active = True

    def feed(self, data: bytes):
        if not self.active:
            return
        if not data or len(data) % 2:
            raise RuntimeError("灵矽下行 PCM 音频格式错误")
        now = time.monotonic()
        self._playback_end = max(now, self._playback_end) + len(data) / (2 * self.rate)
        self.on_pcm(data)

    def busy(self) -> bool:
        return bool(self._playback_end) and time.monotonic() < self._playback_end + PLAYBACK_TAIL_S

    def flush(self):
        self._playback_end = 0.0
        self.on_flush()

    def stop(self):
        self.flush()
        self.active = False


class BrowserLinxSession(LinxSession):
    """复用会话协议；浏览器负责收音和播放，服务器不需要声卡。"""

    def __init__(self, *args, on_pcm: Callable[[bytes], None], on_format: Callable[[dict], None],
                 on_flush: Callable[[], None], **kwargs):
        if kwargs.get("duplex"):
            raise ValueError("浏览器音频原型仅支持半双工")
        kwargs["audio"] = True
        kwargs["duplex"] = False
        super().__init__(*args, **kwargs)
        self.mic_enabled = False
        self._on_pcm = on_pcm
        self._on_format = on_format
        self._on_flush = on_flush

    def _start_audio(self, params: dict):
        if (params.get("format", "pcm") != "pcm" or int(params.get("channels", 1)) != 1
                or int(params.get("bit_depth", 16)) != 16 or params.get("endianness", "little") != "little"):
            raise RuntimeError("浏览器原型目前仅支持单声道 16 位小端 PCM 下行音频")
        rate = int(params.get("sample_rate", IN_RATE))
        if not 8000 <= rate <= 48000:
            raise RuntimeError("灵矽下行音频采样率不受支持")
        self._mic = BrowserMicrophone()
        self._speaker = BrowserSpeaker(rate, self._on_pcm, self._on_flush)
        self._on_format({"format": "pcm", "sample_rate": rate, "channels": 1, "bit_depth": 16})

    def feed_microphone(self, frame: bytes):
        if self._mic is None:
            raise RuntimeError("灵矽音频尚未连接")
        self._mic.feed(frame)
