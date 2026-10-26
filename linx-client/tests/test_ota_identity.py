"""OTA 上报和虚拟设备身份：用本地假 OTA 服务，不连真平台。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from linx_client import ota
from linx_client.identity import Identity, _random_mac, load_or_create


def test_random_mac_is_locally_administered_unicast():
    for _ in range(50):
        mac = _random_mac()
        first = int(mac.split(":")[0], 16)
        assert len(mac.split(":")) == 6
        assert first & 0x02  # 本地管理位
        assert not first & 0x01  # 单播


def test_identity_is_created_once_then_reused(tmp_path):
    path = tmp_path / ".linx_device.json"
    a = load_or_create(str(path))
    assert path.exists()
    b = load_or_create(str(path))
    assert a == b
    assert json.loads(path.read_text()) == {"mac": a.mac, "client_id": a.client_id}


class FakeOta:
    """按顺序返回预设的 OTA 应答，并记下收到的请求。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.requests.append((dict(self.headers), json.loads(body)))
                reply = fake.replies.pop(0) if len(fake.replies) > 1 else fake.replies[0]
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/ota/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


IDENT = Identity(mac="02:11:22:33:44:55", client_id="cid-1")


def test_report_sends_device_headers_and_body():
    fake = FakeOta([{"websocket": {"url": "ws://x/v1/ws/", "token": "t"}}])
    try:
        data = ota.report(IDENT, fake.url)
    finally:
        fake.close()
    assert data["websocket"]["token"] == "t"
    headers, body = fake.requests[0]
    assert headers["Device-Id"] == IDENT.mac
    assert headers["Client-Id"] == IDENT.client_id
    assert body["mac_address"] == IDENT.mac and body["uuid"] == IDENT.client_id


def test_report_raises_on_platform_error():
    fake = FakeOta([{"error": "设备被禁用"}])
    try:
        with pytest.raises(RuntimeError, match="设备被禁用"):
            ota.report(IDENT, fake.url)
    finally:
        fake.close()


def test_wait_for_binding_shows_code_once_then_returns_endpoint(capsys):
    unbound = {"activation": {"code": "123456"}}
    bound = {"websocket": {"url": "ws://x/v1/ws/", "token": "tok"}}
    fake = FakeOta([unbound, unbound, bound])
    try:
        ws = ota.wait_for_binding(IDENT, poll_s=0, url=fake.url)
    finally:
        fake.close()
    assert ws == bound["websocket"]
    assert len(fake.requests) == 3
    assert capsys.readouterr().out.count("123456") == 1  # 同一个激活码只提示一次
