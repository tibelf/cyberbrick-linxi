"""注册给灵矽大模型调用的小车工具。描述文字直接影响大模型何时调用，改动后可在智控台验证。"""

import json

from .mcp import McpServer, Prop
from .tank import TankExecutor


def register_tank_tools(mcp: McpServer, tank: TankExecutor) -> None:
    mcp.add_tool(
        "self.tank.stop",
        "立即停车并停止所有动作。用户说“停/停下/别动/stop”时必须第一时间调用，优先于任何其他工具。",
        [],
        tank.stop,
    )
    mcp.add_tool(
        "self.tank.move",
        "让小车直线前进或后退。用户说“往前走两秒”时 direction=forward、duration_ms=2000。"
        "没说时长默认 1000 毫秒，没说速度默认 50。",
        [
            Prop("direction", "string", "forward=前进，backward=后退", enum=["forward", "backward"]),
            Prop("duration_ms", "integer", "持续毫秒数", minimum=100, maximum=5000, default=1000),
            Prop("speed", "integer", "速度百分比", minimum=10, maximum=100, default=50),
        ],
        tank.move,
    )
    mcp.add_tool(
        "self.tank.turn",
        "让小车原地左转或右转。转 90 度大约 800 毫秒（需实测校准），没说时长默认 600 毫秒。",
        [
            Prop("direction", "string", "left=左转，right=右转", enum=["left", "right"]),
            Prop("duration_ms", "integer", "持续毫秒数", minimum=100, maximum=3000, default=600),
            Prop("speed", "integer", "速度百分比", minimum=10, maximum=100, default=60),
        ],
        tank.turn,
    )
    mcp.add_tool(
        "self.tank.turret",
        "让炮塔向左或向右水平转动一段时间（车身不动）。用户说“炮塔左转/往右瞄一点/转一下炮塔”时调用。"
        "没说时长默认 500 毫秒。",
        [
            Prop("direction", "string", "left=左转，right=右转", enum=["left", "right"]),
            Prop("duration_ms", "integer", "持续毫秒数", minimum=100, maximum=3000, default=500),
        ],
        tank.turret,
    )
    mcp.add_tool(
        "self.tank.fire",
        "发射子弹。只有用户明确说“开火/发射/打/射击”时才调用；聊天、开玩笑或不确定时不要调用。",
        [Prop("shots", "integer", "发射数量", minimum=1, maximum=3, default=1)],
        tank.fire,
    )
    mcp.add_tool(
        "self.tank.dance",
        "小车原地扭动并左右摆动炮塔，表演一段舞蹈。用户说“跳个舞/庆祝一下/打个招呼”时调用。",
        [],
        tank.dance,
    )
    mcp.add_tool(
        "self.get_device_status",
        "获取小车当前状态：正在执行的动作、排队动作、速度上限、已发射数量。",
        [],
        lambda: json.dumps(tank.status(), ensure_ascii=False),
    )
