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
        "没说时长默认 1000 毫秒，没说速度默认 80；用户说“慢一点”用 50，“全速/快”用 100。",
        [
            Prop("direction", "string", "forward=前进，backward=后退", enum=["forward", "backward"]),
            Prop("duration_ms", "integer", "持续毫秒数", minimum=100, maximum=5000, default=1000),
            Prop("speed", "integer", "速度百分比", minimum=10, maximum=100, default=80),
        ],
        tank.move,
    )
    mcp.add_tool(
        "self.tank.turn",
        "让小车原地左转或右转（两条履带反向）。没说角度默认 90 度；“掉头”180 度，“转一圈”360 度，“稍微转一点”30 度。",
        [
            Prop("direction", "string", "left=左转，right=右转", enum=["left", "right"]),
            Prop("angle", "integer", "转动角度", minimum=10, maximum=360, default=90),
        ],
        tank.turn,
    )
    mcp.add_tool(
        "self.tank.barrel",
        "抬高或压低炮管（俯仰）。用户说“炮管抬高/抬一点/往上瞄/压低/往下瞄”时调用。没说时长默认 300 毫秒，"
        "“抬很多/抬到最高”用 1000 毫秒。左右瞄准靠 self.tank.turn 转动车身。",
        [
            Prop("direction", "string", "up=抬高，down=压低", enum=["up", "down"]),
            Prop("duration_ms", "integer", "持续毫秒数", minimum=100, maximum=1500, default=300),
        ],
        tank.barrel,
    )
    mcp.add_tool(
        "self.tank.fire",
        "发射子弹。只有用户明确说“开火/发射/打/射击”时才调用；聊天、开玩笑或不确定时不要调用。",
        [Prop("shots", "integer", "发射数量", minimum=1, maximum=3, default=1)],
        tank.fire,
    )
    mcp.add_tool(
        "self.tank.set_persona",
        "切换小车的人格。用户说“切换成特种兵/老爷爷/醉汉模式”“恢复正常”时调用。"
        "切换后你的说话风格也要跟着变：特种兵简短干脆、带军事口令；老爷爷慢悠悠、爱唠叨；醉汉说话含糊、颠三倒四。"
        "走路风格由小车自动调整：特种兵全速冲刺，老爷爷慢慢走走停停，醉汉歪歪扭扭走 S 形。",
        [Prop("persona", "string", "normal=正常，soldier=特种兵，grandpa=老爷爷，drunk=醉汉",
              enum=["normal", "soldier", "grandpa", "drunk"])],
        tank.set_persona,
    )
    mcp.add_tool(
        "self.tank.dance",
        "小车原地扭动并上下摆动炮管，表演一段舞蹈。用户说“跳个舞/庆祝一下/打个招呼”时调用。",
        [],
        tank.dance,
    )
    mcp.add_tool(
        "self.get_device_status",
        "获取小车当前状态：正在执行的动作、排队动作、速度上限、已发射数量。",
        [],
        lambda: json.dumps(tank.status(), ensure_ascii=False),
    )
