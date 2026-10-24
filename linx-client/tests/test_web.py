import asyncio
import json
import socket
import urllib.request

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from linx_client.tank import MockBackend, TankExecutor
from linx_client.web import WebConsole


class FakeSession:
    def __init__(self):
        self.mic_enabled = True
        self.sent: list[str] = []

    async def send_text(self, text):
        self.sent.append(text)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _recv_type(ws, t):
    while True:
        e = json.loads(await asyncio.wait_for(ws.recv(), 2))
        if e["type"] == t:
            return e


def test_web_console():
    async def run():
        backend = MockBackend(verbose=False)
        tank = TankExecutor(backend, max_speed=40)
        await tank.start()
        session = FakeSession()
        port = _free_port()
        console = WebConsole(tank, session, port=port)
        await console.start()
        console.publish({"type": "user", "text": "往前走"})

        page = await asyncio.to_thread(lambda: urllib.request.urlopen(console.url, timeout=2).read().decode())
        assert "语音坦克控制台" in page

        origin = f"http://localhost:{port}"
        async with connect(f"ws://127.0.0.1:{port}/ws", origin=origin) as ws:
            init = await _recv_type(ws, "init")
            assert init["history"] == [{"type": "user", "text": "往前走"}]
            assert init["conn"] == "connecting"

            await ws.send(json.dumps({"type": "action", "name": "forward"}))
            manual = await _recv_type(ws, "manual")
            assert "前进" in manual["text"]

            await ws.send(json.dumps({"type": "action", "name": "rm -rf"}))
            assert "未知动作" in (await _recv_type(ws, "error"))["text"]

            await ws.send(json.dumps({"type": "text", "text": "开火"}))
            await ws.send(json.dumps({"type": "mic", "on": False}))
            assert (await _recv_type(ws, "mic"))["on"] is False
            assert session.mic_enabled is False and session.sent == ["开火"]

            await ws.send(json.dumps({"type": "stop"}))
            await _recv_type(ws, "info")
        assert ("drive", 40, 40, 1300) in backend.calls  # 手动前进也被裁剪到 max_speed
        assert backend.calls[-1] == ("halt",)

        with pytest.raises(InvalidStatus):  # 别的网站的页面不能连进来
            async with connect(f"ws://127.0.0.1:{port}/ws", origin="http://evil.example"):
                pass
        await tank.close()

    asyncio.run(run())
