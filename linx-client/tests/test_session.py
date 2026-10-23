"""用本地假平台跑通：鉴权头 → hello → initialize → tools/list → tools/call。"""

import asyncio
import json

from websockets.asyncio.server import serve

from linx_client.identity import Identity
from linx_client.mcp import McpServer
from linx_client.session import LinxSession
from linx_client.tank import MockBackend, TankExecutor
from linx_client.tools import register_tank_tools


def test_session_against_fake_platform():
    async def main():
        seen = {}
        replies = {}

        async def platform(ws):
            seen["headers"] = ws.request.headers
            seen["hello"] = json.loads(await ws.recv())
            await ws.send(json.dumps({"type": "hello", "transport": "websocket", "session_id": "s1",
                                      "audio_params": {"format": "pcm", "sample_rate": 16000}}))
            seen["listen"] = json.loads(await ws.recv())
            reqs = [
                {"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2024-11-05"}, "id": 1},
                {"jsonrpc": "2.0", "method": "tools/list", "id": 2},
                {"jsonrpc": "2.0", "method": "tools/call", "id": 3,
                 "params": {"name": "self.tank.move", "arguments": {"direction": "forward", "duration_ms": 100}}},
                {"jsonrpc": "2.0", "method": "tools/call", "id": 4,
                 "params": {"name": "self.tank.fire", "arguments": {}}},
            ]
            for r in reqs:
                await ws.send(json.dumps({"type": "mcp", "payload": r}))
            while len(replies) < len(reqs):
                msg = json.loads(await ws.recv())
                assert msg["type"] == "mcp"
                replies[msg["payload"]["id"]] = msg["payload"]
            await asyncio.sleep(1.5)  # 让动作执行完
            await ws.close()

        async with serve(platform, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            be = MockBackend(verbose=False)
            tank = TankExecutor(be, fire_pulse_ms=10, fire_cooldown_s=0)
            await tank.start()
            mcp = McpServer()
            register_tank_tools(mcp, tank)
            ident = Identity(mac="02:00:00:00:00:01", client_id="cid")
            session = LinxSession(ident, mcp, lambda: (f"ws://127.0.0.1:{port}/v1/ws/", "tok"), audio=False)
            await asyncio.wait_for(session._run_once(), 10)
            await tank.close()

        h = seen["headers"]
        assert h["Authorization"] == "Bearer tok" and h["Device-Id"] == ident.mac and h["Protocol-Version"] == "1"
        assert seen["hello"]["features"]["mcp"] is True
        assert seen["hello"]["audio_params"]["format"] == "pcm"
        assert seen["listen"] == {"session_id": "s1", "type": "listen", "state": "start", "mode": "realtime"}
        assert replies[1]["result"]["capabilities"] == {"tools": {}}
        assert any(t["name"] == "self.tank.fire" for t in replies[2]["result"]["tools"])
        assert replies[3]["result"]["isError"] is False
        assert replies[4]["result"]["isError"] is False
        kinds = [c[0] for c in be.calls]
        assert "drive" in kinds and "fire" in kinds

    asyncio.run(main())
