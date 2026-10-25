"""看图闭环寻的（self.tank.seek）：拍照 → 视觉模型按固定格式回答 → 转向/前进 → 动完再拍，直到真的完成。

为什么在本地跑：平台一轮对话通常只执行一次工具调用就等用户说下一句，大模型没法自己“边看边调”。
所以大模型只调用一次 seek，循环在这里完成，结束后用 say() 播报结果。说“停”会取消整个任务。

三种目标：approach=走到跟前（碰到或紧挨着），aim=原地对准，hit=撞上去把它撞倒/撞开。
**没完成就不停**：不按步数收工，只在这些情况结束——视觉模型确认完成、超时（TIME_LIMIT_S）、
连续几次找不到车或目标、脱困几次后仍然卡住。“到了”必须由视觉模型再确认一次（是/否），
不能只凭坐标：低机位时画面里前后距离被压扁，两者看着挨着，实际还差一个车身（2026-10-25 实测）。

两种摄像头摆法：
- overhead（摄像头固定在场地旁边，俯拍或斜拍）：视觉模型最不擅长判断车头朝向，所以只问它
  坦克和目标的外框（百分比坐标 x1,y1,x2,y2）。车头方向由程序自己算：前进一小段，比较前后两张照片里
  坦克的位置，位移方向就是车头。用两个外框的**底边中点**（贴地的点）算方向，用**以车身大小为单位**的
  间隙判断远近，这样不受透视远小近大的影响。前进时顺便标定“画面上移动 1% 要走多少毫秒”。
  （2026-10-25 实测：问“目标在几点钟方向”时，低机位侧拍下模型每次都答 4 点钟，车原地打转，所以改成这种做法。）
- front（摄像头装在车上朝前）：问目标偏离画面中心多少度，直接换算成转向角。

动作一律经过 TankExecutor（move/turn/fire），限速、时长上限、发射锁定和冷却照常生效。
"""

import math
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from .tank import TankExecutor

TIME_LIMIT_S = 120  # 两分钟还没完成就停下报告，免得无止境地转圈
MAX_STEPS = 40  # 兜底的步数上限，正常不会走到
MAX_MISSES = 3  # 连续几次看不到车或目标才放弃（偶尔被挡住、模型看漏不算）
MAX_UNSTICK = 2  # 卡住后脱困（后退 + 转向）几次仍然卡住才放弃
MAX_RAMS = 3  # hit：撞几次还没倒就放弃
SPEED = 80
RAM_SPEED = 100
RAM_MS = 900  # 撞上去时冲多久
PROBE_MS = 500  # 用来看出车头方向的试探前进
MIN_MOVE_PCT = 2.0  # 坦克在画面里至少移动这么多（%）才算看清了方向
NEAR_CARS = 0.5  # 两个外框的间隙小于这么多个车身，就请视觉模型确认是不是已经挨着了
DEFAULT_BOX_PCT = 8.0  # 模型只给了中心点时，假设外框这么大
EXT_AIM_TOLERANCE_DEG = 15  # 外部摄像头：车头和目标方向差在这个范围内算对准（透视误差大，放宽）
STEP_MIN_MS, STEP_MAX_MS = 300, 1500  # 每一步前进的范围，走完再拍照确认
SCAN_ANGLE = 45  # 前视看不到目标时，每次转这么多度再找
FRONT_FOV = 60  # 前视摄像头水平视角（度），只用来提示视觉模型
AIM_TOLERANCE_DEG = 8  # 前视：偏角在这个范围内算对准
FRONT_STEP_MS = 600

Ask = Callable[[str], Awaitable[str]]
Say = Callable[[str], Awaitable[None]]
Point = tuple[float, float]
GOALS = {"approach": "走到", "aim": "对准", "hit": "撞倒"}


def _yes(s: str | None) -> bool:
    return bool(s) and s.strip() in ("是", "有", "yes", "true", "True")


def _point(label: str, answer: str) -> Point | None | bool:
    """在回答里找 “坦克=x,y”。找到坐标返回 (x, y)；写了“无”返回 None；格式不对返回 False。"""
    m = re.search(label + r"\s*[=:：]\s*[(（]?\s*(\d+(?:\.\d+)?)\s*[,，、\s]\s*(\d+(?:\.\d+)?)", answer)
    if m:
        return min(float(m.group(1)), 100.0), min(float(m.group(2)), 100.0)
    if re.search(label + r"\s*[=:：]\s*(无|没有|否)", answer):
        return None
    return False


def parse_positions(answer: str) -> dict | None:
    """解析 “坦克=20,60；目标=75,55”（百分比坐标，左上角 0,0）。格式不对返回 None。"""
    car, tgt = _point("坦克", answer), _point("目标", answer)
    if car is False or tgt is False:
        return None
    return {"car": car, "target": tgt}


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> Point:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    @property
    def foot(self) -> Point:
        """底边中点：物体贴地的位置，低机位时比中心点更能代表它在地面上的位置。"""
        return (self.x1 + self.x2) / 2, self.y2

    @property
    def w(self) -> float:
        return max(self.x2 - self.x1, 1.0)

    @property
    def h(self) -> float:
        return max(self.y2 - self.y1, 1.0)


_NUM = r"(\d+(?:\.\d+)?)"
_SEP = r"\s*[,，、\s]\s*"


def _box(label: str, answer: str) -> Box | None | bool:
    """找 “坦克=x1,y1,x2,y2”；只给了中心点 x,y 时按默认大小补成外框。写了“无”返回 None，格式不对返回 False。"""
    m = re.search(label + r"\s*[=:：]\s*[(（\[]?\s*" + _SEP.join([_NUM] * 4), answer)
    if m:
        v = [float(g) for g in m.groups()]
        if max(v) > 100:  # 有的模型习惯用 0-1000 的坐标
            v = [x / 10 for x in v]
        x1, x2 = sorted((min(v[0], 100), min(v[2], 100)))
        y1, y2 = sorted((min(v[1], 100), min(v[3], 100)))
        return Box(x1, y1, x2, y2)
    p = _point(label, answer)
    if isinstance(p, tuple):
        d = DEFAULT_BOX_PCT / 2
        return Box(p[0] - d, p[1] - d, p[0] + d, p[1] + d)
    return p


def parse_boxes(answer: str) -> dict | None:
    """解析 “坦克=20,55,30,65；目标=70,40,78,70”。格式不对返回 None。"""
    car, tgt = _box("坦克", answer), _box("目标", answer)
    if car is False or tgt is False:
        return None
    return {"car": car, "target": tgt}


def gap_in_cars(car: Box, tgt: Box) -> float:
    """两个物体贴地位置之间的空隙，以车身大小为单位（横向按车宽、纵深按车高算，抵消透视远小近大）。"""
    dx = max(0.0, abs(car.foot[0] - tgt.foot[0]) - (car.w + tgt.w) / 2) / car.w
    dy = max(0.0, abs(car.foot[1] - tgt.foot[1]) - car.h / 2) / car.h
    return math.hypot(dx, dy)


def parse_front(answer: str) -> dict | None:
    """解析 “找到=是；偏角=-20；近=否”。偏角左负右正。"""
    found = re.search(r"找到\s*[=:：]\s*(是|否)", answer)
    if not found:
        return None
    if found.group(1) == "否":
        return {"found": False}
    off = re.search(r"偏角\s*[=:：]\s*([-+−]?\d+)", answer)
    near = re.search(r"近\s*[=:：]\s*(是|否)", answer)
    if not off:
        return None
    return {"found": True, "offset": int(off.group(1).replace("−", "-")), "near": _yes(near and near.group(1))}


def parse_yes_no(answer: str) -> bool | None:
    a = answer.strip()
    if re.match(r"^(是|对|有|已经|yes)", a, re.I):
        return True
    if re.match(r"^(否|不|没|还没|no)", a, re.I):
        return False
    return None


def turn_needed(heading: Point, to_target: Point) -> float:
    """车头方向转到目标方向要转多少度：正数左转，负数右转。

    画面坐标 y 向下，先翻成 y 向上再算，这样逆时针（从上往下看是左转）为正。
    假设画面没有左右镜像（手机用后置摄像头）。
    """
    a = math.degrees(math.atan2(-heading[1], heading[0]))
    b = math.degrees(math.atan2(-to_target[1], to_target[0]))
    return (b - a + 180) % 360 - 180


class Seeker:
    def __init__(self, tank: TankExecutor, ask: Ask, say: Say | None = None, view: str = "overhead",
                 max_steps: int = MAX_STEPS, probe_ms: int = PROBE_MS, time_limit_s: float = TIME_LIMIT_S):
        self.tank = tank
        self.ask = ask
        self.say = say
        self.view = view
        self.max_steps = max_steps
        self.probe_ms = probe_ms
        self.time_limit_s = time_limit_s

    # MCP 工具入口：立即返回，循环在后台跑
    def __call__(self, target: str, goal: str = "approach", fire: bool = False) -> str:
        desc = f"{GOALS.get(goal, '走到')}{target}" + ("并开火" if fire else "")
        return self.tank.start_mission(desc, lambda: self.run(target, goal, fire)) + \
            "（没完成不会停：边看边调整，完成后会播报结果；说“停”随时中止）"

    async def run(self, target: str, goal: str, fire: bool) -> str:
        loop = self._loop_external if self.view == "overhead" else self._loop_front
        ok, result = await loop(target, goal)
        if ok and fire:
            try:
                await self._do(lambda: self.tank.fire(1))
                result += "，已开火"
            except Exception as e:  # noqa: BLE001 — 例如 --no-fire 锁定，如实播报
                result += f"，但没有开火：{e}"
        print(f"🎯 {result}")
        if self.say:
            await self.say(result)
        return result

    async def _do(self, start: Callable[[], str]):
        start()
        await self.tank.wait_idle()

    async def _forward(self, ms: int, speed: int = SPEED):
        await self._do(lambda: self.tank.move("forward", ms, speed))

    async def _turn(self, degrees: float):
        if abs(degrees) >= 10:
            await self._do(lambda: self.tank.turn("left" if degrees > 0 else "right", round(min(abs(degrees), 180))))

    async def _ask_parsed(self, question: str, parse):
        for _ in range(2):  # 格式不对再问一次
            r = parse(await self.ask(question))
            if r is not None:
                return r
        return None

    async def _confirm(self, question: str) -> bool:
        r = await self._ask_parsed(question + "只回答“是”或“否”，不要解释。", parse_yes_no)
        return bool(r)

    async def _ram(self, target: str) -> bool:
        """hit：对准后冲上去，再看目标倒了没有。"""
        await self._forward(RAM_MS, RAM_SPEED)
        await self._do(lambda: self.tank.move("backward", 400, SPEED))  # 退一点，看得清
        return await self._confirm(f"画面里的{target}是不是已经倒下、被撞翻或被撞开了？")

    def _time_up(self, t0: float) -> bool:
        return time.monotonic() - t0 > self.time_limit_s

    # ---- 外部摄像头：外框 + 位移估朝向 ----
    def _question_boxes(self, target: str) -> str:
        return (f"画面里有一辆履带小坦克和{target}。分别给出它们在画面中的外框，用百分比坐标："
                f"画面左上角是 0,0，右下角是 100,100，外框写成 左,上,右,下。"
                f"只用这个格式回答一行，不要解释：坦克=x1,y1,x2,y2；目标=x1,y1,x2,y2。"
                f"画面里找不到的写成 坦克=无 或 目标=无")

    async def _locate(self, target: str) -> tuple[Box, Box] | str:
        r = await self._ask_parsed(self._question_boxes(target), parse_boxes)
        if r is None:
            return "看不懂视觉模型的回答"
        if r["car"] is None:
            return "画面里找不到小坦克"
        if r["target"] is None:
            return f"没有看到{target}"
        return r["car"], r["target"]

    async def _loop_external(self, target: str, goal: str) -> tuple[bool, str]:
        verb = GOALS.get(goal, "走到")
        t0 = time.monotonic()
        prev: Point | None = None  # 上次前进之前坦克的中心；None = 还没测过车头方向
        moved_ms = 0
        ms_per_pct = None
        misses = stuck = unstick = rams = 0
        for _ in range(self.max_steps):
            if self._time_up(t0):
                break
            r = await self._locate(target)
            if isinstance(r, str):
                misses += 1
                if misses >= MAX_MISSES:
                    return False, r
                continue  # 偶尔被挡住或模型看漏，再拍一次
            misses = 0
            car, tgt = r
            heading = None
            if prev is not None:
                d = (car.center[0] - prev[0], car.center[1] - prev[1])
                if math.hypot(*d) < MIN_MOVE_PCT:
                    stuck += 1
                    if stuck >= 2:  # 两次往前走都没挪动：被卡住了，后退再换个角度
                        if unstick >= MAX_UNSTICK:
                            return False, "车好像被卡住了，或者摄像头太低、太远看不出它在动，先停下"
                        unstick += 1
                        stuck = 0
                        await self._do(lambda: self.tank.move("backward", 600, SPEED))
                        await self._turn(60 if unstick % 2 else -60)
                    prev, moved_ms = car.center, self.probe_ms * 2
                    await self._forward(moved_ms)
                    continue
                stuck = 0
                heading = d
                ms_per_pct = moved_ms / math.hypot(*d)
            gap = gap_in_cars(car, tgt)
            if goal != "aim" and gap < NEAR_CARS:
                if await self._confirm(f"画面里的履带小坦克是不是已经碰到或紧挨着{target}了（中间几乎没有空隙）？"):
                    if goal == "approach":
                        return True, f"已走到{target}跟前"
                    rams += 1
                    if await self._ram(target):
                        return True, f"已把{target}撞倒了"
                    if rams >= MAX_RAMS:
                        return False, f"撞了 {rams} 次，{target}还没倒"
                    prev = None  # 撞完退了一下，重新测车头方向
                    continue
            if heading is None:  # 还不知道车头朝哪：试探着走一小段
                prev, moved_ms = car.center, self.probe_ms
                await self._forward(moved_ms)
                continue
            fx, fy = car.foot
            angle = turn_needed(heading, (tgt.foot[0] - fx, tgt.foot[1] - fy))
            if goal == "aim" and abs(angle) <= EXT_AIM_TOLERANCE_DEG:
                return True, f"已对准{target}"
            if abs(angle) > EXT_AIM_TOLERANCE_DEG:
                await self._turn(angle)
            if goal == "aim":
                moved_ms = self.probe_ms  # 原地转完看不出新朝向，再试探一小段确认
            else:
                remaining = max(math.dist(car.center, tgt.center) - (car.w + tgt.w) / 2, 0)
                moved_ms = int(min(max(remaining * ms_per_pct * 0.7, STEP_MIN_MS), STEP_MAX_MS))
            prev = car.center
            await self._forward(moved_ms)
        return False, f"找了 {round(time.monotonic() - t0)} 秒还没{verb}{target}，先停下"

    # ---- 车载前视：偏角 ----
    async def _loop_front(self, target: str, goal: str) -> tuple[bool, str]:
        verb = GOALS.get(goal, "走到")
        t0 = time.monotonic()
        q = (f"这是装在小车上朝前拍的画面，水平视角约 {FRONT_FOV} 度。画面里有{target}吗？"
             f"它偏离画面中心多少度（左边为负、右边为正）？离得近吗（占画面宽度三分之一以上算近）？"
             f"只用这个格式回答一行，不要解释：找到=是或否；偏角=整数；近=是或否")
        scanned = misses = rams = 0
        for _ in range(self.max_steps):
            if self._time_up(t0):
                break
            r = await self._ask_parsed(q, parse_front)
            if r is None:
                misses += 1
                if misses >= MAX_MISSES:
                    return False, f"看不懂视觉模型的回答，{target}的寻找中止"
                continue
            misses = 0
            if not r["found"]:
                if scanned < 360:
                    await self._turn(-SCAN_ANGLE)
                    scanned += SCAN_ANGLE
                    continue
                return False, f"转了一圈也没有看到{target}"
            scanned = 0
            off = r["offset"]
            if abs(off) > AIM_TOLERANCE_DEG:
                await self._turn(-max(abs(off), 10) if off > 0 else max(abs(off), 10))
                continue
            if goal == "aim":
                return True, f"已对准{target}"
            if r["near"]:
                if goal == "approach":
                    return True, f"已走到{target}跟前"
                rams += 1
                if await self._ram(target):
                    return True, f"已把{target}撞倒了"
                if rams >= MAX_RAMS:
                    return False, f"撞了 {rams} 次，{target}还没倒"
                continue
            await self._forward(FRONT_STEP_MS)
        return False, f"找了 {round(time.monotonic() - t0)} 秒还没{verb}{target}，先停下"
