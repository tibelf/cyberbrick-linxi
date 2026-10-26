"""Wi-Fi 维护通道：用本地 TCP 服务模拟车端 _admin_session 的协议。"""

import socket
import threading

import pytest

from linx_client.tank.netadmin import NetRepl
from linx_client.tank.serial_repl import put_file, remove_file

PASSWORD = "secret88"


class FakeCar:
    """先收一行密码，之后每次收 "<字节数>\\n<代码>"，回 "ok|err <字节数>\\n<输出>"。"""

    def __init__(self, run=lambda code: ("ok", "")):
        self.run = run
        self.codes = []
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        f = conn.makefile("rwb")
        if f.readline().strip().decode() != PASSWORD:
            f.write(b"err 0\n")
            f.flush()
            conn.close()
            return
        f.write(b"ok 0\n")
        f.flush()
        while line := f.readline():
            code = f.read(int(line)).decode()
            self.codes.append(code)
            if "machine.reset()" in code:  # 车重启：连接直接断开，不回应答
                conn.close()
                return
            status, out = self.run(code)
            data = out.encode()
            f.write(f"{status} {len(data)}\n".encode() + data)
            f.flush()
        conn.close()

    def close(self):
        self.srv.close()


def test_exec_returns_output():
    car = FakeCar(lambda code: ("ok", "42\n"))
    repl = NetRepl("127.0.0.1", PASSWORD, port=car.port, timeout=2)
    try:
        repl.enter()
        assert repl.exec("print(42)") == "42\n"
        repl.exit()
    finally:
        repl.close()
        car.close()
    assert car.codes == ["print(42)"]


def test_wrong_password_is_rejected():
    car = FakeCar()
    try:
        with pytest.raises(ConnectionError, match="维护密码错误"):
            NetRepl("127.0.0.1", "wrong-pw", port=car.port, timeout=2)
    finally:
        car.close()


def test_exec_error_is_raised():
    car = FakeCar(lambda code: ("err", "NameError: boom\n"))
    repl = NetRepl("127.0.0.1", PASSWORD, port=car.port, timeout=2)
    try:
        with pytest.raises(RuntimeError, match="NameError"):
            repl.exec("boom()")
    finally:
        repl.close()
        car.close()


def test_put_file_and_reset_over_wifi():
    car = FakeCar()
    repl = NetRepl("127.0.0.1", PASSWORD, port=car.port, timeout=2)
    try:
        put_file(repl, "/app/x.py", b"a" * 300, chunk=256)
        remove_file(repl, "/wifi.json")
        repl.reset()  # 车断开连接也不报错
    finally:
        repl.close()
        car.close()
    assert car.codes[0].endswith("_f = open('/app/x.py', 'wb')")
    assert sum("a2b_base64" in c for c in car.codes) == 2  # 300 字节分两块
    assert "_f.close()" in car.codes[3]
    assert "os.remove('/wifi.json')" in car.codes[4]
    assert "machine.reset()" in car.codes[5]
