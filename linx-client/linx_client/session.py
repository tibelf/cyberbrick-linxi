"""灵矽 WebSocket 会话：hello 握手、上行麦克风、下行 TTS、MCP 工具调用。

协议见 Xrobot-docs/docs/xrobot/platform/websocket.md。
"""

import asyncio
import json
from typing import Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .audio import IN_FRAME_MS, IN_RATE
from .identity import Identity
from .mcp import McpServer

Endpoint = tuple[str, str]  # (ws_url, token)


class LinxSession:
    def __init__(
        self,
        ident: Identity,
        mcp: McpServer,
        get_endpoint: Callable[[], Endpoint],
        audio: bool = True,
        listen_mode: str = "realtime",
        duplex: bool = False,
        mic_device=None,
        speaker_device=None,
    ):
        self.ident = ident
        self.mcp = mcp
        self.get_endpoint = get_endpoint
        self.audio = audio
        self.listen_mode = listen_mode
        self.duplex = duplex
        self.mic_device = mic_device
        self.speaker_device = speaker_device
        self.session_id: str | None = None
        self.speaking = False
        self.ws: ClientConnection | None = None
        self._mic = None
        self._speaker = None
        self._closing = False

    # ---- 外部调用 ----
    async def run_forever(self):
        backoff = 1
        while not self._closing:
            try:
                await self._run_once()
                backoff = 1
            except (OSError, WebSocketException, asyncio.TimeoutError, RuntimeError) as e:
                if self._closing:
                    break
                print(f"[linx] 连接断开：{e!r}，{backoff}s 后重连")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def close(self):
        self._closing = True
        if self.ws:
            await self.ws.close()

    async def send_text(self, text: str):
        """调试用：不说话直接发文本（借用 listen.detect 消息，服务端会把它当作用户输入的唤醒/问题）。"""
        await self._send_json({"session_id": self.session_id, "type": "listen", "state": "detect", "text": text})

    # ---- 内部 ----
    async def _run_once(self):
        url, token = await asyncio.to_thread(self.get_endpoint)
        headers = {
            "Authorization": f"Bearer {token}",
            "Device-Id": self.ident.mac,
            "Client-Id": self.ident.client_id,
            "Protocol-Version": "1",
        }
        print(f"[linx] 连接 {url}")
        async with connect(url, additional_headers=headers, max_size=None, ping_interval=20) as ws:
            self.ws = ws
            await self._send_json(
                {
                    "type": "hello",
                    "version": 1,
                    "features": {"mcp": True, "notify": {"config_updated": True}},
                    "transport": "websocket",
                    "audio_params": {
                        "format": "pcm",
                        "sample_rate": IN_RATE,
                        "channels": 1,
                        "bit_depth": 16,
                        "endianness": "little",
                        "frame_duration": IN_FRAME_MS,
                        "frame_size": IN_RATE * IN_FRAME_MS // 1000,
                        "sample_format": "signed_int16",
                        "play_buffer_duration": 1000,
                    },
                }
            )
            hello = await self._wait_hello(ws)
            self.session_id = hello.get("session_id")
            print(f"[linx] 握手成功 session={self.session_id} 下行音频={hello.get('audio_params')}")

            tasks = []
            try:
                if self.audio:
                    self._start_audio(hello.get("audio_params") or {})
                    tasks.append(asyncio.create_task(self._pump_mic()))
                await self._listen_start()
                await self._recv_loop(ws)
            finally:
                for t in tasks:
                    t.cancel()
                self._stop_audio()
                self.ws = None

    async def _wait_hello(self, ws) -> dict:
        async with asyncio.timeout(10):
            while True:
                raw = await ws.recv()
                if isinstance(raw, str):
                    msg = json.loads(raw)
                    if msg.get("type") == "hello":
                        return msg
                    await self._handle_text(msg)

    def _start_audio(self, params: dict):
        from .audio import Microphone, Speaker

        loop = asyncio.get_running_loop()
        self._mic = Microphone(loop, self.mic_device)
        self._speaker = Speaker(
            sample_rate=int(params.get("sample_rate", 16000)),
            fmt=params.get("format", "pcm"),
            frame_ms=int(params.get("frame_duration", 60)),
            device=self.speaker_device,
        )
        self._speaker.start()
        self._mic.start()

    def _stop_audio(self):
        for dev in (self._mic, self._speaker):
            if dev:
                try:
                    dev.stop()
                except Exception:  # noqa: BLE001
                    pass
        self._mic = self._speaker = None

    async def _listen_start(self):
        await self._send_json(
            {"session_id": self.session_id, "type": "listen", "state": "start", "mode": self.listen_mode}
        )

    async def _pump_mic(self):
        while True:
            frame = await self._mic.queue.get()
            if self.ws is None:
                continue
            if not self.duplex and (self.speaking or self._speaker.busy()):
                continue  # 半双工：播报时不上传，避免小车把自己的声音当成指令
            await self.ws.send(frame)

    async def _recv_loop(self, ws):
        async for raw in ws:
            if isinstance(raw, bytes):
                if self._speaker:
                    self._speaker.feed(raw)
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[linx] 非 JSON 文本: {raw[:200]}")
                continue
            await self._handle_text(msg)

    async def _handle_text(self, msg: dict):
        t = msg.get("type")
        if t == "mcp":
            asyncio.create_task(self._handle_mcp(msg.get("payload") or {}))
        elif t == "stt":
            print(f"🗣  你：{msg.get('text')}")
        elif t == "llm":
            if msg.get("text"):
                print(f"   {msg.get('text')} ({msg.get('emotion')})")
        elif t == "tts":
            state = msg.get("state")
            if state == "start":
                self.speaking = True
            elif state == "sentence_start":
                print(f"🤖 小车：{msg.get('text')}")
            elif state == "stop":
                self.speaking = False
                if msg.get("is_aborted") and self._speaker:
                    self._speaker.flush()
                if self.listen_mode != "realtime":
                    await self._listen_start()
        elif t == "notify" and msg.get("event") == "config_updated":
            print("[linx] 智能体配置已更新，重连生效")
            if self.ws:
                await self.ws.close()
        else:
            print(f"[linx] 收到 {t}: {json.dumps(msg, ensure_ascii=False)[:200]}")

    async def _handle_mcp(self, payload: dict):
        method = payload.get("method")
        if method == "tools/call":
            p = payload.get("params") or {}
            print(f"🔧 调用 {p.get('name')} {json.dumps(p.get('arguments') or {}, ensure_ascii=False)}")
        resp = await self.mcp.handle(payload)
        if resp is not None:
            if method == "tools/call":
                print(f"   → {json.dumps(resp.get('result') or resp.get('error'), ensure_ascii=False)[:200]}")
            await self._send_json({"session_id": self.session_id, "type": "mcp", "payload": resp})

    async def _send_json(self, obj: dict):
        if self.ws is not None:
            await self.ws.send(json.dumps(obj, ensure_ascii=False))
