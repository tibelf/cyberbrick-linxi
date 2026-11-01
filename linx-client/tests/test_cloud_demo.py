"""通过真实 HTTP 与 WebSocket 验证模拟云控台的权限和停车行为。"""

import asyncio
import json
import socket
import urllib.request

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, InvalidStatus

import linx_client.cloud_demo as cloud_demo
from linx_client.cloud_demo import CloudDemo
from linx_client.tank import MockBackend, TankExecutor


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.01)


def test_demo_authentication_and_disconnect_stop():
    async def run():
        port = _free_port()
        origin = f"http://127.0.0.1:{port}"
        url = f"ws://127.0.0.1:{port}/control"
        token = "a" * 40
        backend = MockBackend(verbose=False)
        tank = TankExecutor(backend, max_speed=40, fire_enabled=False)
        demo = CloudDemo(tank, token)
        await tank.start()
        await demo.start(port)
        try:
            page = await asyncio.to_thread(lambda: urllib.request.urlopen(origin + "/", timeout=2).read())
            assert "模拟模式" in page.decode()
            assert token.encode() not in page

            # 来自同一台机器也不能跳过密码；别的网站不能建立 WebSocket。
            with pytest.raises(InvalidStatus):
                async with connect(url, origin="http://evil.example"):
                    pass
            async with connect(url, origin=origin) as denied:
                await denied.send(json.dumps({"type": "drive", "direction": "forward"}))
                with pytest.raises(ConnectionClosedError) as rejected:
                    await denied.recv()
                assert rejected.value.rcvd.code == 4001
            assert not any(call[0] == "drive" for call in backend.calls)

            async with connect(url, origin=origin) as ws:
                await ws.send(json.dumps({"type": "auth", "token": token}))
                assert json.loads(await ws.recv())["mode"] == "模拟小车"
                async with connect(url, origin=origin) as other:
                    await other.send(json.dumps({"type": "auth", "token": token}))
                    with pytest.raises(ConnectionClosedError) as busy:
                        await other.recv()
                    assert busy.value.rcvd.code == 4002
                await ws.send(json.dumps({"type": "drive", "direction": "forward"}))
                await _wait_for(lambda: ("drive", 40, 40, 400) in backend.calls)
                await ws.send(json.dumps({"type": "fire"}))
                assert json.loads(await ws.recv())["type"] == "error"
                assert not any(call[0] == "fire" for call in backend.calls)

            await _wait_for(lambda: backend.calls[-1] == ("halt",))
            async with connect(url, origin=origin) as next_owner:
                await next_owner.send(json.dumps({"type": "auth", "token": token}))
                assert json.loads(await next_owner.recv())["type"] == "ready"
        finally:
            demo.server.close()
            await demo.server.wait_closed()
            await tank.close()

    asyncio.run(run())


def test_demo_rejects_weak_or_missing_password():
    tank = TankExecutor(MockBackend(verbose=False))
    for token in ("", "short"):
        with pytest.raises(ValueError):
            CloudDemo(tank, token)


def test_proxy_origin_requires_https_and_is_explicit():
    async def run():
        port = _free_port()
        token = "a" * 40
        tank = TankExecutor(MockBackend(verbose=False))
        demo = CloudDemo(tank, token)
        await tank.start()
        try:
            for invalid in ("http://car.example", "https://car.example/path", "https://user@car.example"):
                with pytest.raises(ValueError):
                    await demo.start(port, invalid)
            await demo.start(port, "https://car.example")
            url = f"ws://127.0.0.1:{port}/control"
            with pytest.raises(InvalidStatus):
                async with connect(url, origin="https://another.example"):
                    pass
            async with connect(url, origin="https://car.example") as ws:
                await ws.send(json.dumps({"type": "auth", "token": token}))
                assert json.loads(await ws.recv())["type"] == "ready"
        finally:
            demo.server.close()
            await demo.server.wait_closed()
            await tank.close()

    asyncio.run(run())


def test_real_controller_without_presence_is_disconnected_and_stopped(monkeypatch):
    monkeypatch.setattr(cloud_demo, "CONTROLLER_TIMEOUT_S", 0.15)

    async def run():
        port = _free_port()
        backend = MockBackend(verbose=False)
        tank = TankExecutor(backend, max_speed=40, fire_enabled=False)
        gateway = type("OnlineGateway", (), {"online": True})()
        demo = CloudDemo(tank, "a" * 40, gateway)
        await tank.start()
        await demo.start(port)
        try:
            async with connect(f"ws://127.0.0.1:{port}/control", origin=f"http://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "auth", "token": "a" * 40}))
                assert json.loads(await ws.recv())["mode"] == "真实小车"
                with pytest.raises(ConnectionClosed):
                    async with asyncio.timeout(2):
                        while True:
                            await ws.recv()  # 不回应租约，模拟网页离线
            await _wait_for(lambda: backend.calls[-1] == ("halt",))
        finally:
            demo.server.close()
            await demo.server.wait_closed()
            await tank.close()

    asyncio.run(run())
