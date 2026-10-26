"""摄像头取图和视觉接口的其余分支：电脑摄像头（假 VideoCapture）、解码、缩图、视觉接口的异常应答。"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from linx_client import camera  # noqa: E402
from linx_client.camera import FileCamera, HttpSnapshotCamera, OpenCVCamera, PhotoTool, _encode, explain  # noqa: E402
from linx_client.identity import Identity  # noqa: E402
from linx_client.mcp import McpServer  # noqa: E402

IDENT = Identity(mac="02:00:00:00:00:01", client_id="cid")


def _jpeg(w=320, h=240, value=128) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), value, np.uint8))
    assert ok
    return buf.tobytes()


class FakeCapture:
    """假的 cv2.VideoCapture：只支持 1920×1080 以下，按设置的分辨率出纯色画面。"""

    def __init__(self, index, value=128, opened=True):
        self.value, self.opened = value, opened
        self.w, self.h = 640, 480
        self.released = False

    def isOpened(self):
        return self.opened

    def set(self, prop, v):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            self.w = min(int(v), 1920)
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self.h = min(int(v), 1080)

    def get(self, prop):
        return self.w if prop == cv2.CAP_PROP_FRAME_WIDTH else self.h

    def read(self):
        return True, np.full((self.h, self.w, 3), self.value, np.uint8)

    def release(self):
        self.released = True


def _opencv_camera(monkeypatch, **cap_kw):
    caps = []
    monkeypatch.setattr(cv2, "VideoCapture", lambda i: caps.append(FakeCapture(i, **cap_kw)) or caps[-1])
    cam = OpenCVCamera(0)
    cam.WARMUP_S = 0
    return cam, caps


def test_opencv_camera_picks_tallest_wide_size_and_encodes(monkeypatch):
    cam, caps = _opencv_camera(monkeypatch)
    jpeg = cam.snapshot()
    assert jpeg.startswith(b"\xff\xd8")
    assert (caps[0].w, caps[0].h) == (1920, 1080)  # 摄像头给不了 4:3，退到 1080p
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), 1)
    assert img.shape[1] == camera.MAX_WIDTH  # 上传前缩小
    cam.close()
    assert caps[0].released


def test_opencv_camera_rejects_black_frame(monkeypatch):
    cam, _ = _opencv_camera(monkeypatch, value=0)
    with pytest.raises(RuntimeError, match="全黑"):
        cam.snapshot()
    cam.close()


def test_opencv_camera_not_opened(monkeypatch):
    cam, _ = _opencv_camera(monkeypatch, opened=False)
    with pytest.raises(RuntimeError, match="打不开摄像头"):
        cam.frame()


def test_opencv_camera_stale_frame(monkeypatch):
    cam, caps = _opencv_camera(monkeypatch)
    cam.frame()
    caps[0].read = lambda: (False, None)  # 摄像头断了：读不到新帧
    frame, _t = cam._latest
    cam._latest = (frame, 0.0)
    with pytest.raises(RuntimeError, match="没有新画面"):
        cam.frame()
    cam.close()


def test_file_camera_frame(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(_jpeg())
    assert FileCamera(str(f)).frame().shape == (240, 320, 3)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"nope")
    with pytest.raises(RuntimeError, match="读不了图片"):
        FileCamera(str(bad)).frame()


class Server:
    def __init__(self, handler):
        self.srv = HTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def _handler(body: bytes, ctype="image/jpeg"):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.do_GET()

    return H


def test_http_camera_frame_and_non_jpeg():
    good = Server(_handler(_jpeg()))
    html = Server(_handler(b"<html>login</html>", "text/html"))
    try:
        assert HttpSnapshotCamera(good.url + "/shot.jpg").frame().shape == (240, 320, 3)
        with pytest.raises(RuntimeError, match="不是 JPEG"):
            HttpSnapshotCamera(html.url + "/shot.jpg").snapshot()
    finally:
        good.close()
        html.close()


@pytest.mark.parametrize("reply, message", [
    (b"<html>502</html>", "不是 JSON"),
    (json.dumps({"success": False, "message": "额度用完"}).encode(), "额度用完"),
])
def test_explain_bad_replies(reply, message):
    srv = Server(_handler(reply, "application/json"))
    try:
        with pytest.raises(RuntimeError, match=message):
            explain({"url": srv.url + "/explain2"}, IDENT, b"\xff\xd8x", "看看")
    finally:
        srv.close()


def test_photo_tool_needs_vision_and_times_out(monkeypatch):
    mcp = McpServer()
    tool = PhotoTool(FileCamera("/nonexistent.jpg"), mcp, IDENT)
    with pytest.raises(RuntimeError, match="视觉分析"):
        asyncio.run(tool.ask("看看"))

    class Slow:
        def snapshot(self):
            import time

            time.sleep(0.5)
            return b""

    mcp.client_capabilities = {"vision": {"url": "http://127.0.0.1:9/"}}
    monkeypatch.setattr(camera, "SNAPSHOT_TIMEOUT_S", 0.05)
    with pytest.raises(RuntimeError, match="没拿到画面"):
        asyncio.run(PhotoTool(Slow(), mcp, IDENT).ask("看看"))


def test_encode_keeps_small_frames():
    small = np.full((100, 200, 3), 50, np.uint8)
    img = cv2.imdecode(np.frombuffer(_encode(small), np.uint8), 1)
    assert img.shape[:2] == (100, 200)
