"""看图闭环寻的（self.tank.seek）：拍照 → 视觉模型按固定格式回答 → 转向/前进 → 动完再拍，直到对准或走到。

为什么在本地跑：平台一轮对话通常只执行一次工具调用就等用户说下一句，大模型没法自己“边看边调”。
所以大模型只调用一次 seek，循环在这里完成，结束后用 say() 播报结果。说“停”会取消整个任务。

动作一律经过 TankExecutor（move/turn/fire），限速、时长上限、发射锁定和冷却照常生效。
"""

import re
from typing import Awaitable, Callable

from .tank import TankExecutor

MAX_STEPS = 8
CAR_LEN_MS = 600  # 速度 80 时前进一个车身长大约多少毫秒（待实测校准）
STEP_MAX_MS = 1500  # 每一步最多走这么久，走完再拍照确认
SCAN_ANGLE = 45  # 前视看不到目标时，每次转这么多度再找
FRONT_FOV = 60  # 前视摄像头水平视角（度），只用来提示视觉模型
AIM_TOLERANCE_DEG = 8  # 前视：偏角在这个范围内算对准

Ask = Callable[[str], Awaitable[str]]
Say = Callable[[str], Awaitable[None]]


def _yes(s: str | None) -> bool:
    return bool(s) and s.strip() in ("是", "有", "yes", "true", "True")


def parse_overhead(answer: str) -> dict | None:
    """解析 “找到=是；方向=3；距离=2” 这类回答。找不到格式返回 None。"""
    found = re.search(r"找到\s*[=:：]\s*(是|否)", answer)
    if not found:
        return None
    if found.group(1) == "否":
        return {"found": False}
    clock = re.search(r"方向\s*[=:：]\s*(\d{1,2})", answer)
    dist = re.search(r"距离\s*[=:：]\s*(\d+(?:\.\d+)?)", answer)
    if not clock or not (1 <= int(clock.group(1)) <= 12):
        return None
    return {"found": True, "clock": int(clock.group(1)), "dist": float(dist.group(1)) if dist else None}


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


def clock_to_turn(clock: int) -> tuple[str, int] | None:
    """12 点钟 = 正前方。返回 (left/right, 角度)，已经在正前方返回 None。"""
    deg = (clock % 12) * 30
    if deg == 0:
        return None
    return ("right", deg) if deg <= 180 else ("left", 360 - deg)


class Seeker:
    def __init__(self, tank: TankExecutor, ask: Ask, say: Say | None = None, view: str = "overhead",
                 car_len_ms: int = CAR_LEN_MS, max_steps: int = MAX_STEPS):
        self.tank = tank
        self.ask = ask
        self.say = say
        self.view = view
        self.car_len_ms = car_len_ms
        self.max_steps = max_steps

    # MCP 工具入口：立即返回，循环在后台跑
    def __call__(self, target: str, goal: str = "approach", fire: bool = False) -> str:
        desc = f"{'走到' if goal == 'approach' else '对准'}{target}" + ("并开火" if fire else "")
        return self.tank.start_mission(desc, lambda: self.run(target, goal, fire)) + \
            f"（最多 {self.max_steps} 步，边看边调整，完成后会播报结果；说“停”随时中止）"

    def _question(self, target: str) -> str:
        if self.view == "overhead":
            return (f"这是从正上方往下拍的俯视图，画面里有一辆履带小坦克，以它炮管指向的方向为 12 点钟。"
                    f"{target}在小坦克的几点钟方向？离小坦克大约几个车身长？"
                    f"只用这个格式回答一行，不要解释：找到=是或否；方向=1到12的整数；距离=数字")
        return (f"这是装在小车上朝前拍的画面，水平视角约 {FRONT_FOV} 度。画面里有{target}吗？"
                f"它偏离画面中心多少度（左边为负、右边为正）？离得近吗（占画面宽度三分之一以上算近）？"
                f"只用这个格式回答一行，不要解释：找到=是或否；偏角=整数；近=是或否")

    async def _look(self, target: str) -> dict | None:
        parse = parse_overhead if self.view == "overhead" else parse_front
        q = self._question(target)
        for _ in range(2):  # 格式不对再问一次
            r = parse(await self.ask(q))
            if r is not None:
                return r
        return None

    async def _do(self, start: Callable[[], str]):
        start()
        await self.tank.wait_idle()

    async def run(self, target: str, goal: str, fire: bool) -> str:
        ok, result = await self._loop(target, goal)
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

    async def _loop(self, target: str, goal: str) -> tuple[bool, str]:
        scanned = 0
        for step in range(1, self.max_steps + 1):
            r = await self._look(target)
            if r is None:
                return False, f"看不懂视觉模型的回答，{target}的寻找中止"
            if not r["found"]:
                if self.view == "front" and scanned < 360:
                    await self._do(lambda: self.tank.turn("right", SCAN_ANGLE))
                    scanned += SCAN_ANGLE
                    continue
                return False, f"没有看到{target}"
            scanned = 0
            if self.view == "overhead":
                turn = clock_to_turn(r["clock"])
                close = r["dist"] is not None and r["dist"] <= 1
                if turn is None and (goal == "aim" or close):
                    return True, f"已{'对准' if goal == 'aim' else '走到'}{target}（第 {step} 步）"
                if turn:
                    await self._do(lambda: self.tank.turn(*turn))
                    if goal == "aim" or r["clock"] not in (11, 12, 1):
                        continue  # 转得多，先拍照确认方向再走
                dist = r["dist"] if r["dist"] is not None else 1
                ms = int(min(max(dist - 0.5, 0.5) * self.car_len_ms, STEP_MAX_MS))
                await self._do(lambda: self.tank.move("forward", ms, 80))
            else:
                off = r["offset"]
                if abs(off) > AIM_TOLERANCE_DEG:
                    await self._do(lambda: self.tank.turn("right" if off > 0 else "left", max(abs(off), 10)))
                    continue
                if goal == "aim" or r["near"]:
                    return True, f"已{'对准' if goal == 'aim' else '走到'}{target}（第 {step} 步）"
                await self._do(lambda: self.tank.move("forward", min(self.car_len_ms, STEP_MAX_MS), 80))
        return False, f"走了 {self.max_steps} 步还没到{target}，先停下"
