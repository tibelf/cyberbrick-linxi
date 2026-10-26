"""会话的事件转发、断线重连、麦克风上传规则（半双工 / 静音）。用本地假平台和假声卡。"""

import asyncio
import json

from websockets.asyncio.server import serve

from linx_client.identity import Identity
from linx_client.mcp import McpServer
from linx_client.session import LinxSession

IDENT = Identity(mac="02:00:00:00:00:01", client_id="cid")


def _session(port, **kw):
    return LinxSession(IDENT, McpServer(), lambda: (f"ws://127.0.0.1:{port}/v1/ws/", "tok"), audio=False, **kw)


async def _hello(ws):
    await ws.recv()  # 设备的 hello
    await ws.send(json.dumps({"type": "hello", "session_id": "s1", "audio_params": {"format": "pcm"}}))
    return json.loads(await ws.recv())  # listen start


def test_platform_messages_become_events_and_manual_mode_relistens():
    async def main():
        listens = []

        async def platform(ws):
            listens.append(await _hello(ws))
            for msg in [{"type": "stt", "text": "往前走"},
                        {"type": "llm", "text": "😊", "emotion": "happy"},
                        {"type": "tts", "state": "start"},
                        {"type": "tts", "state": "sentence_start", "text": "好嘞"},
                        {"type": "tts", "state": "stop"},
                        {"type": "goodbye"}]:
                await ws.send(json.dumps(msg))
            await ws.send("不是 JSON")  # 忽略，不断线
            listens.append(json.loads(await ws.recv()))  # 手动模式：播完重新开始听
            await ws.send(json.dumps({"type": "notify", "event": "config_updated"}))  # 设备应主动断开重连
            await ws.wait_closed()

        events = []
        async with serve(platform, "127.0.0.1", 0) as server:
            s = _session(server.sockets[0].getsockname()[1], listen_mode="manual", on_event=events.append)
            await asyncio.wait_for(s._run_once(), 5)

        kinds = [(e["type"], e.get("text") or e.get("emotion") or e.get("on") or e.get("state")) for e in events]
        assert ("user", "往前走") in kinds
        assert ("emotion", "😊") in kinds
        assert ("bot", "好嘞") in kinds
        assert [e["on"] for e in events if e["type"] == "speaking"] == [True, False]
        assert kinds[-1] == ("conn", "disconnected")
        assert listens == [{"session_id": "s1", "type": "listen", "state": "start", "mode": "manual"}] * 2
        assert s.ws is None and not s.speaking

    asyncio.run(main())


def test_run_forever_retries_and_reconnects_until_closed():
    async def main():
        conns = []
        second = asyncio.Event()

        async def platform(ws):
            conns.append(ws)
            await _hello(ws)
            if len(conns) == 1:
                await ws.close()  # 第一次连上就被平台断开
            else:
                second.set()
                await ws.wait_closed()

        async with serve(platform, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            calls = []

            def endpoint():
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("OTA 暂时不可用")  # 第一次取地址失败：等 1 秒重试
                return f"ws://127.0.0.1:{port}/v1/ws/", "tok"

            s = LinxSession(IDENT, McpServer(), endpoint, audio=False)
            runner = asyncio.create_task(s.run_forever())
            await asyncio.wait_for(second.wait(), 10)
            await s.close()
            await asyncio.wait_for(runner, 5)
        assert len(calls) == 3 and len(conns) == 2

    asyncio.run(main())


def test_say_and_send_text_use_listen_detect():
    async def main():
        got = []

        async def platform(ws):
            await _hello(ws)
            got.append(json.loads(await ws.recv()))
            got.append(json.loads(await ws.recv()))
            await ws.close()

        async with serve(platform, "127.0.0.1", 0) as server:
            s = _session(server.sockets[0].getsockname()[1])
            run = asyncio.create_task(s._run_once())
            while s.session_id is None:
                await asyncio.sleep(0.01)
            await s.send_text("开一炮")
            await s.say("已经到瓶子旁边了")
            await asyncio.wait_for(run, 5)
        assert got[0] == {"session_id": "s1", "type": "listen", "state": "detect", "text": "开一炮"}
        assert got[1]["text_response"] == "【小车播报】已经到瓶子旁边了"

    asyncio.run(main())


class FakeMeter:
    def __init__(self):
        self.value = 0

    def level(self):
        return self.value


class FakeSpeaker:
    def __init__(self):
        self.meter = FakeMeter()

    def busy(self):
        return False


class FakeMic:
    def __init__(self):
        self.queue = asyncio.Queue()


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


def test_pump_mic_sends_expected_frames():
    async def main():
        s = LinxSession(IDENT, McpServer(), lambda: ("", ""), audio=False)
        ws = s.ws = FakeWs()
        s._mic, s._speaker = FakeMic(), FakeSpeaker()
        pump = asyncio.create_task(s._pump_mic())
        cases = [
            (b"\x01\x02", False, True, 0),    # 平时原样上传
            (b"\x03\x04", False, False, 0),   # 网页按住说话没按：传静音，平台才能判断一句话说完
            (b"\x05\x06", True, True, 0),     # 半双工：小车说话时不上传
            (b"\x07\x08", False, True, 100),  # 声卡还有余音，也算在说话
        ]
        for frame, speaking, enabled, echo in cases:
            s.speaking, s.mic_enabled, s._speaker.meter.value = speaking, enabled, echo
            await s._mic.queue.put(frame)
            await asyncio.sleep(0.01)
        s.ws = None
        await s._mic.queue.put(b"\x09\x0a")  # 断线时丢弃
        await asyncio.sleep(0.01)
        pump.cancel()
        return ws.sent

    assert asyncio.run(main()) == [b"\x01\x02", b"\x00\x00"]
