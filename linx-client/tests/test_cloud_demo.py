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


def test_barrel_single_shot_lock_cooldown_and_new_controller():
    async def run():
        backend = MockBackend(verbose=False)
        tank = TankExecutor(backend, max_speed=40, fire_enabled=False)
        gateway = type("OnlineGateway", (), {"online": True, "fire_supported": True})()
        demo = CloudDemo(tank, "a" * 40, gateway, fire_allowed=True)
        port = _free_port()
        await tank.start()
        await demo.start(port)
        lease = None

        async def receive(ws, kind):
            nonlocal lease
            async with asyncio.timeout(2):
                while True:
                    msg = json.loads(await ws.recv())
                    if msg["type"] == "lease":
                        lease = msg["id"]
                        await ws.send(json.dumps({"type": "presence", "lease": lease}))
                    if msg["type"] == kind:
                        return msg

        async def send(ws, kind, **args):
            await ws.send(json.dumps({"type": kind, "lease": lease, **args}))

        try:
            url = f"ws://127.0.0.1:{port}/control"
            origin = f"http://127.0.0.1:{port}"
            async with connect(url, origin=origin) as ws:
                await ws.send(json.dumps({"type": "auth", "token": "a" * 40}))
                ready = await receive(ws, "ready")
                assert ready["fire"]["available"] and not ready["fire"]["armed"]
                await receive(ws, "lease")
                await send(ws, "fire")
                assert "启用发射" in (await receive(ws, "error"))["message"]
                await send(ws, "barrel", direction="up")
                assert (await receive(ws, "action_result"))["action"] == "barrel"
                await _wait_for(lambda: ("turret", 45, 500) in backend.calls)
                await tank.wait_idle()
                await send(ws, "fire_arm", on=True)
                assert (await receive(ws, "fire_state"))["armed"]
                assert tank.fire_enabled
                await send(ws, "fire")
                assert (await receive(ws, "action_result"))["action"] == "fire"
                await _wait_for(lambda: any(c[0] == "fire" for c in backend.calls))
                await send(ws, "fire")
                assert "冷却" in (await receive(ws, "error"))["message"]
                assert len([c for c in backend.calls if c[0] == "fire"]) == 1
                await send(ws, "stop")
                assert not (await receive(ws, "fire_state"))["armed"]
                assert not tank.fire_enabled
            await _wait_for(lambda: demo._controller is None)
            async with connect(url, origin=origin) as ws:
                await ws.send(json.dumps({"type": "auth", "token": "a" * 40}))
                assert not (await receive(ws, "ready"))["fire"]["armed"]
        finally:
            demo.server.close()
            await demo.server.wait_closed()
            await tank.close()

    asyncio.run(run())


def test_analog_pad_requires_fresh_lease_and_obeys_speed_limit():
    async def run():
        backend = MockBackend(verbose=False)
        tank = TankExecutor(backend, max_speed=40, fire_enabled=False)
        gateway = type("OnlineGateway", (), {"online": True, "fire_supported": True})()
        demo = CloudDemo(tank, "a" * 40, gateway, fire_allowed=True)
        port = _free_port()
        await tank.start()
        await demo.start(port)
        lease = None

        async def receive(ws, kind):
            nonlocal lease
            async with asyncio.timeout(2):
                while True:
                    msg = json.loads(await ws.recv())
                    if msg["type"] == "lease":
                        lease = msg["id"]
                        await ws.send(json.dumps({"type": "presence", "lease": lease}))
                    if msg["type"] == kind:
                        return msg

        try:
            async with connect(f"ws://127.0.0.1:{port}/control", origin=f"http://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "auth", "token": "a" * 40}))
                await receive(ws, "ready")
                await receive(ws, "lease")
                await ws.send(json.dumps({"type": "pad", "lease": "expired", "l": 30, "r": 30}))
                assert "过期" in (await receive(ws, "error"))["message"]
                for invalid in (True, 1.5, "40", 101, -101, None):
                    await ws.send(json.dumps({"type": "pad", "lease": lease, "l": invalid, "r": 0}))
                    assert "整数" in (await receive(ws, "error"))["message"]
                assert not any(c[0] == "drive" for c in backend.calls)
                await ws.send(json.dumps({"type": "pad", "lease": lease, "l": 30, "r": -12}))
                await _wait_for(lambda: ("drive", 30, -12, 400) in backend.calls)
                await ws.send(json.dumps({"type": "pad", "lease": lease, "l": 100, "r": -100}))
                await _wait_for(lambda: ("drive", 40, -40, 400) in backend.calls)
                await ws.send(json.dumps({"type": "fire_arm", "lease": lease, "on": True}))
                assert (await receive(ws, "fire_state"))["armed"]
                await ws.send(json.dumps({"type": "pad", "lease": lease, "l": 0, "r": 0}))
                assert not (await receive(ws, "fire_state"))["armed"]
                await _wait_for(lambda: backend.calls[-1] == ("halt",))
        finally:
            demo.server.close()
            await demo.server.wait_closed()
            await tank.close()

    asyncio.run(run())


def test_slow_car_coalesces_analog_commands_and_stop_discards_pending():
    class SlowBackend(MockBackend):
        def __init__(self):
            super().__init__(verbose=False)
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def drive(self, left, right, failsafe_ms):
            self.started.set()
            await self.release.wait()
            await super().drive(left, right, failsafe_ms)

    async def run():
        backend = SlowBackend()
        tank = TankExecutor(backend, max_speed=40, fire_enabled=False)
        gateway = type("OnlineGateway", (), {"online": True, "fire_supported": True})()
        demo = CloudDemo(tank, "a" * 40, gateway)
        port = _free_port()
        await tank.start()
        await demo.start(port)
        try:
            async with connect(f"ws://127.0.0.1:{port}/control", origin=f"http://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "auth", "token": "a" * 40}))
                assert json.loads(await ws.recv())["type"] == "ready"
                lease = json.loads(await ws.recv())["id"]
                await ws.send(json.dumps({"type": "pad", "lease": lease, "l": 25, "r": 25}))
                await asyncio.wait_for(backend.started.wait(), 1)
                previous = demo._last_presence
                for left in (26, 32, 38):
                    await ws.send(json.dumps({"type": "pad", "lease": lease, "l": left, "r": -10}))
                await ws.send(json.dumps({"type": "presence", "lease": lease}))
                await _wait_for(lambda: demo._last_presence > previous)
                backend.release.set()
                await _wait_for(lambda: ("drive", 38, -10, 400) in backend.calls)
                assert ("drive", 26, -10, 400) not in backend.calls
                assert ("drive", 32, -10, 400) not in backend.calls

                backend.release.clear()
                backend.started.clear()
                await ws.send(json.dumps({"type": "pad", "lease": lease, "l": -20, "r": -20}))
                await asyncio.wait_for(backend.started.wait(), 1)
                await ws.send(json.dumps({"type": "pad", "lease": lease, "l": 40, "r": 40}))
                await ws.send(json.dumps({"type": "stop"}))
                await _wait_for(lambda: backend.calls[-1] == ("halt",))
                stop_at = len(backend.calls)
                backend.release.set()
                await asyncio.sleep(0.1)
                assert not any(c[0] == "drive" for c in backend.calls[stop_at:])
        finally:
            demo.server.close()
            await demo.server.wait_closed()
            await tank.close()

    asyncio.run(run())
