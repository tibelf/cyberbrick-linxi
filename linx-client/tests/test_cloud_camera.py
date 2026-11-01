"""验证云端摄像头限于回环地址，并能走灵矽 MCP 识图链路。"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from linx_client.cloud_camera import MAX_JPEG_BYTES, CloudSnapshotCamera, register_cloud_camera
from linx_client.identity import Identity
from linx_client.mcp import McpServer

JPEG = b"\xff\xd8\xff\xe0fake-camera\xff\xd9"


@pytest.fixture
def camera_server():
    seen = {"paths": [], "mode": "normal", "uploads": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen["paths"].append(self.path)
            if seen["mode"] == "redirect":
                self.send_response(302)
                self.send_header("Location", "/private")
                self.end_headers()
                return
            body = JPEG if seen["mode"] == "normal" else b"\xff\xd8" + b"x" * MAX_JPEG_BYTES
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            seen["uploads"].append(self.rfile.read(int(self.headers["Content-Length"])))
            body = json.dumps({"success": True, "response": "白色物体"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", seen
    server.shutdown()
    server.server_close()
    thread.join()


def test_cloud_camera_to_vision_via_mcp(camera_server):
    base, seen = camera_server
    camera = CloudSnapshotCamera(base + "/capture")
    mcp = McpServer()
    register_cloud_camera(mcp, Identity("02:00:00:00:00:01", "fake-client"), camera)

    async def run():
        await mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"capabilities": {"vision": {"url": base + "/vision"}}}})
        return await mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "self.camera.take_photo", "arguments": {"question": "前面有什么？"}}})

    response = asyncio.run(run())
    assert not response["result"]["isError"]
    assert "白色物体" in response["result"]["content"][0]["text"]
    assert seen["paths"] == ["/capture"]
    assert JPEG in seen["uploads"][0]
    assert "前面有什么？".encode() in seen["uploads"][0]
    assert "self.tank.seek" not in mcp.tools


@pytest.mark.parametrize("url", [
    "http://192.168.1.3:80/capture", "http://localhost:80/capture", "https://127.0.0.1:80/capture",
    "http://127.0.0.1/capture", "http://u:p@127.0.0.1:80/capture", "http://127.0.0.1:80/capture?q=1",
    "http://127.0.0.1:80/private", "http://127.0.0.1:bad/capture",
])
def test_cloud_camera_rejects_untrusted_sources(url):
    with pytest.raises(ValueError):
        CloudSnapshotCamera(url)


def test_cloud_camera_rejects_redirect_and_oversized_frame(camera_server):
    base, seen = camera_server
    camera = CloudSnapshotCamera(base + "/capture")
    seen["mode"] = "redirect"
    with pytest.raises(RuntimeError, match="连接不可用"):
        camera.snapshot()
    assert seen["paths"] == ["/capture"]  # 不跟随重定向访问其他地址
    seen["mode"] = "oversized"
    with pytest.raises(RuntimeError, match="无效或过大"):
        camera.snapshot()
