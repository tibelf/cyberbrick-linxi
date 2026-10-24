"""通过局域网 UDP 控制运行 Wi-Fi 模式的车（车上程序见 firmware/car/wifi_main.py）。

协议是一行 ASCII 指令一个包，车回 ok / pong / err <原因>。语义与 serial 后端一致：
只设状态并带 failsafe_ms，时长由 executor 控制。UDP 可能丢包，每条指令等应答，超时重发。
"""

import asyncio
import os
import socket
import threading

PORT = 4210


def discover(port: int = PORT, timeout: float = 1.5) -> str | None:
    """广播 ping，返回第一台应答的车的 IP。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    try:
        s.sendto(b"ping", ("255.255.255.255", port))
        while True:
            data, addr = s.recvfrom(64)
            if data == b"pong":
                return addr[0]
    except OSError:
        return None
    finally:
        s.close()


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
