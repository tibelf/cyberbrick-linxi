"""经 Wi-Fi 连车上的 MicroPython WebREPL（端口 8266），装车后没有 USB 时用来维护。

WebReplPort 提供和 pyserial 一样的 write/read/in_waiting/reset_input_buffer，
所以 RawRepl、put_file 等可以原样复用。终端数据走 WebSocket 文本帧。
"""

import time

from websockets.sync.client import connect

WEBREPL_PORT = 8266


class WebReplPort:
    def __init__(self, host: str, password: str, port: int = WEBREPL_PORT, timeout: float = 5.0):
        self.ws = connect(f"ws://{host}:{port}/", open_timeout=timeout, proxy=None)
        self._buf = b""
        self._wait_for(b"Password: ", timeout)
        self.ws.send(password + "\r")
        self._wait_for(b"WebREPL connected", timeout)

    def _pull(self, timeout: float) -> None:
        try:
            msg = self.ws.recv(timeout=timeout)
        except TimeoutError:
            return
        self._buf += msg if isinstance(msg, bytes) else msg.encode("utf-8")

    def _wait_for(self, marker: bytes, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while marker not in self._buf:
            if time.monotonic() > deadline:
                raise ConnectionError(f"WebREPL 没有出现 {marker!r}（密码错误？），已收到 {self._buf[-80:]!r}")
            self._pull(0.2)
        self._buf = b""

    @property
    def in_waiting(self) -> int:
        if not self._buf:
            self._pull(0.05)
        return len(self._buf)

    def read(self, n: int) -> bytes:
        if not self._buf:
            self._pull(0.1)
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def write(self, data: bytes) -> None:
        self.ws.send(data.decode("utf-8"))

    def reset_input_buffer(self) -> None:
        self._pull(0.1)
        self._buf = b""

    def close(self) -> None:
        self.ws.close()
