"""仅供本机或 SSH 隧道测试的云控车原型，只能连接模拟小车。"""

import argparse
import asyncio
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from .tank import MockBackend, TankExecutor

PAGE = Path(__file__).with_name("web") / "cloud-demo.html"
AUTH_TIMEOUT_S = 5


class CloudDemo:
    """网页和动作测试入口。只监听回环地址，不能连接实车或发射。"""

    def __init__(self, tank: TankExecutor, token: str):
        if len(token) < 32:
            raise ValueError("请设置至少 32 个字符的随机 CAR_DEMO_TOKEN")
        self.tank = tank
        self._token = token
        self._controller = None

    @staticmethod
    def _http(connection, request):
        path = urlsplit(request.path).path
        if path == "/control":
            return None
        headers = Headers([("Content-Type", "text/html; charset=utf-8"),
                           ("Cache-Control", "no-store"),
                           ("X-Content-Type-Options", "nosniff"),
                           ("X-Frame-Options", "DENY")])
        if path == "/":
            return Response(200, "OK", headers, PAGE.read_bytes())
        return Response(404, "Not Found", headers, b"not found")

    async def start(self, port: int):
        # 不提供公网监听选项；当前原型不能直接放到公网或反向代理后。
        origins = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
        self.server = await serve(self._handle, "127.0.0.1", port, origins=origins,
                                  process_request=self._http, max_size=4096, max_queue=8,
                                  ping_interval=10, ping_timeout=10, close_timeout=2)

    async def _handle(self, ws):
        owner = False
        try:
            try:
                raw = await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT_S)
                auth = json.loads(raw)
                valid = (isinstance(auth, dict) and auth.get("type") == "auth"
                         and isinstance(auth.get("token"), str)
                         and secrets.compare_digest(auth["token"], self._token))
            except (TimeoutError, ValueError, TypeError):
                valid = False
            if not valid:
                await ws.close(code=4001, reason="验证失败")
                return
            if self._controller is not None:
                await ws.close(code=4002, reason="已有控制者")
                return
            self._controller = ws
            owner = True
            await ws.send(json.dumps({"type": "ready", "mode": "模拟小车"}, ensure_ascii=False))
            async for raw in ws:
                try:
                    cmd = json.loads(raw)
                    if not isinstance(cmd, dict):
                        raise ValueError("指令格式错误")
                    if cmd.get("type") == "stop":
                        await self.tank.stop()
                    elif cmd.get("type") == "drive":
                        direction = cmd.get("direction")
                        values = {"forward": (80, 80), "backward": (-80, -80),
                                  "left": (-80, 80), "right": (80, -80)}
                        if direction not in values:
                            raise ValueError("未知方向")
                        await self.tank.manual(*values[direction])
                    else:
                        raise ValueError("未知指令")
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    await ws.send(json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False))
        except ConnectionClosed:
            pass
        finally:
            if owner:
                try:
                    await self.tank.stop()
                finally:
                    self._controller = None


async def _run(port: int, token: str):
    # 这里固定模拟后端，避免误接真实车辆；发射始终关闭。
    tank = TankExecutor(MockBackend(), max_speed=40, fire_enabled=False)
    demo = CloudDemo(tank, token)
    await tank.start()
    try:
        await demo.start(port)
        print(f"模拟控制台：http://127.0.0.1:{port}/（只能在服务器本机或 SSH 隧道中访问）")
        async with demo.server:
            await asyncio.Future()  # 不依赖 stdin，适合无交互测试
    finally:
        await tank.close()


def main():
    parser = argparse.ArgumentParser(description="仅限本机或 SSH 隧道使用的模拟云控车原型")
    parser.add_argument("--port", type=int, default=18765, help="本机监听端口（默认 18765）")
    args = parser.parse_args()
    token = os.environ.get("CAR_DEMO_TOKEN", "")
    if len(token) < 32:
        parser.error("请通过 CAR_DEMO_TOKEN 环境变量设置至少 32 个字符的随机密码")
    try:
        asyncio.run(_run(args.port, token))
    except KeyboardInterrupt:
        print("模拟控制台已停止")


if __name__ == "__main__":
    main()
