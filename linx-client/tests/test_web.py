import asyncio
import json
import socket
import urllib.error
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

    async def abort(self):
        self.sent.append("<abort>")


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
            await ws.send(json.dumps({"type": "abort"}))

            await ws.send(json.dumps({"type": "stop"}))
            await _recv_type(ws, "info")
        assert ("drive", 40, 40, 1300) in backend.calls  # 手动前进也被裁剪到 max_speed
        assert backend.calls[-1] == ("halt",)

        with pytest.raises(InvalidStatus):  # 别的网站的页面不能连进来
            async with connect(f"ws://127.0.0.1:{port}/ws", origin="http://evil.example"):
                pass
        await tank.close()

    asyncio.run(run())


def test_phone_pad_over_lan():
    """手机从局域网连：控制台不给看，手柄要密钥，只能发手柄类指令，断开就停车。"""
    from linx_client.tank.udp import _lan_ip
    from linx_client.web import PAD_COMMANDS

    try:
        lan = _lan_ip()
    except OSError:
        pytest.skip("没有局域网")

    def get(url):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=2) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, ""

    async def run():
        backend = MockBackend(verbose=False)
        tank = TankExecutor(backend, max_speed=80)
        await tank.start()
        port = _free_port()
        console = WebConsole(tank, None, port=port, pad=True)
        await console.start()
        key = console.pad_key
        assert console.pad_url == f"http://{lan}:{port}/pad?k={key}"
        base = f"http://{lan}:{port}"

        assert (await asyncio.to_thread(get, base + "/"))[0] == 403  # 控制台只给本机
        assert (await asyncio.to_thread(get, base + "/pad"))[0] == 403  # 没密钥
        status, page = await asyncio.to_thread(get, console.pad_url)
        assert status == 200 and "坦克手柄" in page
        assert (await asyncio.to_thread(get, f"http://127.0.0.1:{port}/"))[0] == 200  # 本机照常

        with pytest.raises(InvalidStatus):
            async with connect(f"ws://{lan}:{port}/ws", origin=base):
                pass
        async with connect(f"ws://{lan}:{port}/ws?k={key}", origin=base) as ws:
            init = await _recv_type(ws, "init")
            assert init["history"] == [] and init["pad_url"] is None  # 手机拿不到对话记录和密钥
            await ws.send(json.dumps({"type": "pad", "l": 100, "r": -100, "barrel": 1}))
            await ws.send(json.dumps({"type": "text", "text": "开火"}))
            assert "不能" in (await _recv_type(ws, "error"))["text"]
            await ws.send(json.dumps({"type": "action", "name": "forward"}))
            assert "不能" in (await _recv_type(ws, "error"))["text"]
        await asyncio.sleep(0.1)
        assert ("drive", 80, -80, 400) in backend.calls  # 裁剪到限速，看门狗 0.4s
        assert ("turret", 45, 400) in backend.calls
        assert backend.calls[-1] == ("halt",)  # 手机断开立刻停
        assert {"pad", "stop", "action"} == PAD_COMMANDS
        await tank.close()

    asyncio.run(run())
