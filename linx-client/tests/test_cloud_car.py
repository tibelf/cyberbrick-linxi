"""模拟设备验证认证、超时、取消与断线时的动作安全。"""

import asyncio
import json
import socket

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from linx_client.cloud_car import CloudCarBackend, CloudCarGateway, COMMAND_AGE_S
from linx_client.cloud_demo import CloudDemo
from linx_client.tank import TankExecutor


async def _open_gateway():
    gateway = CloudCarGateway("a" * 40)
    await gateway.start(0)
    port = gateway.server.sockets[0].getsockname()[1]
    return gateway, f"ws://127.0.0.1:{port}/device"


async def _auth(ws, token="a" * 40, capabilities=None):
    await ws.send(json.dumps({"type": "auth", "token": token, "capabilities": capabilities or []}))
    return json.loads(await ws.recv())


async def _poll(ws, nonce):
    await ws.send(json.dumps({"type": "poll", "nonce": nonce}))
    return json.loads(await ws.recv())


async def _ack(ws, command, ok=True):
    await ws.send(json.dumps({"type": "ack", "id": command["id"], "ok": ok}))


async def _close(gateway):
    gateway.server.close()
    await gateway.server.wait_closed()


def test_authenticated_device_and_acknowledged_command():
    async def run():
        gateway, url = await _open_gateway()
        backend = CloudCarBackend(gateway, lambda: True)
        states = []
        gateway.on_status = states.append
        try:
            async with connect(url) as denied:
                with pytest.raises(ConnectionClosedError) as rejected:
                    await _auth(denied, "wrong")
                assert rejected.value.rcvd.code == 4001
            async with connect(url) as device:
                assert (await _auth(device))["type"] == "ready"
                assert (await _poll(device, 1))["type"] == "noop"
                assert (await backend.ping())["online"]
                action = asyncio.create_task(backend.drive(20, 20, 400))
                await asyncio.sleep(0)
                command = await _poll(device, 2)
                assert (command["op"], command["l"], command["r"], command["ms"]) == ("drive", 20, 20, 400)
                assert not action.done()
                await _ack(device, command)
                await action
                with pytest.raises(PermissionError, match="发射"):
                    await backend.fire(300)
            await asyncio.sleep(0.01)
            assert states == [True, False]
            assert not gateway.online
        finally:
            await _close(gateway)

    asyncio.run(run())


def test_expired_command_is_not_replayed_on_reconnect():
    async def run():
        gateway, url = await _open_gateway()
        backend = CloudCarBackend(gateway, lambda: True)
        try:
            async with connect(url) as device:
                await _auth(device)
                await _poll(device, 1)
                action = asyncio.create_task(backend.drive(20, 20, 400))
                await asyncio.sleep(COMMAND_AGE_S + 0.05)
                assert (await _poll(device, 2))["type"] == "noop"
                with pytest.raises(TimeoutError):
                    await action
            async with connect(url) as next_device:
                await _auth(next_device)
                assert (await _poll(next_device, 1))["type"] == "noop"
        finally:
            await _close(gateway)

    asyncio.run(run())


def test_fire_requires_updated_device_and_current_permission():
    async def run():
        gateway, url = await _open_gateway()
        armed = [False]
        backend = CloudCarBackend(gateway, lambda: True, lambda: armed[0])
        try:
            async with connect(url) as old_device:
                await _auth(old_device)
                await _poll(old_device, 1)
                armed[0] = True
                with pytest.raises(RuntimeError, match="更新"):
                    await backend.fire(500)
            await asyncio.sleep(.01)
            async with connect(url) as device:
                await _auth(device, capabilities=["fire-v1"])
                await _poll(device, 1)
                assert gateway.fire_supported
                armed[0] = False
                with pytest.raises(PermissionError, match="启用发射"):
                    await backend.fire(500)
                armed[0] = True
                with pytest.raises(ValueError):
                    await backend.fire(501)
                shot = asyncio.create_task(backend.fire(500))
                await asyncio.sleep(0)
                command = await _poll(device, 2)
                assert (command["op"], command["ms"]) == ("fire", 500)
                await _ack(device, command)
                await shot
            await asyncio.sleep(.01)
            assert not gateway.fire_supported
        finally:
            await _close(gateway)

    asyncio.run(run())


def test_stop_supersedes_pending_motion_and_disconnect_fails_outstanding():
    async def run():
        gateway, url = await _open_gateway()
        backend = CloudCarBackend(gateway, lambda: True)
        disconnected = []
        gateway.on_disconnect = lambda: disconnected.append(True)
        try:
            async with connect(url) as device:
                await _auth(device)
                await _poll(device, 1)
                motion = asyncio.create_task(backend.drive(20, 20, 400))
                await asyncio.sleep(0)
                halt = asyncio.create_task(backend.halt())
                await asyncio.sleep(0)
                with pytest.raises(ConnectionError, match="停车"):
                    await motion
                command = await _poll(device, 2)
                assert command["op"] == "halt"
                await _ack(device, command)
                await halt
                pending = asyncio.create_task(backend.drive(20, 20, 400))
                await asyncio.sleep(0)
            with pytest.raises(ConnectionError, match="中断"):
                await pending
            assert disconnected
        finally:
            await _close(gateway)

    asyncio.run(run())


def test_device_must_ack_and_controller_must_be_authorized():
    async def run():
        gateway, url = await _open_gateway()
        backend = CloudCarBackend(gateway, lambda: False)
        try:
            async with connect(url) as device:
                await _auth(device)
                await _poll(device, 1)
                with pytest.raises(RuntimeError, match="控制者"):
                    await backend.drive(20, 20, 400)
                assert (await _poll(device, 2))["type"] == "noop"
                pending = asyncio.create_task(gateway.command("drive", l=20, r=20, ms=400))
                await asyncio.sleep(0)
                assert (await _poll(device, 3))["op"] == "drive"
                with pytest.raises(ConnectionClosedError) as rejected:
                    await _poll(device, 4)
                assert rejected.value.rcvd.code == 4003
                with pytest.raises(ConnectionError):
                    await pending
        finally:
            await _close(gateway)

    asyncio.run(run())


def test_real_control_requires_fresh_page_and_stop_reaches_device():
    async def run():
        gateway, url = await _open_gateway()
        owner = {}
        tank = TankExecutor(CloudCarBackend(gateway, lambda: owner["demo"].controller_authorized()),
                            max_speed=40, fire_enabled=False)
        demo = CloudDemo(tank, "b" * 40, gateway)
        owner["demo"] = demo
        await tank.start()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        await demo.start(port)
        origin = f"http://127.0.0.1:{port}"
        try:
            async with connect(url) as device:
                await _auth(device)
                await _poll(device, 1)
                async with connect(f"ws://127.0.0.1:{port}/control", origin=origin) as browser:
                    await browser.send(json.dumps({"type": "auth", "token": "b" * 40}))
                    ready = json.loads(await browser.recv())
                    assert ready["mode"] == "真实小车" and ready["car_online"]
                    lease = json.loads(await browser.recv())["id"]
                    await browser.send(json.dumps({"type": "presence", "lease": lease}))
                    await browser.send(json.dumps({"type": "drive", "direction": "forward", "lease": "invalid"}))
                    assert "过期" in json.loads(await browser.recv())["message"]
                    await browser.send(json.dumps({"type": "drive", "direction": "forward", "lease": lease}))
                    await asyncio.sleep(0.02)
                    drive = await _poll(device, 2)
                    assert (drive["op"], drive["l"], drive["r"], drive["ms"]) == ("drive", 40, 40, 400)
                    await _ack(device, drive)
                    await browser.send(json.dumps({"type": "stop"}))
                    await asyncio.sleep(0.02)
                    halt = await _poll(device, 3)
                    assert halt["op"] == "halt"
                    await _ack(device, halt)
        finally:
            demo.server.close()
            gateway.server.close()
            await demo.server.wait_closed()
            await gateway.server.wait_closed()
            await tank.close()

    asyncio.run(run())
