"""仅测试车端到云端的认证连接，绝不执行或转发小车动作。"""

import argparse
import asyncio
import json
import os
import secrets
from urllib.parse import urlsplit

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


class CarProbe:
    """只监听回环地址，由外部 TLS 反向代理转发设备连接。"""

    def __init__(self, token: str, auth_timeout: float = 5):
        if len(token) < 32:
            raise ValueError("设备测试密码必须是至少 32 个字符的随机值")
        self._token = token
        self._auth_timeout = auth_timeout
        self._device = None
        self.server = None

    async def start(self, port: int):
        self.server = await serve(
            self._handle,
            "127.0.0.1",
            port,
            origins=[None],
            max_size=512,
            max_queue=4,
            ping_interval=15,
            ping_timeout=10,
            close_timeout=2,
        )

    async def _handle(self, ws):
        owner = False
        try:
            if urlsplit(ws.request.path).path != "/device":
                await ws.close(code=4004, reason="未知路径")
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), self._auth_timeout)
                auth = json.loads(raw)
                valid = (isinstance(raw, str) and isinstance(auth, dict)
                         and auth.get("type") == "auth" and isinstance(auth.get("token"), str)
                         and secrets.compare_digest(auth["token"], self._token))
            except (TimeoutError, TypeError, ValueError):
                valid = False
            if not valid:
                await ws.close(code=4001, reason="验证失败")
                return
            if self._device is not None:
                await ws.close(code=4002, reason="设备已在线")
                return
            self._device = ws
            owner = True
            await ws.send(json.dumps({"type": "ready", "mode": "只读检测"}, ensure_ascii=False))
            print("[车端检测] 设备已认证上线", flush=True)
            async for raw in ws:
                try:
                    message = json.loads(raw)
                    valid = (isinstance(raw, str) and isinstance(message, dict)
                             and message.get("type") == "ping" and type(message.get("id")) is int
                             and 0 <= message["id"] <= 2**31 - 1)
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    await ws.close(code=4003, reason="只接受检测消息")
                    return
                await ws.send(json.dumps({"type": "pong", "id": message["id"]}))
        except ConnectionClosed:
            pass
        finally:
            if owner:
                self._device = None
                print("[车端检测] 设备已离线", flush=True)


async def _run(port: int, token: str):
    probe = CarProbe(token)
    await probe.start(port)
    print(f"[车端检测] 只读服务监听 127.0.0.1:{port}，不执行动作", flush=True)
    async with probe.server:
        await asyncio.Future()


def main():
    parser = argparse.ArgumentParser(description="测试车端连接，不提供实车动作")
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    token = os.environ.get("CAR_PROBE_TOKEN", "")
    if len(token) < 32:
        parser.error("请通过 CAR_PROBE_TOKEN 提供至少 32 个字符的随机密码")
    try:
        asyncio.run(_run(args.port, token))
    except KeyboardInterrupt:
        print("[车端检测] 已停止")


if __name__ == "__main__":
    main()
