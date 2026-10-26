"""麦克风和扬声器：用假的 sounddevice 模块，不碰真声卡。"""

import asyncio
import sys
import time
import types

import pytest

from linx_client.audio import IN_FRAME_SAMPLES, Microphone, PlaybackMeter, Speaker
from linx_client.identity import Identity
from linx_client.mcp import McpServer
from linx_client.session import LinxSession


class FakeStream:
    def __init__(self, **kw):
        self.kw = kw
        self.callback = kw.get("callback")
        self.written = []
        self.state = "new"

    def start(self):
        self.state = "started"

    def stop(self):
        self.state = "stopped"

    def close(self):
        self.state = "closed"

    def write(self, data):
        self.written.append(data)


@pytest.fixture
def fake_sd(monkeypatch):
    sd = types.SimpleNamespace(streams=[])
    sd.RawInputStream = lambda **kw: sd.streams.append(FakeStream(**kw)) or sd.streams[-1]
    sd.RawOutputStream = lambda **kw: sd.streams.append(FakeStream(**kw)) or sd.streams[-1]
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return sd


def test_microphone_queues_frames_and_drops_oldest(fake_sd):
    async def main():
        mic = Microphone(asyncio.get_running_loop(), device=3)
        stream = fake_sd.streams[0]
        assert stream.kw["samplerate"] == 16000 and stream.kw["blocksize"] == IN_FRAME_SAMPLES
        assert stream.kw["device"] == 3
        mic.start()
        for i in range(mic.queue.maxsize + 5):
            stream.callback(bytes([i % 256]) * 4, 2, None, None)  # 音频线程回调
        await asyncio.sleep(0.01)
        first = mic.queue.get_nowait()
        mic.stop()
        return mic, first, stream

    mic, first, stream = asyncio.run(main())
    assert first == bytes([5]) * 4  # 队列满了丢最旧的，保证低延迟
    assert stream.state == "closed"


def test_speaker_plays_records_level_and_flushes(fake_sd):
    spk = Speaker(sample_rate=16000)
    stream = fake_sd.streams[0]
    spk.start()
    loud = (8000).to_bytes(2, "little", signed=True) * 320
    spk.feed(loud)
    deadline = time.monotonic() + 2
    while not stream.written and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stream.written == [loud]
    assert spk.meter.level() > 1000  # 刚播出去的声音算作回声来源

    spk._q.put(b"x")
    spk._q.put(b"y")
    spk.flush()  # 打断：没播的全扔掉
    assert not spk.busy()
    spk.stop()
    assert stream.state == "closed"


def test_playback_meter_forgets_old_audio(monkeypatch):
    meter = PlaybackMeter()
    meter.record((4000).to_bytes(2, "little", signed=True) * 10)
    assert meter.level() > 0
    monkeypatch.setattr(PlaybackMeter, "WINDOW_S", -1)  # 窗口外的声音不算
    assert meter.level() == 0


def test_opus_without_opuslib_explains_how_to_fix(fake_sd, monkeypatch):
    monkeypatch.setitem(sys.modules, "opuslib", None)
    with pytest.raises(RuntimeError, match="opuslib"):
        Speaker(sample_rate=24000, fmt="opus")


def test_session_starts_and_stops_audio_devices(fake_sd):
    async def main():
        s = LinxSession(Identity("02:00:00:00:00:01", "cid"), McpServer(), lambda: ("", ""), mic_device=1)
        s._start_audio({"sample_rate": 24000, "format": "pcm", "frame_duration": 60})
        mic_stream, spk_stream = fake_sd.streams
        assert mic_stream.state == spk_stream.state == "started"
        assert spk_stream.kw["samplerate"] == 24000  # 按服务端 hello 的下行格式播放
        s._stop_audio()
        return mic_stream, spk_stream, s

    mic_stream, spk_stream, s = asyncio.run(main())
    assert mic_stream.state == spk_stream.state == "closed"
    assert s._mic is None and s._speaker is None
