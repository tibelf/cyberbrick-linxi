"""通过 USB 串口 + MicroPython raw REPL 控制 CyberBrick Core（ESP32-C3）。

引脚映射（Mini T，待按车上 rc_config 确认）：
  左履带 Pin2、右履带 Pin4(前)/Pin5(后)、炮塔舵机 Pin3(50Hz)、发射电机 Pin6/Pin7。
设计要点：
  - 连接时只注入一次辅助函数，之后每条指令只发一行，延迟低；
  - 指令不阻塞（不在设备上 sleep），"停"随时能打断；
  - 设备端用 machine.Timer 做看门狗：上位机掉线 failsafe_ms 后自动停车。

⚠️ 引脚语义（尤其左履带"duty 0 = 前进"）未在我们的车上验证。
首次使用请把车架空，运行 `python -m linx_client tank-test --backend serial` 确认方向。
"""

import asyncio
import os
import threading
import time

BOOTSTRAP = r"""
from machine import Pin, PWM, Timer
_pw = {}
def _pwm(n, f=1000):
    p = _pw.get(n)
    if p is None:
        p = PWM(Pin(n), freq=f)
        _pw[n] = p
    return p
def _off(n):
    p = _pw.pop(n, None)
    if p:
        p.deinit()
def halt(*_):
    for n in list(_pw):
        _off(n)
_wd = Timer(0)
def _arm(ms):
    _wd.init(mode=Timer.ONE_SHOT, period=ms, callback=halt)
def drive(l, r, ms):
    if l > 0:
        _pwm(2).duty(0)
    elif l < 0:
        _pwm(2).duty(int(-l * 10.23))
    else:
        _off(2)
    if r > 0:
        _pwm(4).duty(int(r * 10.23)); _pwm(5).duty(0)
    elif r < 0:
        _pwm(4).duty(0); _pwm(5).duty(int(-r * 10.23))
    else:
        _off(4); _off(5)
    _arm(ms)
def aim(a):
    _pwm(3, 50).duty(int(a * 102 / 180 + 25))
def fire(ms):
    _pwm(6).duty(1023); _pwm(7).duty(0)
    _arm(ms)
print('cb-ready')
"""


class RawRepl:
    """最小 raw REPL 实现。`port` 只需要 write/read/in_waiting/reset_input_buffer。"""

    def __init__(self, port, timeout: float = 3.0):
        self.port = port
        self.timeout = timeout
        self._buf = b""  # 已读出但还没消费的字节（一次 read 可能越过标记）

    def _read_until(self, marker: bytes) -> bytes:
        """返回到 marker（含）为止的数据，多读的部分留给下一次。"""
        deadline = time.monotonic() + self.timeout
        while (i := self._buf.find(marker)) < 0:
            if time.monotonic() > deadline:
                raise TimeoutError(f"等待 {marker!r} 超时，已收到 {self._buf[-80:]!r}")
            chunk = self.port.read(max(1, self.port.in_waiting or 1))
            if chunk:
                self._buf += chunk
        end = i + len(marker)
        out, self._buf = self._buf[:end], self._buf[end:]
        return out

    def enter(self) -> None:
        self.port.write(b"\r\x03\x03")  # 打断正在运行的 main.py
        time.sleep(0.2)
        self.port.reset_input_buffer()
        self._buf = b""
        self.port.write(b"\x01")  # Ctrl-A 进入 raw REPL
        self._read_until(b"raw REPL; CTRL-B to exit\r\n>")

    def exec(self, code: str) -> str:
        self.port.write(code.encode("utf-8") + b"\x04")
        self._read_until(b"OK")
        out = self._read_until(b"\x04")[:-1]
        err = self._read_until(b"\x04")[:-1]
        self._read_until(b">")
        if err.strip():
            raise RuntimeError(err.decode("utf-8", "replace").strip())
        return out.decode("utf-8", "replace")

    def exit(self) -> None:
        self.port.write(b"\x02")  # Ctrl-B 回到普通 REPL


def find_port() -> str | None:
    if os.environ.get("CYBERBRICK_PORT"):
        return os.environ["CYBERBRICK_PORT"]
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    for p in ports:
        if p.vid == 0x303A:  # Espressif USB（ESP32-C3 内置 USB-JTAG/Serial）
            return p.device
    for p in ports:
        if any(k in (p.description or "") for k in ("CyberBrick", "USB Serial", "CP210")):
            return p.device
    return None


class SerialBackend:
    def __init__(self, port: str | None = None, baud: int = 115200):
        self.port_name = port
        self.baud = baud
        self._repl: RawRepl | None = None
        self._serial = None
        self._lock = threading.Lock()

    async def connect(self):
        await asyncio.to_thread(self._connect_sync)

    def _connect_sync(self):
        import serial

        name = self.port_name or find_port()
        if not name:
            raise RuntimeError("找不到 CyberBrick 串口，请用 --port 或环境变量 CYBERBRICK_PORT 指定")
        self._serial = serial.Serial(name, self.baud, timeout=0.1)
        self._repl = RawRepl(self._serial)
        self._repl.enter()
        out = self._repl.exec(BOOTSTRAP)
        if "cb-ready" not in out:
            raise RuntimeError(f"注入驱动失败: {out!r}")
        print(f"[serial-tank] 已连接 {name}")

    async def _exec(self, line: str):
        def run():
            with self._lock:
                return self._repl.exec(line)

        return await asyncio.to_thread(run)

    async def drive(self, left, right, failsafe_ms):
        await self._exec(f"drive({int(left)},{int(right)},{int(failsafe_ms)})")

    async def aim(self, angle):
        await self._exec(f"aim({int(angle)})")

    async def fire(self, pulse_ms):
        await self._exec(f"fire({int(pulse_ms)})")

    async def halt(self):
        await self._exec("halt()")

    async def close(self):
        if self._repl:
            try:
                await self.halt()
                self._repl.exit()  # 回到普通 REPL；按复位键即恢复原厂遥控程序
            finally:
                self._serial.close()
