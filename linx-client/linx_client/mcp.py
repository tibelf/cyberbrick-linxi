"""设备端 MCP 服务（灵矽平台是 MCP 客户端）。

消息封装：{"type": "mcp", "payload": <JSON-RPC 2.0>}，见 docs/xrobot/mcp/hardware-mcp.md。
参数类型与官方 AddTool 一致：只用 boolean / integer / string。
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import __version__

PROTOCOL_VERSION = "2024-11-05"


@dataclass
class Prop:
    name: str
    type: str  # "integer" | "string" | "boolean"
    description: str = ""
    minimum: int | None = None
    maximum: int | None = None
    enum: list[str] | None = None
    default: Any = None

    @property
    def required(self) -> bool:
        return self.default is None

    def schema(self) -> dict:
        s: dict[str, Any] = {"type": self.type}
        if self.description:
            s["description"] = self.description
        if self.minimum is not None:
            s["minimum"] = self.minimum
        if self.maximum is not None:
            s["maximum"] = self.maximum
        if self.enum:
            s["enum"] = self.enum
        if self.default is not None:
            s["default"] = self.default
        return s

    def coerce(self, value: Any) -> Any:
        if value is None:
            if self.required:
                raise ValueError(f"缺少参数 {self.name}")
            return self.default
        if self.type == "integer":
            value = int(value)
            if self.minimum is not None:
                value = max(self.minimum, value)
            if self.maximum is not None:
                value = min(self.maximum, value)
        elif self.type == "boolean":
            value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
        else:
            value = str(value)
            if self.enum and value not in self.enum:
                raise ValueError(f"参数 {self.name} 只能是 {self.enum}，收到 {value!r}")
        return value


Handler = Callable[..., Awaitable[Any] | Any]


@dataclass
class Tool:
    name: str
    description: str
    props: list[Prop] = field(default_factory=list)
    handler: Handler | None = None


class McpServer:
    def __init__(self, name: str = "CyberBrick 语音坦克"):
        self.name = name
        self.tools: dict[str, Tool] = {}
        self.client_capabilities: dict = {}

    @property
    def vision(self) -> dict | None:
        """平台下发的视觉接口 {url, token}；智控台没开视觉分析时为 None。"""
        v = self.client_capabilities.get("vision")
        return v if isinstance(v, dict) and v.get("url") else None

    def add_tool(self, name: str, description: str, props: list[Prop], handler: Handler) -> None:
        self.tools[name] = Tool(name, description, props, handler)

    def _tool_list(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "type": 0,
                "inputSchema": {
                    "type": "object",
                    "properties": {p.name: p.schema() for p in t.props},
                    "required": [p.name for p in t.props if p.required],
                },
            }
            for t in self.tools.values()
        ]

    async def handle(self, msg: dict) -> dict | None:
        """处理一条 JSON-RPC 消息，返回响应（通知类消息返回 None）。"""
        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is None:
            return None  # notifications/initialized 等
        try:
            if method == "initialize":
                # 平台在这里下发自己的能力，例如视觉接口 capabilities.vision = {url, token}
                self.client_capabilities = (msg.get("params") or {}).get("capabilities") or {}
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": __version__},
                }
            elif method == "tools/list":
                result = {"tools": self._tool_list(), "nextCursor": None}
            elif method == "tools/call":
                result = await self._call(msg.get("params") or {})
            else:
                return _error(msg_id, -32601, f"未知方法: {method}")
        except LookupError as e:
            return _error(msg_id, -32601, str(e))
        except Exception as e:  # noqa: BLE001 — 任何异常都要回给平台，不能让连接断掉
            return _error(msg_id, -32603, str(e))
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    async def _call(self, params: dict) -> dict:
        name = params.get("name")
        tool = self.tools.get(name)
        if tool is None:
            raise LookupError(f"未知工具: {name}")
        args = params.get("arguments") or {}
        try:
            kwargs = {p.name: p.coerce(args.get(p.name)) for p in tool.props}
            out = tool.handler(**kwargs)
            if inspect.isawaitable(out):
                out = await out
            return {"content": [{"type": "text", "text": _to_text(out)}], "isError": False}
        except Exception as e:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"执行失败：{e}"}], "isError": True}


def _to_text(out: Any) -> str:
    if out is True or out is None:
        return "ok"
    return str(out)


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
