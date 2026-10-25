"""全双工防回声：外放的 TTS 漏进麦克风不算插话，也不会传给平台；真有人说话才打断并补传原声。"""

import array
import asyncio

from linx_client.audio import PlaybackMeter
from linx_client.echo import LEARN_FRAMES, BargeInDetector, rms
from linx_client.mcp import McpServer
from linx_client.session import LinxSession


def _tone(level: int, n: int = 320) -> bytes:
    return array.array("h", [level if i % 2 else -level for i in range(n)]).tobytes()


def test_rms():
    assert round(rms(_tone(1000))) == 1000 and rms(b"") == 0


def test_echo_does_not_barge_in_but_voice_does():
    d = BargeInDetector(level=500)
    # 外放音量 8000，麦克风里的回声约 0.5 倍，有起伏
    for i in range(200):
        assert not d.feed(4000 * (1 + 0.3 * (i % 3 - 1)), 8000)
    assert 0.3 < d.echo_gain < 0.7
    # 有人凑近说话：麦克风音量远超预期回声，连续几帧后确认插话
    hits = [d.feed(20000, 8000) for _ in range(6)]
    assert hits[-1] and not any(hits[:-1])


def test_no_barge_in_before_echo_gain_learned():
    d = BargeInDetector(level=500)
    assert not any(d.feed(30000, 8000) for _ in range(LEARN_FRAMES - 1))


def test_silence_between_sentences_uses_floor_level():
    d = BargeInDetector(level=2000)
    assert d.threshold(0) == 2000
    assert not any(d.feed(1500, 0) for _ in range(20))  # 句间停顿、环境噪音
    assert any(d.feed(5000, 0) for _ in range(6))  # 停顿时插话


def test_playback_meter_window():
    m = PlaybackMeter()
    assert m.level() == 0
    m.record(_tone(3000))
    assert round(m.level()) == 3000
    m._played[0] = (m._played[0][0] - 1, 3000)  # 一秒前的播放不再算
    assert m.level() == 0


class _FakeSpeaker:
    def __init__(self, level):
        self.meter = type("M", (), {"level": lambda s: level})()

    def busy(self):
        return True

    def flush(self):
        pass


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


def test_duplex_uploads_silence_while_talking_then_preroll_on_barge_in():
    async def main():
        s = LinxSession(None, McpServer(), lambda: ("", ""), audio=False, duplex=True, barge_in_level=500)
        s.ws, s.speaking, s._speaker = _FakeWs(), True, _FakeSpeaker(8000)
        mic = asyncio.Queue()
        s._mic = type("Mic", (), {"queue": mic})()
        echo, voice = _tone(4000), _tone(25000)
        for _ in range(60):
            mic.put_nowait(echo)
        for _ in range(6):
            mic.put_nowait(voice)
        pump = asyncio.create_task(s._pump_mic())
        while not mic.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        pump.cancel()
        audio = [f for f in s.ws.sent if isinstance(f, bytes)]
        assert all(f == bytes(len(echo)) for f in audio[:60])  # 回声一帧都没传上去
        assert not s.speaking and any(isinstance(f, str) and '"abort"' in f for f in s.ws.sent)
        assert audio[60:].count(voice) == 6  # 插话的原声补传了，开头的字不丢

    asyncio.run(main())


def test_half_duplex_drops_mic_during_playback_tail():
    async def main():
        s = LinxSession(None, McpServer(), lambda: ("", ""), audio=False)
        sp = _FakeSpeaker(3000)
        sp.busy = lambda: False  # 平台已发 tts stop、队列也播空了，但声卡里还在响
        s.ws, s.speaking, s._speaker = _FakeWs(), False, sp
        mic = asyncio.Queue()
        s._mic = type("Mic", (), {"queue": mic})()
        mic.put_nowait(_tone(4000))
        pump = asyncio.create_task(s._pump_mic())
        await asyncio.sleep(0.02)
        pump.cancel()
        assert not s.ws.sent

    asyncio.run(main())
