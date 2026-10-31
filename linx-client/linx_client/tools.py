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
    async def device_status():
        await tank.check()  # 真的问一下车，不只是读本地记录
        return json.dumps(tank.status(), ensure_ascii=False)

    mcp.add_tool(
        "self.get_device_status",
        "检查小车：会真的给车发消息确认它在不在线（link.online、延迟、是否刚重启过 rebooted），"
        "并返回最近一次动作失败的原因（last_error）、正在执行的动作和任务、排队动作、速度上限、已发射数量。"
        "用户说“车没反应/车怎么不动/检查一下车/车还在吗”时调用，然后用一句话告诉用户结果："
        "不在线就提醒检查车的电源、电量和网络；刚重启过多半是电量低；有 last_error 就照实说出来。",
        [],
        device_status,
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


def register_game_tools(mcp: McpServer, game) -> None:
    """game 是 game.TargetChallenge：打靶挑战。"""
    mcp.add_tool(
        "self.game.start",
        "开始打靶挑战：地上摆好目标，玩家用语音指挥小车在限时内全部打倒，指令越少分越高。"
        "用户说“开始打靶挑战/我们来比赛打靶/3 个目标限时两分钟”时调用，把返回的开场白念给用户。"
        "用户说了名字（“我是小明”）就填 player。挑战中照常执行用户的移动和开火指令，不要替用户决定开火。",
        [Prop("targets", "integer", "目标个数", default=3, minimum=1, maximum=10),
         Prop("seconds", "integer", "限时（秒）", default=120, minimum=20, maximum=600),
         Prop("player", "string", "玩家名字", default="")],
        game.start,
    )
    mcp.add_tool(
        "self.game.hit",
        "记一次命中。挑战中用户说“打中了/倒了/又倒一个/打中两个”时调用（打中几个填 count）。"
        "只有用户自己说打中了才调用，不要猜。把返回结果念给用户，全部打中时会给出总分和排名。",
        [Prop("count", "integer", "这次打中几个", default=1, minimum=1, maximum=10)],
        game.hit,
    )
    mcp.add_tool("self.game.stop", "提前结束打靶挑战（不记分）。用户说“不玩了/结束挑战”时调用。", [], game.stop)
    mcp.add_tool("self.game.status", "打靶挑战的当前成绩：命中、剩余时间、开火次数、指令数、得分，没在挑战时给出最高分。"
                 "用户问“还剩多久/几分了/最高分多少”时调用。", [], game.status)


def register_macro_tools(mcp: McpServer, macros) -> None:
    """macros 是 macros.Macros：语音编程，把一串动作起个名字记下来，以后一句话执行。"""
    mcp.add_tool(
        "self.macro.save",
        "把一串动作记成一个名字（语音编程）。用户说“记住，巡逻就是前进两秒、左转 90 度，重复 4 次”时调用："
        "name=巡逻，steps 每步一句、用分号隔开，只能用这些写法：前进 2000（毫秒，可加“速度 100”）、后退 1000、左转 90、右转 45、"
        "炮管抬 300、炮管压 300、开火 1、跳舞、等待 500、情绪 开心（开心/生气/难过/惊讶/思考）、执行 别的动作名；"
        "整套重复几遍写在 repeat。同名会覆盖。只有用户定义时明确说了开火，steps 里才能有开火。"
        "返回报错时按提示改好再调一次。记住后用一句话告诉用户，不要马上执行。",
        [Prop("name", "string", "这套动作的名字，例如“巡逻”"),
         Prop("steps", "string", "例如“前进 2000；左转 90”"),
         Prop("repeat", "integer", "整套重复几遍", default=1, minimum=1, maximum=10)],
        macros.save,
    )
    mcp.add_tool(
        "self.macro.run",
        "执行一套记住的动作。用户说出记住的动作名（例如“巡逻”“来一段庆祝”“跑一遍胜利舞”）时调用，"
        "repeat=0 表示按记住的遍数，用户说了“来三遍”就填 3。动作里如果有开火，是用户当初自己定义的，可以执行。"
        "不知道有哪些动作时先调用 self.macro.list。",
        [Prop("name", "string", "动作名"),
         Prop("repeat", "integer", "执行几遍，0=按记住的", default=0, minimum=0, maximum=10)],
        macros.run,
    )
    mcp.add_tool("self.macro.list", "列出记住的所有动作和它们的步骤。用户问“你会哪些动作/学过什么”时调用。", [], macros.list)
    mcp.add_tool("self.macro.delete", "忘掉一套记住的动作。用户说“把巡逻忘了/删掉巡逻”时调用。",
                 [Prop("name", "string", "动作名")], macros.delete)
    mcp.add_tool(
        "self.macro.record_start",
        "开始“边做边学”：之后用户下的移动、转向、炮管、开火、跳舞指令照常执行，同时记下来。"
        "用户说“开始学动作/我教你一套动作/看我怎么指挥”时调用。",
        [],
        macros.record_start,
    )
    mcp.add_tool(
        "self.macro.record_stop",
        "结束“边做边学”。用户说“记住这套叫胜利舞”时 name=胜利舞；说“不学了/算了”时 name 留空（不保存）。",
        [Prop("name", "string", "动作名，留空=不保存", default=""),
         Prop("repeat", "integer", "整套重复几遍", default=1, minimum=1, maximum=10)],
        macros.record_stop,
    )


def register_wander_tool(mcp: McpServer, wanderer, camera: bool) -> None:
    """有摄像头时边看边逛；没有摄像头（或看不清）时闭着眼慢慢逛。两种都一直逛到用户说“停”。"""
    how = ("边走边看摄像头避开障碍物和死角，看不清画面时会自动改成慢慢地闭着眼逛，" if camera
           else "现在没开摄像头，小车会闭着眼慢慢逛（速度低、常转向），")
    mcp.add_tool(
        "self.tank.wander",
        "让小车自由闲逛：它会一直到处逛、随手做些小动作，" + how +
        "**一直逛到用户说“停”为止**。用户说“自己逛逛/随便走走/去玩吧/巡逻一下/开始闲逛”时调用一次即可，"
        "然后回一句“好嘞，我出去溜达啦”，不要再调用其他移动工具。闲逛时小车绝不会开火。"
        "闲逛中用户又给了别的任务（例如去某个东西那里），直接调用对应工具，它会接替闲逛。",
        [],
        wanderer,
    )


def register_map_tools(mcp: McpServer, builder) -> None:
    """builder 是 navigate.MapBuilder；--marker 时注册。"""
    mcp.add_tool(
        "self.map.build",
        "拍一张俯视照片给场地建地图：标出障碍物（箱子、椅子腿、墙等），之后小车去哪里、闲逛时都会沿着绕开障碍物的路线走，"
        "不会乱碰东西。地上要平放着锚点标记，照片里要能看到它。用户说“拍一张地图/建个地图/看看场地/重新拍地图/东西挪过了”时调用，"
        "然后把返回的结果用一句话告诉用户（地图多大、有哪些障碍物）。",
        [],
        builder,
    )


def register_camera_tools(mcp: McpServer, photo, view: str = "overhead", seeker=None, wanderer=None) -> None:
    """photo 是 camera.PhotoTool；只有启动时指定了 --camera 才注册。view 决定怎么教大模型看图。
    seeker 是 seek.Seeker：本地闭环寻的，大模型调用一次，小车自己边看边调。"""
    if wanderer is not None:
        register_wander_tool(mcp, wanderer, camera=True)
    if seeker is not None:
        mcp.add_tool(
            "self.tank.seek",
            "让小车自己看着摄像头去完成一个目标：goal=approach 走到它跟前（碰到或紧挨着），aim 原地转向对准，"
            "hit 冲上去把它撞倒/撞开。小车会自己反复拍照、微调，没完成不会停，完成后自己播报结果，你只需要调用一次，"
            "然后告诉用户“收到，我边看边找”，不要再连续调用 take_photo、turn、move。"
            "用户说“去红色杯子那里/走到瓶子旁边”用 approach，“对准杯子”用 aim，“把瓶子撞倒/撞一下那个盒子/推开它”用 hit。"
            "hit 只能撞瓶子、盒子这类物品，绝不能撞人或动物，用户要求撞人时拒绝。"
            "fire 只有在用户这句话里明确说了“开火/发射/打它”时才设为 true，对准后会打一发；否则必须是 false。"
            "摄像头固定在场地旁边时，小车要先往前走一小段才能看出车头朝哪，所以 aim 也会稍微移动。"
            "绝不对准人或动物。",
            [
                Prop("target", "string", "要找的目标，越具体越好，例如“红色杯子”"),
                Prop("goal", "string", "approach=走到目标跟前，aim=原地转向对准，hit=撞倒/撞开目标",
                     enum=["approach", "aim", "hit"],
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


def register_blind_tools(mcp: McpServer, blind, camera: bool = True) -> None:
    """blind 是 blind.BlindNavigator：一张俯拍图规划、闭眼走完。camera=False 时只能口述摆放（ahead_cm）。"""
    if camera:
        _register_blind_map(mcp, blind)
    mcp.add_tool(
        "self.blind.goto",
        "闭着眼走到地图上的某个东西那里：按记住的场地规划绕开障碍物的路线，转向和前进按校准参数执行，途中不拍照。"
        "用户说“去红色盒子那里/开到纸箱旁边”时调用。"
        "where 选去目标的哪一侧（都是从小车看过去）：“绕到垃圾桶后面”=后面，“去盒子左边”=左边，"
        "“绕着纸杯转一圈/绕纸杯一圈”=绕一圈（最后回到出发点），“撞倒纸杯/撞一下盒子”=撞倒（开到跟前正对它，冲过去再退回来；"
        "只撞物品，绝不撞人或动物）。"
        "没拍俯拍图、或者用户直接说了位置（“纸杯在车头正前方 40 厘米”）时不用拍照：ahead_cm 填距离（厘米），"
        "没说距离就不填，按车头正前方 50 厘米算。位置是估计的，走过几段后会越来越不准。",
        [Prop("target", "string", "要去的东西，用地图里的名字或用户的说法"),
         Prop("where", "string", "去目标的哪一侧，或者撞倒它",
              enum=["旁边", "后面", "左边", "右边", "绕一圈", "撞倒"], default="旁边"),
         Prop("ahead_cm", "integer", "不拍照时目标在车头正前方多少厘米；有俯拍图就不填", minimum=0, maximum=150, default=0)],
        blind.goto,
    )


def _register_blind_map(mcp: McpServer, blind) -> None:
    mcp.add_tool(
        "self.blind.map",
        "拍一张从正上方往下拍的场地照片，让视觉模型记住小车和障碍物在哪。之后用 self.blind.goto 去某个东西那里时，"
        "小车闭着眼按规划好的绕障路线走，不再拍照。用户说“拍一张俯拍图/记住场地/重新拍地图/东西挪了”时调用；"
        "如果用户已经说了要去哪，把目标填进 target，让视觉模型一定把它标出来；用户说了车头朝哪（“车头对着垃圾桶”“车头朝左”）"
        "就原话填进 facing，比视觉模型自己判断准。返回结果里会说车头朝哪，用户说不对时带上 facing 重新调用。",
        [Prop("target", "string", "接下来要去的东西（例如“红色盒子”），可以不填", default=""),
         Prop("facing", "string", "用户说的车头朝向，原话照填：“对着垃圾桶”“朝左”“朝画面上方”；没说就不填", default="")],
        blind.build,
    )
