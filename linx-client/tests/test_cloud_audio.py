"""假灵矽平台验证浏览器音频适配层，不访问硬件和真实平台。"""

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from linx_client.cloud_audio import BrowserLinxSession, BrowserMicrophone, MIC_QUEUE_FRAMES, PCM_FRAME_BYTES
from linx_client.identity import Identity
from linx_client.mcp import McpServer


def test_audio_through_fake_linx_platform():
    async def run():
        seen = []
        output = []
        formats = []
        flushed = []
        first_received = asyncio.Event()
        frame = bytes([1, 2]) * (PCM_FRAME_BYTES // 2)
        reply = bytes([3, 4]) * (PCM_FRAME_BYTES // 2)

        async def platform(ws):
            hello = json.loads(await ws.recv())
            assert hello["audio_params"]["sample_rate"] == 16000
            await ws.send(json.dumps({"type": "hello", "session_id": "s1",
                                      "audio_params": {"format": "pcm", "sample_rate": 16000}}))
            assert json.loads(await ws.recv())["type"] == "listen"
            seen.append(await ws.recv())
            first_received.set()
            seen.append(await ws.recv())
            await ws.send(json.dumps({"type": "tts", "state": "start"}))
            await ws.send(reply)
            await ws.send(json.dumps({"type": "tts", "state": "stop"}))
            await asyncio.sleep(0.05)

        async with serve(platform, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            session = BrowserLinxSession(Identity("02:00:00:00:00:01", "cid"), McpServer(),
                                         lambda: (f"ws://127.0.0.1:{port}/", "fake"),
                                         on_pcm=output.append, on_format=formats.append,
                                         on_flush=lambda: flushed.append(True))
            task = asyncio.create_task(session._run_once())
            async with asyncio.timeout(3):
                while session._mic is None:
                    await asyncio.sleep(0.01)
            session.feed_microphone(frame)  # 未按住说话时必须是静音
            await asyncio.wait_for(first_received.wait(), 3)
            session.mic_enabled = True
            session.feed_microphone(frame)
            await asyncio.wait_for(task, 3)

        assert seen == [bytes(PCM_FRAME_BYTES), frame]
        assert output == [reply]
        assert formats == [{"format": "pcm", "sample_rate": 16000, "channels": 1, "bit_depth": 16}]
        assert flushed

    asyncio.run(run())


def test_invalid_format_and_stale_input_are_rejected_or_dropped():
    async def run():
        mic = BrowserMicrophone()
        with pytest.raises(ValueError):
            mic.feed(b"bad")
        for value in range(MIC_QUEUE_FRAMES + 1):
            mic.feed(bytes([value]) * PCM_FRAME_BYTES)
        assert mic.queue.qsize() == MIC_QUEUE_FRAMES
        assert (await mic.queue.get())[0] == 1  # 满了会丢最旧的，不会积压旧指令
        mic.stop()
        assert mic.queue.empty()

        session = BrowserLinxSession(Identity("02:00:00:00:00:01", "cid"), McpServer(), lambda: ("", ""),
                                     on_pcm=lambda _: None, on_format=lambda _: None, on_flush=lambda: None)
        with pytest.raises(RuntimeError):
            session._start_audio({"format": "opus"})
        with pytest.raises(RuntimeError):
            session.feed_microphone(bytes(PCM_FRAME_BYTES))

    asyncio.run(run())
