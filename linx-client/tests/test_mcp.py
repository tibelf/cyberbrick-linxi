import asyncio

from linx_client.mcp import McpServer, Prop
from linx_client.tank import MockBackend, TankExecutor
from linx_client.tools import register_tank_tools


def call(mcp, method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "method": method, "id": id}
    if params is not None:
        msg["params"] = params
    return asyncio.run(mcp.handle(msg))


def make():
    mcp = McpServer()
    tank = TankExecutor(MockBackend(verbose=False))
    register_tank_tools(mcp, tank)
    return mcp, tank


def test_initialize():
    mcp, _ = make()
    r = call(mcp, "initialize", {"protocolVersion": "2024-11-05"})
    assert r["result"]["capabilities"] == {"tools": {}}
    assert r["result"]["protocolVersion"] == "2024-11-05"


def test_tools_list_schema():
    mcp, _ = make()
    tools = {t["name"]: t for t in call(mcp, "tools/list")["result"]["tools"]}
    assert {"self.tank.move", "self.tank.fire", "self.tank.stop", "self.get_device_status"} <= tools.keys()
    move = tools["self.tank.move"]["inputSchema"]
    assert move["required"] == ["direction"]
    assert move["properties"]["duration_ms"]["maximum"] == 5000
    assert tools["self.tank.fire"]["type"] == 0


def test_unknown_tool_and_method():
    mcp, _ = make()
    assert call(mcp, "tools/call", {"name": "self.nope"})["error"]["code"] == -32601
    assert call(mcp, "foo/bar")["error"]["code"] == -32601


def test_notification_has_no_response():
    mcp, _ = make()
    assert asyncio.run(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None


def test_bad_enum_is_tool_error():
    mcp, _ = make()
    r = call(mcp, "tools/call", {"name": "self.tank.move", "arguments": {"direction": "up"}})
    assert r["result"]["isError"] is True


def test_prop_clamps_integer():
    p = Prop("x", "integer", minimum=0, maximum=10)
    assert p.coerce(99) == 10 and p.coerce("-3") == 0
