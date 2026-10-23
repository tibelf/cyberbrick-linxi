from linx_client.tank.serial_repl import RawRepl


class FakePort:
    """模拟 MicroPython raw REPL 的串口行为。"""

    def __init__(self, stdout=b"", stderr=b""):
        self.rx = b""
        self.written = b""
        self.stdout, self.stderr = stdout, stderr

    @property
    def in_waiting(self):
        return len(self.rx)

    def read(self, n):
        out, self.rx = self.rx[:n], self.rx[n:]
        return out

    def reset_input_buffer(self):
        self.rx = b""

    def write(self, data):
        self.written += data
        if data == b"\x01":
            self.rx += b"raw REPL; CTRL-B to exit\r\n>"
        elif data.endswith(b"\x04"):
            self.rx += b"OK" + self.stdout + b"\x04" + self.stderr + b"\x04>"


def test_exec_returns_stdout():
    port = FakePort(stdout=b"cb-ready\r\n")
    repl = RawRepl(port, timeout=0.5)
    repl.enter()
    assert "cb-ready" in repl.exec("print('cb-ready')")
    assert port.written.endswith(b"print('cb-ready')\x04")


def test_exec_raises_on_stderr():
    port = FakePort(stderr=b"Traceback: NameError")
    repl = RawRepl(port, timeout=0.5)
    repl.enter()
    try:
        repl.exec("boom()")
    except RuntimeError as e:
        assert "NameError" in str(e)
    else:
        raise AssertionError("应该抛出异常")
