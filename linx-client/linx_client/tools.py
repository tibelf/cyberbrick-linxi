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


CAMERA_HOWTO = {
    # 俯视：摄像头在场地正上方往下拍，画面里同时有车和目标
    "overhead": "摄像头固定在场地旁边往下拍（可能是正上方，也可能是斜着拍），画面里能同时看到小车和周围的东西。小车是一辆履带坦克，"
    "车头就是炮管指向的方向（车顶如果贴了箭头，以箭头为准）。"
    "question 要让视觉模型以小车为参照来回答，例如“这辆履带小坦克的炮管指向为 12 点钟，红色杯子在几点钟方向？"
    "离小车大约几个车身长？中间有没有挡路的东西？”。"
    "拿到结果后换算成动作：每差 1 个钟点转 30 度（1-5 点钟右转，7-11 点钟左转，6 点钟掉头 180 度），"
    "转完再前进；距离没把握时先走 1 秒，再拍一次确认，边看边走，直到目标在 12 点钟方向、离车很近。"
    "如果视觉模型分不清车头朝向：先前进 500 毫秒再拍一张，问它小车往哪个方向移动了，移动方向就是车头。",
    # 前视：摄像头装在车上朝前，画面就是车看到的
    "front": "摄像头装在小车上朝前拍，画面就是小车看到的。"
    "question 写清楚要从画面里得到什么，例如“画面里有没有红色杯子？在左边、中间还是右边？大约偏离中心多少度？”。"
    "要对准目标时：先拍照问目标偏左还是偏右、偏多少度，再用 self.tank.turn 转过去，然后再拍一次确认目标在中间。",
}


def register_camera_tools(mcp: McpServer, photo, view: str = "overhead", seeker=None) -> None:
    """photo 是 camera.PhotoTool；只有启动时指定了 --camera 才注册。view 决定怎么教大模型看图。
    seeker 是 seek.Seeker：本地闭环寻的，大模型调用一次，小车自己边看边调。"""
    if seeker is not None:
        mcp.add_tool(
            "self.tank.seek",
            "让小车自己看着摄像头去找某个目标：找到后转向对准，goal=approach 时还会走到它跟前。"
            "小车会自己反复拍照、微调，直到对准或走到，完成后自己播报结果，你只需要调用一次，"
            "然后告诉用户“收到，我边看边找”，不要再连续调用 take_photo、turn、move。"
            "用户说“去红色杯子那里/找到那个盒子/对准杯子”时用它。"
            "fire 只有在用户这句话里明确说了“开火/发射/打它”时才设为 true，对准后会打一发；否则必须是 false。"
            "摄像头固定在场地旁边时，小车要先往前走一小段才能看出车头朝哪，所以 aim 也会稍微移动。"
            "绝不对准人或动物。",
            [
                Prop("target", "string", "要找的目标，越具体越好，例如“红色杯子”"),
                Prop("goal", "string", "approach=走到目标跟前，aim=原地转向对准", enum=["approach", "aim"],
                     default="approach"),
                Prop("fire", "boolean", "对准后是否开火（用户明确要求才为 true）", default=False),
            ],
            seeker,
        )
    mcp.add_tool(
        "self.camera.take_photo",
        "拍一张照片并让视觉模型回答 question。用户让你“看看/找一找某个东西/瞄准某个东西/去某个地方”时先调用它，不要瞎猜。"
        + CAMERA_HOWTO[view]
        + "拍照和瞄准都不代表可以开火，只有用户明确说“开火/发射/打”时才调用 self.tank.fire。",
        [Prop("question", "string", "要从照片里回答的问题")],
        photo,
    )
