"""摄像头取图 + 灵矽视觉分析（self.camera.take_photo）。

取图来源（--camera）：
  数字，如 0       电脑摄像头（OpenCV；macOS 上 iPhone 连续互通相机也会作为一个序号出现）
  http(s)://...    返回一张 JPEG 的快照地址，例如车载 XIAO ESP32S3 Sense 的 /capture
  其他             图片文件路径（调试用）

视觉接口由平台在 MCP initialize 的 capabilities.vision 下发（智控台「对话与视觉 → 视觉分析」开启后才有）。
调用方式（2026-10-25 实测，见 docs/HANDOFF.md）：POST multipart，字段 question + file（camera.jpg，image/jpeg），
请求头 Authorization: Bearer <token>、Device-Id、Client-Id；返回
{"action":"RESPONSE","response":"<分析文字>","success":true}。
"""

import asyncio
import json
import threading
import time
import urllib.request
import uuid
from typing import Protocol

from .identity import Identity

MAX_WIDTH = 640  # 缩小再上传：分析够用，上传快
# 不走系统代理：本地代理（如 Clash）会掐断到七牛国内节点的连接，局域网的车载摄像头也不该走代理
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Camera(Protocol):
    def snapshot(self) -> bytes:
        """返回一张 JPEG。"""

    def frame(self):
        """返回一帧 BGR 图像（numpy 数组，标记跟踪用；需要 OpenCV）。"""


def _cv2():
    try:
        import cv2
    except ImportError as e:
        raise RuntimeError("需要 OpenCV：pip install opencv-python") from e
    return cv2


def _encode(frame) -> bytes:
    cv2 = _cv2()
    h, w = frame.shape[:2]
    if w > MAX_WIDTH:
        frame = cv2.resize(frame, (MAX_WIDTH, int(h * MAX_WIDTH / w)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise RuntimeError("JPEG 编码失败")
    return buf.tobytes()


class OpenCVCamera:
    """电脑摄像头。第一次取图时打开，之后一直开着、后台线程不停地读，取图直接拿最新一帧：
    标记跟踪每秒要取十几帧，每次都重新打开太慢；刚打开的头一秒画面常常偏暗甚至全黑，也只在开头等一次。"""

    WARMUP_S = 1.0
    STALE_S = 2.0  # 最新一帧超过这么久没更新，说明摄像头断了（手机连续互通相机断开等）

    def __init__(self, index: int = 0):
        self.index = index
        self._cap = None
        self._latest = None  # (frame, time)
        self._lock = threading.Lock()

    def _ensure(self):
        with self._lock:
            if self._cap is not None:
                return
            cv2 = _cv2()
            cap = cv2.VideoCapture(self.index)
            if not cap.isOpened():
                raise RuntimeError(f"打不开摄像头 {self.index}（macOS 需在「隐私与安全性 → 摄像头」里允许终端）")
            # 默认常常只给 640×480：车顶标记斜着拍时只剩十几个像素，解不出来（2026-10-25 用户实测
            # “要么看不到 0 号、要么看不到 1 号”）。要最高 1080p，拿到多少用多少；上传视觉模型前会再缩小
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"📷 摄像头 {self.index}：{w}×{h}" + ("（分辨率偏低，标记要拍近一点）" if w < 1280 else ""))
            self._cap = cap
            threading.Thread(target=self._reader, daemon=True).start()
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.WARMUP_S or self._latest is None:
            if time.monotonic() - t0 > 5:
                raise RuntimeError(f"摄像头 {self.index} 读不到画面")
            time.sleep(0.05)

    def _reader(self):
        while self._cap is not None:
            ok, frame = self._cap.read()
            if ok:
                self._latest = (frame, time.monotonic())
            else:
                time.sleep(0.05)

    def frame(self):
        self._ensure()
        frame, t = self._latest
        if time.monotonic() - t > self.STALE_S:
            raise RuntimeError(f"摄像头 {self.index} 已经 {time.monotonic() - t:.0f} 秒没有新画面，可能断开了")
        return frame

    def snapshot(self) -> bytes:
        frame = self.frame()
        if frame.mean() < 8:
            raise RuntimeError(f"摄像头 {self.index} 拍到的是全黑画面：镜头被挡住了，或者没有摄像头权限")
        return _encode(frame)

    def close(self):
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()


class HttpSnapshotCamera:
    def __init__(self, url: str, timeout: float = 5):
        self.url = url
        self.timeout = timeout

    def snapshot(self) -> bytes:
        with _NO_PROXY.open(self.url, timeout=self.timeout) as resp:
            data = resp.read()
        if not data.startswith(b"\xff\xd8"):
            raise RuntimeError(f"{self.url} 返回的不是 JPEG")
        return data

    def frame(self):
        import numpy as np

        img = _cv2().imdecode(np.frombuffer(self.snapshot(), np.uint8), 1)
        if img is None:
            raise RuntimeError(f"{self.url} 的图片解不开")
        return img


class FileCamera:
    def __init__(self, path: str):
        self.path = path

    def snapshot(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()

    def frame(self):
        img = _cv2().imread(self.path)
        if img is None:
            raise RuntimeError(f"读不了图片 {self.path}")
        return img


def make_camera(spec: str | None) -> Camera | None:
    if not spec:
        return None
    if spec.isdigit():
        return OpenCVCamera(int(spec))
    if spec.startswith(("http://", "https://")):
        return HttpSnapshotCamera(spec)
    return FileCamera(spec)


def _multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def explain(vision: dict, ident: Identity, jpeg: bytes, question: str, timeout: float = 15) -> str:
    """把照片发给平台视觉接口，返回分析文字。"""
    body, ctype = _multipart({"question": question}, "file", "camera.jpg", jpeg)
    headers = {"Content-Type": ctype, "Device-Id": ident.mac, "Client-Id": ident.client_id}
    if vision.get("token"):
        headers["Authorization"] = f"Bearer {vision['token']}"
    req = urllib.request.Request(vision["url"], data=body, method="POST", headers=headers)
    with _NO_PROXY.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"视觉接口返回的不是 JSON：{raw[:200]}") from e
    if not data.get("success"):
        raise RuntimeError(f"视觉分析失败：{data.get('message') or raw[:200]}")
    return str(data.get("response", ""))


SNAPSHOT_TIMEOUT_S = 10


class PhotoTool:
    """self.camera.take_photo 的实现。最近一张照片留在 last_jpeg，方便网页控制台或调试查看。"""

    def __init__(self, camera: Camera, mcp, ident: Identity):
        self.camera = camera
        self.mcp = mcp
        self.ident = ident
        self.last_jpeg: bytes | None = None
        self.last_answer: str | None = None
        self.on_photo = None  # 网页控制台订阅：(jpeg, question, answer)

    async def __call__(self, question: str) -> str:
        answer = await self.ask(question)
        # 与小智设备一致：把视觉接口的结果原样交给平台
        return json.dumps({"success": True, "action": "RESPONSE", "response": answer}, ensure_ascii=False)

    async def ask(self, question: str) -> str:
        """拍一张并返回视觉模型的回答（闭环寻的也用它）。"""
        vision = self.mcp.vision
        if vision is None:
            raise RuntimeError("平台没有下发视觉接口：请在智控台「对话与视觉 → 视觉分析」开启视觉模型，保存后重连")
        t0 = time.monotonic()
        try:
            jpeg = await asyncio.wait_for(asyncio.to_thread(self.camera.snapshot), SNAPSHOT_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise RuntimeError(f"拍照超过 {SNAPSHOT_TIMEOUT_S} 秒没拿到画面：摄像头可能被别的程序占用，"
                               "或手机连续互通相机断开了") from None
        answer = await asyncio.to_thread(explain, vision, self.ident, jpeg, question)
        self.last_jpeg, self.last_answer = jpeg, answer
        print(f"📷 {question} → {answer}（{time.monotonic() - t0:.1f}s）")
        if self.on_photo:
            self.on_photo(jpeg, question, answer)
        return answer
