"""USB 串口控车：SerialBackend 的连接、每条指令发的那一行、找串口的规则。用假串口，不接车。"""

import asyncio
from types import SimpleNamespace

import pytest
import serial
from serial.tools import list_ports

from linx_client.tank import MockBackend, make_backend
from linx_client.tank.serial_repl import SerialBackend, find_port


class FakeSerial:
    """模拟车上的 raw REPL：注入 BOOTSTRAP 时回 cb-ready，print('pong') 回 pong，其他指令没有输出。"""

    def __init__(self, name, baud, timeout=None):
        self.name = name
        self.rx = b""
        self.lines = []
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.rx)

    def read(self, n):
        out, self.rx = self.rx[:n], self.rx[n:]
        return out

    def reset_input_buffer(self):
        self.rx = b""

    def write(self, data):
        if data == b"\x01":
            self.rx += b"raw REPL; CTRL-B to exit\r\n>"
        elif data.endswith(b"\x04"):
            code = data[:-1].decode()
            self.lines.append(code)
            out = b"cb-ready\r\n" if "cb-ready" in code else b"pong\r\n" if "pong" in code else b""
            self.rx += b"OK" + out + b"\x04\x04>"

    def close(self):
        self.closed = True


@pytest.fixture
def fake_serial(monkeypatch):
    opened = []

    def factory(name, baud, timeout=None):
        port = FakeSerial(name, baud, timeout)
        opened.append(port)
        return port

    monkeypatch.setattr(serial, "Serial", factory)
    monkeypatch.setattr("linx_client.tank.serial_repl.time.sleep", lambda s: None)
    return opened


def test_backend_sends_one_line_per_command(fake_serial):
    async def main():
        backend = SerialBackend("/dev/fake")
        await backend.connect()
        await backend.drive(60, -40, 2300)
        await backend.turret(45, 600)
        await backend.fire(500)
        await backend.led(0, 0, 255)
        status = await backend.ping()
        await backend.close()
        return status

    status = asyncio.run(main())
    port = fake_serial[0]
    assert port.name == "/dev/fake"
    assert "cb-ready" in port.lines[0]  # 先注入驱动
    assert port.lines[1:6] == ["drive(60,-40,2300)", "turret(45,600)", "fire(500)", "led(0,0,255)", "print('pong')"]
    assert port.lines[-1] == "halt()"  # 关闭前先停车
    assert port.closed
    assert status["online"] and status["link"] == "USB /dev/fake"


def test_backend_fails_clearly_without_port(monkeypatch):
    monkeypatch.setattr("linx_client.tank.serial_repl.find_port", lambda: None)
    with pytest.raises(RuntimeError, match="找不到 CyberBrick 串口"):
        asyncio.run(SerialBackend().connect())


def test_find_port_prefers_env_then_espressif(monkeypatch):
    monkeypatch.setenv("CYBERBRICK_PORT", "COM9")
    assert find_port() == "COM9"
    monkeypatch.delenv("CYBERBRICK_PORT")

    ports = [SimpleNamespace(vid=0x1234, device="/dev/other", description="USB Serial"),
             SimpleNamespace(vid=0x303A, device="/dev/esp", description="USB JTAG")]
    monkeypatch.setattr(list_ports, "comports", lambda: ports)
    assert find_port() == "/dev/esp"

    monkeypatch.setattr(list_ports, "comports", lambda: ports[:1])
    assert find_port() == "/dev/other"  # 没有乐鑫芯片时按描述认

    monkeypatch.setattr(list_ports, "comports", lambda: [])
    assert find_port() is None


def test_make_backend():
    assert isinstance(make_backend("mock"), MockBackend)
    assert isinstance(make_backend("serial", port="/dev/x"), SerialBackend)
    assert make_backend("udp", host="10.0.0.2").__class__.__name__ == "UdpBackend"
    with pytest.raises(ValueError):
        make_backend("bluetooth")
