"""通过服务器本机的受控连接读取摄像头，供云端灵矽识图。"""

import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .camera import PhotoTool
from .tools import register_camera_tools

MAX_JPEG_BYTES = 512 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class CloudSnapshotCamera:
    """仅从明确指定的服务器回环地址取 JPEG；不能由网页指定来源。"""

    def __init__(self, url: str, timeout: float = 3):
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise ValueError("云端摄像头地址不合法") from exc
        if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or not port
                or parts.username or parts.password or parts.path != "/capture"
                or parts.query or parts.fragment):
            raise ValueError("云端摄像头地址须为 http://127.0.0.1:端口/capture")
        self.url = url
        self.timeout = timeout

    def snapshot(self) -> bytes:
        request = urllib.request.Request(self.url, headers={"Accept": "image/jpeg"})
        try:
            with _LOOPBACK_OPENER.open(request, timeout=self.timeout) as response:
                data = response.read(MAX_JPEG_BYTES + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError("摄像头连接不可用") from exc
        if len(data) > MAX_JPEG_BYTES or not (data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")):
            raise RuntimeError("摄像头返回的图片无效或过大")
        return data


def register_cloud_camera(mcp, ident, camera: CloudSnapshotCamera) -> PhotoTool:
    """云端只注册拍照识图，不启用视觉自动驾驶。"""
    photo = PhotoTool(camera, mcp, ident)
    register_camera_tools(mcp, photo, view="front")
    return photo
