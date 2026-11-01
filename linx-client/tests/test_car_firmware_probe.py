"""检查车端探测使用的 WebSocket 握手和数据帧。"""

import base64
import binascii
import hashlib
import importlib.util
import io
import sys
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setitem(sys.modules, "ubinascii", binascii)
    path = Path(__file__).resolve().parents[2] / "firmware/car/cloud_probe.py"
    spec = importlib.util.spec_from_file_location("car_firmware_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Stream:
    def __init__(self, received=b""):
        self.received = io.BytesIO(received)
        self.sent = bytearray()

    def read(self, n):
        return self.received.read(n)

    def readline(self):
        return self.received.readline()

    def write(self, data):
        # 即使底层一次只写一部分，也必须将整帧送完。
        count = min(len(data), 3)
        self.sent.extend(data[:count])
        return count


def test_upgrade_checks_server_accept(probe, monkeypatch):
    monkeypatch.setattr(probe.os, "urandom", lambda n: bytes(range(n)))
    key = base64.b64encode(bytes(range(16)))
    accept = base64.b64encode(hashlib.sha1(key + probe.GUID).digest())
    stream = Stream(b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
    probe._websocket_upgrade(stream)
    assert b"GET /device HTTP/1.1\r\n" in stream.sent
    assert b"Sec-WebSocket-Key: " + key in stream.sent
    with pytest.raises(ValueError, match="校验失败"):
        probe._websocket_upgrade(Stream(b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: bad\r\n\r\n"))


def test_frames_mask_client_data_and_reply_to_ping(probe, monkeypatch):
    monkeypatch.setattr(probe.os, "urandom", lambda n: b"abcd"[:n])
    message = b'{"type":"pong","id":1}'
    stream = Stream(b"\x89\x02hi" + bytes([0x81, len(message)]) + message)
    assert probe._read_message(stream) == {"type": "pong", "id": 1}
    assert stream.sent[:6] == b"\x8a\x82abcd"
    assert bytes(stream.sent[6 + i] ^ b"abcd"[i % 4] for i in range(2)) == b"hi"


def test_rejects_masked_fragmented_and_oversized_server_frames(probe):
    for frame in (b"\x81\x80", b"\x01\x00", b"\x81\x7e\x02\x01", b"\x81\x7f"):
        with pytest.raises(ValueError):
            probe._read_frame(Stream(frame))
