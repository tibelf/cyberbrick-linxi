"""“车没反应”的排查：真的问一下车在不在、发现车重启过、动作在后台失败时如实报告。"""

import asyncio
import json
import socket
import threading

import pytest

from linx_client.mcp import McpServer
from linx_client.tank import MockBackend, TankExecutor
from linx_client.tank import udp
from linx_client.tank.udp import UdpBackend
from linx_client.tools import register_tank_tools


class _BrokenBackend(MockBackend):
    def __init__(self):
        super().__init__(verbose=False)
        self.alive = True

    async def drive(self, left, right, failsafe_ms):
        raise TimeoutError("车（192.168.1.8）没有应答：'drive'")

    async def ping(self):
        if not self.alive:
            raise TimeoutError("车没有应答")
        return {"online": True, "link": "假车"}


def test_failed_action_is_reported_not_silent():
    async def main():
        tank = TankExecutor(_BrokenBackend())
        errors = []
        tank.on_error = errors.append
        await tank.start()
        assert tank.move("forward", 100, 50).startswith("已安排")  # 工具调用当时并不知道会失败
        await tank.wait_idle()
        await tank.close()
        assert "没执行成功" in tank.status()["last_error"]["message"] and len(errors) == 1

    asyncio.run(main())


def test_offline_reported_once_until_back_online():
    async def main():
        be = _BrokenBackend()
        tank = TankExecutor(be)
        errors = []
        tank.on_error = errors.append
        assert (await tank.check())["online"]
        be.alive = False
        assert (await tank.check())["online"] is False
        await tank.check()
        assert len(errors) == 1 and "没有应答" in errors[0]  # 车一直关着也只提醒一次
        be.alive = True
        await tank.check()
        be.alive = False
        await tank.check()
        assert len(errors) == 2

    asyncio.run(main())


def test_get_device_status_tool_checks_car():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        mcp = McpServer()
        register_tank_tools(mcp, tank)
        resp = await mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "self.get_device_status", "arguments": {}}})
        await tank.close()
        status = json.loads(resp["result"]["content"][0]["text"])
        assert status["link"]["online"] is True and "last_error" in status

    asyncio.run(main())


class _FakeCar:
    """本机 UDP 假车：按脚本回复。"""

    def __init__(self, replies):
        self.replies = replies
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.got = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(128)
            except OSError:
                return
            cmd = data.decode()
            self.got.append(cmd)
            reply = self.replies(cmd)
            if reply:
                self.sock.sendto(reply.encode(), addr)


def _udp(car, **kw):
    be = UdpBackend(host="127.0.0.1", port=car.port, timeout=0.1, **kw)
    be._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    be._sock.settimeout(be.timeout)
    return be


def test_udp_ping_detects_reboot():
    ups = iter([600, 605, 3])
    car = _FakeCar(lambda cmd: f"stat up={next(ups)} mem=50000" if cmd == "stat" else "ok")
    be = _udp(car)
    r = [asyncio.run(be.ping()) for _ in range(3)]
    assert [x["rebooted"] for x in r] == [False, False, True]
    assert r[0]["online"] and r[0]["uptime_s"] == 600


def test_udp_ping_works_with_old_car_program():
    car = _FakeCar(lambda cmd: "pong" if cmd == "ping" else "err 未知指令")
    info = asyncio.run(_udp(car).ping())
    assert info["online"] and "uptime_s" not in info


def test_udp_rediscovers_when_car_ip_changes(monkeypatch):
    car = _FakeCar(lambda cmd: "ok")  # 车在 127.0.0.1
    be = _udp(car)
    be.host = "127.0.0.2"  # 车重启后 DHCP 换了 IP：旧地址上没有车
    monkeypatch.setattr(udp, "discover", lambda port: "127.0.0.1")
    assert be._send_sync("halt") == "ok" and be.host == "127.0.0.1"
    # 车真的关了：重新找也找不到，如实报错；而且 10 秒内不会每条指令都重新扫一遍
    monkeypatch.setattr(udp, "discover", lambda port: pytest.fail("不该这么快又扫网段"))
    be.host = "127.0.0.2"
    with pytest.raises(TimeoutError):
        be._send_sync("halt")
