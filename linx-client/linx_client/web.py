"""网页控制台：`run --web` 时在 http://localhost:8080 提供页面。

页面只是界面：连灵矽、收发音频、控车仍由本进程完成。页面通过 /ws 收事件（对话、工具调用、
车的状态），发操作（急停、发文本、麦克风开关、手动控车）。手动控车同样走 TankExecutor，
限速、时长、发射冷却等安全约束不变。默认只监听 127.0.0.1，并校验 Origin，防止别的网站连进来。

`pad=True`（`run --pad` / `pad` 命令）时对局域网开放，给手机当手柄：/pad 是手柄页面，手机连 /ws 时
URL 里要带随机密钥 k；控制台页面 / 仍然只给本机。手机只能发手柄、急停、开火、跳舞这几种指令。
"""

import asyncio
import json
import secrets
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from .session import LinxSession
from .tank import TankExecutor

INDEX = Path(__file__).with_name("web") / "index.html"
PAD = Path(__file__).with_name("web") / "pad.html"
LOCAL_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
PAD_COMMANDS = {"pad", "stop", "action"}  # 手机手柄能发的指令
PAD_ACTIONS = {"fire", "dance"}


def _is_local(connection) -> bool:
    addr = connection.remote_address
    return bool(addr) and addr[0] in LOCAL_ADDRS


class WebConsole:
    def __init__(self, tank: TankExecutor, session: LinxSession | None, port: int = 8080, game=None, pad: bool = False):
        self.tank = tank
        self.game = game  # game.TargetChallenge：打靶挑战计分板
        self.session = session  # None = 只当手柄用（pad 命令），不连灵矽
        self.port = port
        self.pad_key = secrets.token_urlsafe(6) if pad else None
        self.lan_ip = None
        self.clients: set[ServerConnection] = set()
        self.history: deque[dict] = deque(maxlen=200)  # 新打开的页面先补看最近的对话
        self.conn_state = "connecting"
        self.actions = {
            "forward": lambda: tank.move("forward", 1000, 80),
            "backward": lambda: tank.move("backward", 1000, 80),
            "left": lambda: tank.turn("left", 90),
            "right": lambda: tank.turn("right", 90),
            "barrel_up": lambda: tank.barrel("up", 300),
            "barrel_down": lambda: tank.barrel("down", 300),
            "fire": lambda: tank.fire(1),
            "dance": tank.dance,
        }

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/"

    @property
    def pad_url(self) -> str | None:
        """手机打开的手柄地址（带密钥）。"""
        if not self.pad_key:
            return None
        return f"http://{self.lan_ip}:{self.port}/pad?k={self.pad_key}"

    async def start(self):
        origins = [f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}"]
        host = "127.0.0.1"
        if self.pad_key:
            from .tank.udp import _lan_ip

            self.lan_ip = _lan_ip()
            origins.append(f"http://{self.lan_ip}:{self.port}")
            host = "0.0.0.0"
        self.server = await serve(self._handler, host, self.port, origins=origins, process_request=self._http)
        asyncio.create_task(self._status_loop())

    def publish(self, event: dict):
        """LinxSession 的 on_event 回调（在事件循环线程里调用）。"""
        if event.get("type") == "conn":
            self.conn_state = event["state"]
        elif event.get("type") not in ("speaking", "photo", "game"):  # 照片太大、计分板只要最新的，不进历史
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

    def _key_ok(self, path: str) -> bool:
        return bool(self.pad_key) and parse_qs(urlsplit(path).query).get("k", [""])[0] == self.pad_key

    def _http(self, connection, request):
        path = urlsplit(request.path).path
        local = _is_local(connection)
        html = Headers([("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")])
        if path == "/ws":
            if local or self._key_ok(request.path):
                return None  # 交给 WebSocket 握手
        elif path in ("/", "/index.html"):
            if local:
                return Response(200, "OK", html, INDEX.read_bytes())
        elif path == "/pad":
            if local or self._key_ok(request.path):
                return Response(200, "OK", html, PAD.read_bytes())
        else:
            return Response(404, "Not Found", Headers([("Content-Type", "text/plain")]), b"not found")
        return Response(403, "Forbidden", Headers([("Content-Type", "text/plain; charset=utf-8")]),
                        "没有权限：请用控制台上的二维码或终端里打印的手柄地址打开".encode())

    async def _handler(self, ws: ServerConnection):
        local = _is_local(ws)
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({
                "type": "init",
                "history": list(self.history) if local else [],
                "conn": self.conn_state if self.session else "offline",
                "mic": self.session.mic_enabled if self.session else False,
                "status": self.tank.status(),
                "game": self.game.snapshot() if self.game else None,
                "pad_url": self.pad_url if local else None,
            }, ensure_ascii=False))
            async for raw in ws:
                try:
                    cmd = json.loads(raw)
                    if not local and (cmd.get("type") not in PAD_COMMANDS
                                      or (cmd.get("type") == "action" and cmd.get("name") not in PAD_ACTIONS)):
                        raise PermissionError("手机手柄不能发这个指令")
                    await self._command(cmd)
                except Exception as e:  # noqa: BLE001  页面操作出错只提示，不断开
                    self.publish({"type": "error", "text": str(e)})
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            if not local and self.tank.manual_active:
                await self.tank.manual(0, 0)  # 手机断开：立刻停，不等看门狗

    async def _command(self, cmd: dict):
        t = cmd.get("type")
        if self.session is None and t in ("abort", "text", "mic", "game_start", "game_hit", "game_stop"):
            raise RuntimeError("现在是纯手柄模式（pad 命令），没有连接灵矽")
        if t == "pad":
            await self.tank.manual(int(cmd.get("l", 0)), int(cmd.get("r", 0)), int(cmd.get("barrel", 0)))
        elif t == "stop":
            await self.tank.stop()
            if self.session:
                await self.session.abort()
            self.publish({"type": "info", "text": "急停"})
        elif t == "abort":
            await self.session.abort()
        elif t == "persona":
            self.publish({"type": "manual", "text": self.tank.set_persona(str(cmd.get("name")))})
        elif t == "text":
            text = str(cmd.get("text", "")).strip()
            if text:
                await self.session.send_text(text)
        elif t == "mic":
            self.session.mic_enabled = bool(cmd.get("on"))
            self._broadcast({"type": "mic", "on": self.session.mic_enabled})
        elif t in ("game_start", "game_hit", "game_stop") and self.game:
            if t == "game_start":
                text = self.game.start(int(cmd.get("targets") or 3), int(cmd.get("seconds") or 120), str(cmd.get("player") or ""))
            elif t == "game_hit":
                text = self.game.hit(1)
            else:
                text = self.game.stop()
            self.publish({"type": "info", "text": "🎯 " + text})
            await self.session.say(text)  # 网页上点的也让小车念出来
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
