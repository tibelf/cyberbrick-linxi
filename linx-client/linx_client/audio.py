"""麦克风采集与扬声器播放（sounddevice）。上行固定 PCM 16kHz/16bit/单声道/20ms 帧。

服务端下行格式以 hello 响应里的 audio_params 为准：pcm 直接播放，opus 需要安装 opuslib + libopus。
"""

import asyncio
import queue
import threading
import time
from collections import deque

IN_RATE = 16000
IN_FRAME_MS = 20
IN_FRAME_SAMPLES = IN_RATE * IN_FRAME_MS // 1000  # 320


class Microphone:
    def __init__(self, loop: asyncio.AbstractEventLoop, device=None):
        import sounddevice as sd

        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._loop = loop

        def cb(indata, frames, time_info, status):  # 在音频线程里
            data = bytes(indata)
            loop.call_soon_threadsafe(self._put, data)

        self._stream = sd.RawInputStream(
            samplerate=IN_RATE,
            blocksize=IN_FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=device,
            callback=cb,
        )

    def _put(self, data: bytes):
        if self.queue.full():
            self.queue.get_nowait()  # 丢最旧的帧，保证低延迟
        self.queue.put_nowait(data)

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()


class PlaybackMeter:
    """记录最近播放出去的音量，给插话检测估算“此刻麦克风里大概有多少回声”。"""

    WINDOW_S = 0.4  # 声卡输出有延迟、房间有混响，取最近 0.4 秒的最大值

    def __init__(self):
        self._played: deque[tuple[float, float]] = deque(maxlen=64)  # (写出时间, RMS)

    def record(self, data: bytes):
        from .echo import rms

        self._played.append((time.monotonic(), rms(data)))

    def level(self) -> float:
        cutoff = time.monotonic() - self.WINDOW_S
        return max((r for t, r in list(self._played) if t >= cutoff), default=0.0)


class Speaker:
    def __init__(self, sample_rate: int, fmt: str = "pcm", frame_ms: int = 60, device=None):
        import sounddevice as sd

        self.sample_rate = sample_rate
        self._decoder = None
        if fmt == "opus":
            try:
                import opuslib
            except ImportError as e:
                raise RuntimeError("服务端下发 opus 音频，请 `pip install opuslib` 并安装 libopus") from e
            self._decoder = opuslib.Decoder(sample_rate, 1)
            self._frame_samples = sample_rate * frame_ms // 1000
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self.meter = PlaybackMeter()
        self._stream = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16", device=device)
        self._thread = threading.Thread(target=self._play, daemon=True)

    def start(self):
        self._stream.start()
        self._thread.start()

    def feed(self, data: bytes):
        if self._decoder:
            data = self._decoder.decode(data, self._frame_samples)
        self._q.put(data)

    def busy(self) -> bool:
        return not self._q.empty()

    def flush(self):
        """打断时清空未播放的缓冲。"""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _play(self):
        while True:
            data = self._q.get()
            if data is None:
                return
            self.meter.record(data)
            self._stream.write(data)

    def stop(self):
        self._q.put(None)
        self._stream.stop()
        self._stream.close()
