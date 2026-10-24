"""经 Wi-Fi 连车上的维护通道（TCP 8266，车端见 firmware/car/wifi_main.py 的 _admin_session）。

装车后没有 USB 时用它更新程序、看状态。接口和 RawRepl 一样（enter/exec/exit），
所以 put_file、remove_file 可以原样复用。
"""

import socket

ADMIN_PORT = 8266


class NetRepl:
    def __init__(self, host: str, password: str, port: int = ADMIN_PORT, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile("rwb")
        self.f.write(password.encode() + b"\n")
        self.f.flush()
        if self._reply()[0] != "ok":
            raise ConnectionError("维护密码错误")

    def _reply(self) -> tuple[str, str]:
        hdr = self.f.readline().decode().split()
        if len(hdr) != 2:
            raise ConnectionError("维护通道连接中断")
        status, n = hdr[0], int(hdr[1])
        return status, self.f.read(n).decode("utf-8", "replace")

    def enter(self) -> None:
        pass

    def exit(self) -> None:
        pass

    def exec(self, code: str) -> str:
        data = code.encode()
        self.f.write(f"{len(data)}\n".encode() + data)
        self.f.flush()
        status, out = self._reply()
        if status != "ok":
            raise RuntimeError(out.strip())
        return out

    def reset(self) -> None:
        """重启车；连接会随之断开。"""
        try:
            self.exec("import machine\nmachine.reset()")
        except (OSError, ConnectionError):
            pass

    def close(self) -> None:
        self.sock.close()
