"""通过局域网 UDP 控制运行 Wi-Fi 模式的车（车上程序见 firmware/car/wifi_main.py）。

协议是一行 ASCII 指令一个包，车回 ok / pong / err <原因>。语义与 serial 后端一致：
只设状态并带 failsafe_ms，时长由 executor 控制。UDP 可能丢包，每条指令等应答，超时重发。
"""

import asyncio
import os
import socket
import threading
from pathlib import Path

PORT = 4210


def _lan_ip() -> str:
    """默认路由所在网卡的 IP（UDP connect 不发包）。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


CACHE_FILE = Path(__file__).resolve().parents[2] / ".car_host"  # 上次找到的车的 IP（已 gitignore）


def discover(port: int = PORT, timeout: float = 0.6) -> str | None:
    """找局域网里 Wi-Fi 模式的车，返回 IP。

    ESP32 开着 Wi-Fi 省电时经常漏收广播，所以按可靠程度依次尝试：
    上次找到的 IP → 广播（发 3 轮）→ 把本网段 254 个地址逐个单播问一遍。
    绑定默认网卡：电脑上有虚拟网卡（Docker/虚拟机的 bridge）时，广播可能从别的网卡发出去。
    网段按 /24 估算，家用路由器基本都是。
    """
    ip = _lan_ip()
    base = ip.rsplit(".", 1)[0]
    cached = CACHE_FILE.read_text().strip() if CACHE_FILE.exists() else None
    rounds = [[cached]] if cached else []
    rounds += [[base + ".255", "255.255.255.255"]] * 3
    rounds.append([f"{base}.{i}" for i in range(1, 255)])

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        s.bind((ip, 0))
        for targets in rounds:
            for dst in targets:
                try:
                    s.sendto(b"ping", (dst, port))
                except OSError:
                    pass  # 个别地址发不出去（比如网关拒收）不影响其他
            s.settimeout(timeout)
            try:
                while True:
                    data, addr = s.recvfrom(64)
                    if data == b"pong":
                        CACHE_FILE.write_text(addr[0])
                        return addr[0]
            except socket.timeout:
                continue
    except OSError:
        return None
    finally:
        s.close()
    return None


class UdpBackend:
    def __init__(self, host: str | None = None, port: int = PORT, timeout: float = 0.3, retries: int = 3):
        self.host = host or os.environ.get("CYBERBRICK_HOST")
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    async def connect(self):
        await asyncio.to_thread(self._connect_sync)

    def _connect_sync(self):
        if not self.host:
            self.host = discover(self.port)
            if not self.host:
                raise RuntimeError("局域网里找不到 Wi-Fi 模式的车，请确认车已开机，或用 --host 指定 IP")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(self.timeout)
        self._send_sync("ping")
        print(f"[udp-tank] 已连接 {self.host}:{self.port}")

    def _send_sync(self, line: str) -> str:
        with self._lock:
            for _ in range(self.retries):
                self._sock.sendto(line.encode(), (self.host, self.port))
                try:
                    data, _ = self._sock.recvfrom(128)
                except socket.timeout:
                    continue
                reply = data.decode("utf-8", "replace")
                if reply.startswith("err"):
                    raise RuntimeError(f"车拒绝指令 {line!r}：{reply}")
                return reply
        raise TimeoutError(f"车没有应答：{line!r}")

    async def _send(self, line: str):
        return await asyncio.to_thread(self._send_sync, line)

    async def drive(self, left, right, failsafe_ms):
        await self._send(f"drive {int(left)} {int(right)} {int(failsafe_ms)}")

    async def turret(self, speed, failsafe_ms):
        await self._send(f"turret {int(speed)} {int(failsafe_ms)}")

    async def fire(self, pulse_ms):
        await self._send(f"fire {int(pulse_ms)}")

    async def halt(self):
        await self._send("halt")

    async def close(self):
        if self._sock:
            try:
                await self.halt()
            finally:
                self._sock.close()
