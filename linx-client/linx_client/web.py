"""网页控制台：`run --web` 时在 http://localhost:8080 提供页面。

页面只是界面：连灵矽、收发音频、控车仍由本进程完成。页面通过 /ws 收事件（对话、工具调用、
车的状态），发操作（急停、发文本、麦克风开关、手动控车）。手动控车同样走 TankExecutor，
限速、时长、发射冷却等安全约束不变。只监听 127.0.0.1，并校验 Origin，防止别的网站连进来。
"""

import asyncio
import json
from collections import deque
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from .session import LinxSession
from .tank import TankExecutor

INDEX = Path(__file__).with_name("web") / "index.html"


class WebConsole:
    def __init__(self, tank: TankExecutor, session: LinxSession, port: int = 8080):
        self.tank = tank
        self.session = session
        self.port = port
        self.clients: set[ServerConnection] = set()
        self.history: deque[dict] = deque(maxlen=200)  # 新打开的页面先补看最近的对话
        self.conn_state = "connecting"
        self.actions = {
            "forward": lambda: tank.move("forward", 1000, 80),
            "backward": lambda: tank.move("backward", 1000, 80),
            "left": lambda: tank.turn("left", 600, 80),
            "right": lambda: tank.turn("right", 600, 80),
            "turret_left": lambda: tank.turret("left", 500),
            "turret_right": lambda: tank.turret("right", 500),
            "fire": lambda: tank.fire(1),
            "dance": tank.dance,
        }

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/"

    async def start(self):
        origins = [f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}"]
        self.server = await serve(self._handler, "127.0.0.1", self.port, origins=origins, process_request=self._http)
        asyncio.create_task(self._status_loop())

    def publish(self, event: dict):
        """LinxSession 的 on_event 回调（在事件循环线程里调用）。"""
        if event.get("type") == "conn":
            self.conn_state = event["state"]
        elif event.get("type") != "speaking":
            self.history.append(event)
        self._broadcast(event)

    def _broadcast(self, event: dict):
        data = json.dumps(event, ensure_ascii=False)
        for ws in list(self.clients):
            asyncio.create_task(self._send(ws, data))

    @staticmethod
    async def _send(ws: ServerConnection, data: str):
        try:
            await ws.send(data)
        except ConnectionClosed:
            pass

    def _http(self, connection, request):
        if request.path == "/ws":
            return None  # 交给 WebSocket 握手
        if request.path in ("/", "/index.html"):
            headers = Headers([("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")])
            return Response(200, "OK", headers, INDEX.read_bytes())
        return Response(404, "Not Found", Headers([("Content-Type", "text/plain")]), b"not found")

    async def _handler(self, ws: ServerConnection):
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({
                "type": "init",
                "history": list(self.history),
                "conn": self.conn_state,
                "mic": self.session.mic_enabled,
                "status": self.tank.status(),
            }, ensure_ascii=False))
            async for raw in ws:
                try:
                    await self._command(json.loads(raw))
                except Exception as e:  # noqa: BLE001  页面操作出错只提示，不断开
                    self.publish({"type": "error", "text": str(e)})
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    async def _command(self, cmd: dict):
        t = cmd.get("type")
        if t == "stop":
            await self.tank.stop()
            self.publish({"type": "info", "text": "急停"})
        elif t == "text":
            text = str(cmd.get("text", "")).strip()
            if text:
                await self.session.send_text(text)
        elif t == "mic":
            self.session.mic_enabled = bool(cmd.get("on"))
            self._broadcast({"type": "mic", "on": self.session.mic_enabled})
        elif t == "action":
            fn = self.actions.get(cmd.get("name"))
            if fn is None:
                raise ValueError(f"未知动作 {cmd.get('name')}")
            self.publish({"type": "manual", "text": fn()})

    async def _status_loop(self):
        last = None
        while True:
            status = self.tank.status()
            if status != last:
                self._broadcast({"type": "status", "status": status})
                last = status
            await asyncio.sleep(0.2)
