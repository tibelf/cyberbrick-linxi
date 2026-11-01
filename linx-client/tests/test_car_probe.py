"""车端探测服务不应接受未认证连接或任何动作。"""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError, InvalidStatus

from linx_client.car_probe import CarProbe


def test_probe_auth_single_device_and_no_motion():
    async def run():
        token = "a" * 40
        probe = CarProbe(token)
        await probe.start(0)
        port = probe.server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}/device"
        try:
            with pytest.raises(InvalidStatus):
                async with connect(url, origin="https://evil.example"):
                    pass
            async with connect(url) as denied:
                await denied.send(json.dumps({"type": "auth", "token": "wrong"}))
                with pytest.raises(ConnectionClosedError) as rejected:
                    await denied.recv()
                assert rejected.value.rcvd.code == 4001
            async with connect(url) as device:
                await device.send(json.dumps({"type": "auth", "token": token}))
                assert json.loads(await device.recv()) == {"type": "ready", "mode": "只读检测"}
                async with connect(url) as second:
                    await second.send(json.dumps({"type": "auth", "token": token}))
                    with pytest.raises(ConnectionClosedError) as busy:
                        await second.recv()
                    assert busy.value.rcvd.code == 4002
                await device.send(json.dumps({"type": "ping", "id": 7}))
                assert json.loads(await device.recv()) == {"type": "pong", "id": 7}
                await device.send(json.dumps({"type": "drive", "left": 100, "right": 100}))
                with pytest.raises(ConnectionClosedError) as rejected:
                    await device.recv()
                assert rejected.value.rcvd.code == 4003
            async with connect(url) as next_device:
                await next_device.send(json.dumps({"type": "auth", "token": token}))
                assert json.loads(await next_device.recv())["type"] == "ready"
        finally:
            probe.server.close()
            await probe.server.wait_closed()

    asyncio.run(run())


def test_probe_requires_strong_password():
    with pytest.raises(ValueError):
        CarProbe("short")
