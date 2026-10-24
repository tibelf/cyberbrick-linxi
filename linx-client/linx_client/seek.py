"""看图闭环寻的（self.tank.seek）：拍照 → 视觉模型按固定格式回答 → 转向/前进 → 动完再拍，直到对准或走到。

为什么在本地跑：平台一轮对话通常只执行一次工具调用就等用户说下一句，大模型没法自己“边看边调”。
所以大模型只调用一次 seek，循环在这里完成，结束后用 say() 播报结果。说“停”会取消整个任务。

两种摄像头摆法：
- overhead（摄像头固定在场地旁边，俯拍或斜拍）：视觉模型最不擅长判断车头朝向，所以只问它
  “坦克和目标各在画面哪里”（百分比坐标）。车头方向由程序自己算：前进一小段，比较前后两张照片里
  坦克的位置，位移方向就是车头。再算车头方向和“坦克→目标”方向差多少度，转过去、往前走，重复。
  前进时顺便标定“画面上移动 1% 要走多少毫秒”，用来估下一步走多久。
  （2026-10-25 实测：问“目标在几点钟方向”时，低机位侧拍下模型每次都答 4 点钟，车原地打转，所以改成这种做法。）
- front（摄像头装在车上朝前）：问目标偏离画面中心多少度，直接换算成转向角。

动作一律经过 TankExecutor（move/turn/fire），限速、时长上限、发射锁定和冷却照常生效。
"""

import math
import re
from typing import Awaitable, Callable

from .tank import TankExecutor

MAX_STEPS = 10
SPEED = 80
PROBE_MS = 500  # 用来看出车头方向的试探前进
MIN_MOVE_PCT = 2.0  # 坦克在画面里至少移动这么多（%）才算看清了方向
ARRIVE_PCT = 10.0  # 坦克和目标中心在画面里相距小于这个（%）算走到
EXT_AIM_TOLERANCE_DEG = 15  # 外部摄像头：车头和目标方向差在这个范围内算对准（透视误差大，放宽）
STEP_MIN_MS, STEP_MAX_MS = 300, 1500  # 每一步前进的范围，走完再拍照确认
SCAN_ANGLE = 45  # 前视看不到目标时，每次转这么多度再找
FRONT_FOV = 60  # 前视摄像头水平视角（度），只用来提示视觉模型
AIM_TOLERANCE_DEG = 8  # 前视：偏角在这个范围内算对准
FRONT_STEP_MS = 600

Ask = Callable[[str], Awaitable[str]]
Say = Callable[[str], Awaitable[None]]
Point = tuple[float, float]


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


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


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
                 max_steps: int = MAX_STEPS, probe_ms: int = PROBE_MS):
        self.tank = tank
        self.ask = ask
        self.say = say
        self.view = view
        self.max_steps = max_steps
        self.probe_ms = probe_ms

    # MCP 工具入口：立即返回，循环在后台跑
    def __call__(self, target: str, goal: str = "approach", fire: bool = False) -> str:
        desc = f"{'走到' if goal == 'approach' else '对准'}{target}" + ("并开火" if fire else "")
        return self.tank.start_mission(desc, lambda: self.run(target, goal, fire)) + \
            f"（最多 {self.max_steps} 步，边看边调整，完成后会播报结果；说“停”随时中止）"

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

    async def _forward(self, ms: int):
        await self._do(lambda: self.tank.move("forward", ms, SPEED))

    async def _ask_parsed(self, question: str, parse) -> dict | None:
        for _ in range(2):  # 格式不对再问一次
            r = parse(await self.ask(question))
            if r is not None:
                return r
        return None

    # ---- 外部摄像头：位置 + 位移估朝向 ----
    def _question_positions(self, target: str) -> str:
        return (f"画面里有一辆履带小坦克和{target}。分别给出它们中心在画面中的位置，用百分比坐标："
                f"画面左上角是 0,0，右下角是 100,100。"
                f"只用这个格式回答一行，不要解释：坦克=x,y；目标=x,y。画面里找不到的写成 坦克=无 或 目标=无")

    async def _locate(self, target: str) -> tuple[Point, Point] | str:
        r = await self._ask_parsed(self._question_positions(target), parse_positions)
        if r is None:
            return "看不懂视觉模型的回答"
        if r["car"] is None:
            return "画面里找不到小坦克"
        if r["target"] is None:
            return f"没有看到{target}"
        return r["car"], r["target"]

    async def _loop_external(self, target: str, goal: str) -> tuple[bool, str]:
        done = "对准" if goal == "aim" else "走到"
        r = await self._locate(target)
        if isinstance(r, str):
            return False, r
        prev, tgt = r
        if goal == "approach" and _dist(prev, tgt) < ARRIVE_PCT:
            return True, f"已经在{target}旁边了"
        moved_ms = self.probe_ms
        await self._forward(moved_ms)  # 先试探着走一小段，看车头朝哪
        stuck = 0
        for step in range(1, self.max_steps + 1):
            r = await self._locate(target)
            if isinstance(r, str):
                return False, r
            car, tgt = r
            if goal == "approach" and _dist(car, tgt) < ARRIVE_PCT:
                return True, f"已{done}{target}（第 {step} 步）"
            heading = (car[0] - prev[0], car[1] - prev[1])
            shift = math.hypot(*heading)
            if shift < MIN_MOVE_PCT:
                stuck += 1
                if stuck >= 2:
                    return False, "看不出小坦克往哪个方向走：摄像头可能太低或离得太远，或者车被卡住了"
                prev, moved_ms = car, self.probe_ms * 2
                await self._forward(moved_ms)
                continue
            stuck = 0
            ms_per_pct = moved_ms / shift
            angle = turn_needed(heading, (tgt[0] - car[0], tgt[1] - car[1]))
            if goal == "aim" and abs(angle) <= EXT_AIM_TOLERANCE_DEG:
                return True, f"已{done}{target}（第 {step} 步）"
            if abs(angle) > EXT_AIM_TOLERANCE_DEG:
                await self._do(lambda: self.tank.turn("left" if angle > 0 else "right", round(abs(angle))))
            if goal == "aim":
                moved_ms = self.probe_ms  # 原地转完看不出新朝向，再试探一小段确认
            else:
                gap = _dist(car, tgt) - ARRIVE_PCT * 0.5
                moved_ms = int(min(max(gap * ms_per_pct * 0.7, STEP_MIN_MS), STEP_MAX_MS))
            prev = car
            await self._forward(moved_ms)
        return False, f"走了 {self.max_steps} 步还没到{target}，先停下"

    # ---- 车载前视：偏角 ----
    async def _loop_front(self, target: str, goal: str) -> tuple[bool, str]:
        done = "对准" if goal == "aim" else "走到"
        q = (f"这是装在小车上朝前拍的画面，水平视角约 {FRONT_FOV} 度。画面里有{target}吗？"
             f"它偏离画面中心多少度（左边为负、右边为正）？离得近吗（占画面宽度三分之一以上算近）？"
             f"只用这个格式回答一行，不要解释：找到=是或否；偏角=整数；近=是或否")
        scanned = 0
        for step in range(1, self.max_steps + 1):
            r = await self._ask_parsed(q, parse_front)
            if r is None:
                return False, f"看不懂视觉模型的回答，{target}的寻找中止"
            if not r["found"]:
                if scanned < 360:
                    await self._do(lambda: self.tank.turn("right", SCAN_ANGLE))
                    scanned += SCAN_ANGLE
                    continue
                return False, f"没有看到{target}"
            scanned = 0
            off = r["offset"]
            if abs(off) > AIM_TOLERANCE_DEG:
                await self._do(lambda: self.tank.turn("right" if off > 0 else "left", max(abs(off), 10)))
                continue
            if goal == "aim" or r["near"]:
                return True, f"已{done}{target}（第 {step} 步）"
            await self._forward(FRONT_STEP_MS)
        return False, f"走了 {self.max_steps} 步还没到{target}，先停下"
