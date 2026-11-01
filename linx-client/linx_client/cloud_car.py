"""供小车主动连接的云端通道；动作仍由 TankExecutor 排程和限制。"""

import asyncio
import json
import secrets
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

POLL_TIMEOUT_S = 3.0
POLL_FRESH_S = 1.5
COMMAND_AGE_S = 0.4
COMMAND_TIMEOUT_S = 1.5
MAX_FAILSAFE_MS = 5300


@dataclass
class _Command:
    number: int
    op: str
    args: dict
    created: float
    result: asyncio.Future


class CloudCarGateway:
    """每条动作只响应一次设备轮询；过期、取消和断线的指令都不重放。"""

    def __init__(self, token: str):
        if len(token) < 32:
            raise ValueError("设备密码必须是至少 32 个字符的随机值")
        self._token = token
        self._device = None
        self._pending = deque()
        self._sent = {}
        self._last_poll = 0.0
        self._last_nonce = -1
        self._next_number = 0
        self.on_status = None
        self.on_disconnect = None
        self.server = None

    @property
    def online(self):
        return self._device is not None and time.monotonic() - self._last_poll < POLL_FRESH_S

    def _status(self, online):
        if self.on_status:
            self.on_status(online)

    async def start(self, port):
        self.server = await serve(
            self._handle, "127.0.0.1", port, origins=[None], max_size=512, max_queue=4,
            ping_interval=15, ping_timeout=10, close_timeout=2,
        )

    def _fail_all(self, reason):
        for item in list(self._pending) + list(self._sent.values()):
            if not item.result.done():
                item.result.set_exception(ConnectionError(reason))
        self._pending.clear()
        self._sent.clear()

    async def _handle(self, ws):
        owner = False
        was_online = False
        try:
            if urlsplit(ws.request.path).path != "/device":
                await ws.close(code=4004, reason="未知路径")
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), 5)
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
            self._last_nonce = -1
            self._next_number = 0
            owner = True
            await ws.send('{"type":"ready"}')
            while True:
                raw = await asyncio.wait_for(ws.recv(), POLL_TIMEOUT_S)
                try:
                    msg = json.loads(raw)
                    if not isinstance(raw, str) or not isinstance(msg, dict):
                        raise ValueError
                    if msg.get("type") == "ack":
                        number, ok = msg.get("id"), msg.get("ok")
                        if type(number) is not int or type(ok) is not bool:
                            raise ValueError
                        item = self._sent.pop(number, None)
                        if item and not item.result.done():
                            print(f"[云端车] {item.op} {'已确认' if ok else '被拒绝'}", flush=True)
                            if ok:
                                item.result.set_result(None)
                            else:
                                item.result.set_exception(RuntimeError("小车拒绝或丢弃了指令"))
                    elif msg.get("type") == "poll":
                        nonce = msg.get("nonce")
                        if (type(nonce) is not int or not self._last_nonce < nonce <= 2**31 - 1
                                or self._sent):
                            raise ValueError
                        self._last_nonce = nonce
                        self._last_poll = time.monotonic()
                        if not was_online:
                            was_online = True
                            self._status(True)
                        reply = {"type": "noop", "nonce": nonce}
                        while self._pending:
                            item = self._pending.popleft()
                            if item.result.done():
                                continue
                            if time.monotonic() - item.created > COMMAND_AGE_S:
                                item.result.set_exception(TimeoutError("小车指令已过期"))
                                continue
                            self._sent[item.number] = item
                            reply = {"type": "cmd", "nonce": nonce, "id": item.number,
                                     "op": item.op, **item.args}
                            print(f"[云端车] 已下发 {item.op}", flush=True)
                            break
                        await ws.send(json.dumps(reply, separators=(",", ":")))
                    else:
                        raise ValueError
                except (TypeError, ValueError):
                    await ws.close(code=4003, reason="设备消息无效")
                    return
        except (ConnectionClosed, TimeoutError):
            pass
        finally:
            if owner:
                self._device = None
                self._last_poll = 0.0
                self._fail_all("小车连接中断")
                if was_online:
                    self._status(False)
                if self.on_disconnect:
                    self.on_disconnect()

    async def command(self, op, **args):
        if op not in ("drive", "turret", "halt", "led"):
            raise ValueError("不支持的小车指令")
        if not self.online:
            raise ConnectionError("小车未连接云端")
        if op == "halt":
            self._fail_all("已请求停车")
        if len(self._pending) >= 4:
            raise RuntimeError("小车通信繁忙")
        self._next_number += 1
        if self._next_number > 2**31 - 1:
            raise RuntimeError("小车会话指令编号耗尽")
        loop = asyncio.get_running_loop()
        item = _Command(self._next_number, op, args, time.monotonic(), loop.create_future())
        self._pending.append(item)
        try:
            await asyncio.wait_for(item.result, COMMAND_TIMEOUT_S)
        finally:
            # 取消或超时后不再允许该指令被下一次轮询取出。
            if not item.result.done():
                item.result.cancel()


class CloudCarBackend:
    """只供 executor 调用；发射固定锁定，真实控制还需网页控制者在线。"""

    def __init__(self, gateway: CloudCarGateway, control_authorized):
        self.gateway = gateway
        self._control_authorized = control_authorized

    async def connect(self):
        pass  # 车主动连接，不阻塞云端服务启动

    async def _send(self, op, **args):
        if op != "halt" and not self._control_authorized():
            raise RuntimeError("网页控制者未连接")
        await self.gateway.command(op, **args)

    @staticmethod
    def _int(value, lo, hi):
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError("小车动作参数越界")
        return value

    async def drive(self, left, right, failsafe_ms):
        await self._send("drive", l=self._int(left, -100, 100), r=self._int(right, -100, 100),
                         ms=self._int(failsafe_ms, 1, MAX_FAILSAFE_MS))

    async def turret(self, speed, failsafe_ms):
        await self._send("turret", speed=self._int(speed, -100, 100),
                         ms=self._int(failsafe_ms, 1, MAX_FAILSAFE_MS))

    async def fire(self, pulse_ms):
        raise RuntimeError("云端实车发射尚未启用")

    async def halt(self):
        await self._send("halt")

    async def led(self, r, g, b):
        await self._send("led", r=self._int(r, 0, 255), g=self._int(g, 0, 255), b=self._int(b, 0, 255))

    async def ping(self):
        if not self.gateway.online:
            raise ConnectionError("小车未连接云端")
        return {"online": True, "link": "小车直连云端", "latency_ms": None}

    async def close(self):
        pass
