"""拍照工具：本地假视觉接口校验请求格式（按 2026-10-25 实测的平台格式），再走一遍 MCP 调用。"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from linx_client.camera import FileCamera, HttpSnapshotCamera, OpenCVCamera, PhotoTool, explain, make_camera
from linx_client.identity import Identity
from linx_client.mcp import McpServer
from linx_client.tools import register_camera_tools

JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"
IDENT = Identity(mac="02:00:00:00:00:01", client_id="cid")


@pytest.fixture
def vision_server():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.update(headers=dict(self.headers), body=body)
            out = json.dumps({"action": "RESPONSE", "response": "红色杯子在画面左边，偏 20 度", "success": True})
            self._reply("application/json", out.encode())

        def do_GET(self):  # 模拟车载摄像头的快照地址
            self._reply("image/jpeg", JPEG)

        def _reply(self, ctype, data):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def test_make_camera():
    assert isinstance(make_camera("0"), OpenCVCamera)
    assert isinstance(make_camera("http://192.168.1.9/capture"), HttpSnapshotCamera)
    assert isinstance(make_camera("photo.jpg"), FileCamera)
    assert make_camera(None) is None


def test_explain_request_format(vision_server):
    url, seen = vision_server
    answer = explain({"url": url + "/mcp/vision/explain2", "token": "vt"}, IDENT, JPEG, "杯子在哪？")
    assert answer == "红色杯子在画面左边，偏 20 度"
    h = seen["headers"]
    assert h["Authorization"] == "Bearer vt" and h["Device-Id"] == IDENT.mac and h["Client-Id"] == "cid"
    body = seen["body"]
    assert 'name="question"'.encode() in body and "杯子在哪？".encode() in body
    assert b'name="file"; filename="camera.jpg"' in body and b"Content-Type: image/jpeg" in body
    assert JPEG in body


def test_http_snapshot_camera(vision_server):
    url, _ = vision_server
    assert HttpSnapshotCamera(url + "/capture").snapshot() == JPEG


def _call(mcp, method, params=None, id=1):
    return asyncio.run(mcp.handle({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": id}))


def test_take_photo_via_mcp(vision_server, tmp_path):
    url, _ = vision_server
    img = tmp_path / "p.jpg"
    img.write_bytes(JPEG)
    mcp = McpServer()
    photo = PhotoTool(FileCamera(str(img)), mcp, IDENT)
    register_camera_tools(mcp, photo)

    # 平台没下发视觉接口：工具报错但连接不断
    r = _call(mcp, "tools/call", {"name": "self.camera.take_photo", "arguments": {"question": "看看"}})
    assert r["result"]["isError"] is True and "视觉" in r["result"]["content"][0]["text"]

    _call(mcp, "initialize", {"capabilities": {"vision": {"url": url + "/explain2", "token": "vt"}}})
    assert mcp.vision["token"] == "vt"
    r = _call(mcp, "tools/call", {"name": "self.camera.take_photo", "arguments": {"question": "杯子在哪？"}}, id=2)
    assert r["result"]["isError"] is False
    out = json.loads(r["result"]["content"][0]["text"])
    assert out["success"] is True and "左边" in out["response"]
    assert photo.last_jpeg == JPEG


def test_explain_ignores_system_proxy(vision_server, monkeypatch):
    """开着本地代理（Clash）时，到七牛的上传和局域网快照都不能走代理。"""
    url, _ = vision_server
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")  # 一个连不上的代理
    monkeypatch.setenv("no_proxy", "")
    assert explain({"url": url + "/explain2", "token": "vt"}, IDENT, JPEG, "看看") == "红色杯子在画面左边，偏 20 度"
    assert HttpSnapshotCamera(url + "/capture").snapshot() == JPEG


def test_on_photo_callback(vision_server, tmp_path):
    url, _ = vision_server
    img = tmp_path / "p.jpg"
    img.write_bytes(JPEG)
    mcp = McpServer()
    photo = PhotoTool(FileCamera(str(img)), mcp, IDENT)
    got = []
    photo.on_photo = lambda j, q, a: got.append((j, q, a))
    register_camera_tools(mcp, photo)
    _call(mcp, "initialize", {"capabilities": {"vision": {"url": url + "/explain2"}}})
    _call(mcp, "tools/call", {"name": "self.camera.take_photo", "arguments": {"question": "杯子在哪？"}}, id=2)
    assert got == [(JPEG, "杯子在哪？", "红色杯子在画面左边，偏 20 度")]


def test_camera_view_changes_tool_description():
    from linx_client.tools import CAMERA_HOWTO

    for view, key in (("overhead", "12 点钟"), ("front", "偏离中心")):
        mcp = McpServer()
        register_camera_tools(mcp, lambda question: "", view=view)
        desc = mcp.tools["self.camera.take_photo"].description
        assert CAMERA_HOWTO[view] in desc and key in desc and "开火" in desc


def test_make_camera_with_size():
    cam = make_camera("1:1920x1440")
    assert isinstance(cam, OpenCVCamera) and cam.index == 1 and cam.size == (1920, 1440)


def test_pick_size_prefers_full_4_3_frame():
    """要 16:9 会裁掉上下（用户反馈视角太小）：摄像头支持 4:3 大分辨率时优先用它。"""
    pytest.importorskip("cv2")
    import cv2

    class FakeCap:
        def __init__(self, supported):
            self.supported, self.cur, self.want = supported, supported[-1], [0, 0]

        def set(self, prop, v):
            self.want[0 if prop == cv2.CAP_PROP_FRAME_WIDTH else 1] = v
            fit = [s for s in self.supported if s[0] <= self.want[0] and s[1] <= self.want[1]]
            self.cur = max(fit, key=lambda s: s[0] * s[1]) if fit else self.supported[-1]

        def get(self, prop):
            return self.cur[0 if prop == cv2.CAP_PROP_FRAME_WIDTH else 1]

    iphone = FakeCap([(1920, 1440), (1920, 1080), (1280, 720), (640, 480)])
    assert OpenCVCamera(0)._pick_size(iphone) == (1920, 1440)
    webcam = FakeCap([(1920, 1080), (1280, 720), (640, 480)])
    assert OpenCVCamera(0)._pick_size(webcam) == (1920, 1080)
    old = FakeCap([(640, 480)])
    assert OpenCVCamera(0)._pick_size(old) == (640, 480)
