import asyncio
import importlib.util
import socket
import sys
import threading
import types
from pathlib import Path

import pytest

from linx_client.tank.udp import UdpBackend


class FakeCar:
    """本机 UDP 假车：记录收到的指令，可以丢掉前 drop 个包来模拟丢包。"""

    def __init__(self, drop=0, reply=b"ok"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.drop = drop
        self.reply = reply
        self.received: list[str] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            data, addr = self.sock.recvfrom(128)
            self.received.append(data.decode())
            if self.drop > 0:
                self.drop -= 1
                continue
            self.sock.sendto(b"pong" if data == b"ping" else self.reply, addr)


def test_udp_commands():
    car = FakeCar()
    b = UdpBackend("127.0.0.1", car.port)

    async def run():
        await b.connect()
        await b.drive(40, -40, 1300)
        await b.turret(-45, 800)
        await b.fire(500)
        await b.close()

    asyncio.run(run())
    assert car.received == ["ping", "drive 40 -40 1300", "turret -45 800", "fire 500", "halt"]


def test_udp_retries_on_loss():
    car = FakeCar(drop=2)
    b = UdpBackend("127.0.0.1", car.port, timeout=0.1, retries=3)
    asyncio.run(b.connect())
    assert car.received == ["ping", "ping", "ping"]


def test_udp_raises_on_err_and_timeout():
    car = FakeCar(reply=b"err \xe6\x9c\xaa\xe7\x9f\xa5\xe6\x8c\x87\xe4\xbb\xa4")
    b = UdpBackend("127.0.0.1", car.port, timeout=0.1)
    asyncio.run(b.connect())
    with pytest.raises(RuntimeError):
        asyncio.run(b.drive(10, 10, 100))

    silent = FakeCar(drop=99)
    b2 = UdpBackend("127.0.0.1", silent.port, timeout=0.05, retries=2)
    with pytest.raises(TimeoutError):
        asyncio.run(b2.connect())


class _Rec:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def __getattr__(self, attr):
        return lambda *a, **k: self.log.append((self.name, attr, *a))


@pytest.fixture
def car_main(monkeypatch):
    """用桩模块加载车上的 firmware/car/wifi_main.py，验证指令解析。"""
    log: list[tuple] = []
    machine = types.ModuleType("machine")
    machine.Timer = lambda n: types.SimpleNamespace(init=lambda **k: log.append(("timer", k["period"])), ONE_SHOT=0)
    machine.Timer.ONE_SHOT = 0
    machine.Pin = lambda *a, **k: None
    machine.Pin.OUT = 0
    neopixel = types.ModuleType("neopixel")

    class _NP(list):
        def __init__(self, pin, n):
            super().__init__([None] * n)

        def write(self):
            log.append(("np", self[0]))

    neopixel.NeoPixel = _NP
    motors = types.ModuleType("motors")
    motors.MotorsController = lambda: _Rec(log, "m")
    servos = types.ModuleType("servos")
    servos.ServosController = lambda: _Rec(log, "s")
    for name, mod in {"machine": machine, "motors": motors, "servos": servos, "neopixel": neopixel,
                      "network": types.ModuleType("network")}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    path = Path(__file__).resolve().parents[2] / "firmware" / "car" / "wifi_main.py"
    spec = importlib.util.spec_from_file_location("wifi_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, log


def test_car_handle(car_main):
    mod, log = car_main
    assert mod.handle("ping") == "pong"
    log.clear()
    assert mod.handle("drive 40 30 1300") == "ok"
    assert log == [("m", "set_speed", 1, 614), ("m", "set_speed", 2, -818), ("timer", 1300)]
    log.clear()
    assert mod.handle("drive 500 0 99999") == "ok"  # 速度、时长都被裁剪
    assert log == [("m", "set_speed", 1, 0), ("m", "set_speed", 2, -2047), ("timer", 6000)]
    assert mod.handle("fire x") == "err 参数不是整数"
    assert mod.handle("exec('import os')") == "err 未知指令"
    log.clear()
    assert mod.handle("led 300 -5 20") == "ok"  # 颜色裁剪到 0-255
    assert log == [("np", (255, 0, 20))]
    log.clear()
    assert mod.handle("halt") == "ok"
    assert ("s", "set_angle", 2, 86) in log
