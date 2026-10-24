"""自由闲逛（self.tank.wander）：一直逛下去，只有用户说“停”（或回车、网页急停）才停。

每一小段：拍照 → 视觉模型说出周围哪些方向是空地 → 选一个空的方向（多数时候顺着当前方向走，
偶尔随机换方向）→ 转过去 → 往前走一小段；中间随机穿插小动作（扭一扭、点头、摇炮管、停下来看看）。
前进后在画面里几乎没挪动，说明撞到东西或卡住了：后退、掉头。四周都堵死了也后退、掉头。

两种摄像头摆法与 seek 相同：
- overhead（摄像头固定在场地旁边）：问坦克位置和“周围哪些方向是空地”（按画面方向说）；车头方向由
  前进前后的位移算出，转向后按转过的角度推算。坦克靠近画面边缘时不往外走，免得开出画面看不见。
- front（摄像头装在车上）：问正前方、左前方、右前方通不通。

安全：
- 只用移动、转向、炮管和情绪小动作，**闲逛时绝不开火**。
- 每个动作仍经过 TankExecutor 的限速和时长上限；每段都短（≤1.2s），走完就重新看一眼。
- 和平台断开时（听不到“停”了）先停在原地，重连后自动继续；看不清画面连续 3 次就停下并播报。
"""

import asyncio
import math
import random
import re
from typing import Awaitable, Callable

from .seek import MIN_MOVE_PCT, _point, turn_needed
from .tank import TankExecutor

SPEED = 70  # 闲逛速度：比冲刺慢，留出看路的余量
SEG_MS = (400, 1200)  # 每段前进的时长范围
EDGE_PCT = 12.0  # 坦克离画面边缘小于这个（%）时，不再往那个方向走
KEEP_HEADING = 0.7  # 有空地时，顺着当前方向继续走的概率
FLOURISH = 0.25  # 每段之后做一个小动作的概率
MAX_BLIND = 3  # 连续看不清几次就停下

DIRS = {  # 画面方向（x 向右，y 向下）
    "上": (0, -1), "下": (0, 1), "左": (-1, 0), "右": (1, 0),
    "左上": (-1, -1), "右上": (1, -1), "左下": (-1, 1), "右下": (1, 1),
}

Ask = Callable[[str], Awaitable[str]]
Say = Callable[[str], Awaitable[None]]


def parse_surroundings(answer: str) -> dict | None:
    """解析 “坦克=40,60；空地=上,左上”。空地=无 表示四周都堵住。格式不对返回 None。"""
    car = _point("坦克", answer)
    m = re.search(r"空地\s*[=:：]\s*([^；;。\n]*)", answer)
    if car is False or not m:
        return None
    words = re.split(r"[,，、\s/和]+", m.group(1).strip())
    return {"car": car, "free": {w for w in words if w in DIRS}}


def parse_front_path(answer: str) -> dict | None:
    """解析 “前方=通；左=堵；右=通”。"""
    out = {}
    for key in ("前方", "左", "右"):
        m = re.search(key + r"\s*[=:：]\s*(通|堵)", answer)
        if not m:
            return None
        out[key] = m.group(1) == "通"
    return out


def rotate(heading: tuple[float, float], degrees: float) -> tuple[float, float]:
    """画面坐标里的方向向量转过 degrees（正数 = 左转，与 turn_needed 一致）。"""
    a = math.radians(degrees)
    x, y = heading[0], -heading[1]
    return x * math.cos(a) - y * math.sin(a), -(x * math.sin(a) + y * math.cos(a))


class Wanderer:
    def __init__(self, tank: TankExecutor, ask: Ask, say: Say | None = None, view: str = "overhead",
                 paused: Callable[[], bool] | None = None, rng: random.Random | None = None,
                 seg_ms: tuple[int, int] = SEG_MS):
        self.tank = tank
        self.ask = ask
        self.say = say
        self.view = view
        self.paused = paused or (lambda: False)
        self.rng = rng or random.Random()
        self.seg_ms = seg_ms

    def __call__(self) -> str:
        return self.tank.start_mission("自由闲逛", self.run) + "（会一直逛下去，说“停”才停）"

    # ---- 基本动作 ----
    async def _do(self, start: Callable[[], str | None]):
        if start() is not None:
            await self.tank.wait_idle()

    async def _forward(self, ms: int):
        await self._do(lambda: self.tank.move("forward", ms, SPEED))

    async def _turn(self, degrees: float):
        """正数左转。小于 10° 的忽略。"""
        if abs(degrees) >= 10:
            await self._do(lambda: self.tank.turn("left" if degrees > 0 else "right", round(min(abs(degrees), 360))))

    async def _back_off(self):
        await self._do(lambda: self.tank.move("backward", 600, SPEED))
        await self._turn(self.rng.choice((-1, 1)) * self.rng.randint(120, 180))

    async def _flourish(self):
        """随手做个小动作，看起来像在玩。都不改变车头方向（扭动成对抵消）。"""
        pick = self.rng.choice(["happy", "thinking", "surprised", "barrel", "look"])
        if pick == "barrel":
            await self._do(lambda: self.tank.barrel("up", 300))
            await self._do(lambda: self.tank.barrel("down", 300))
        elif pick == "look":
            await asyncio.sleep(self.rng.uniform(0.8, 2.0))  # 停下来“看看”
        else:
            await self._do(lambda: self.tank.emote(pick, from_mission=True))

    async def _wait_if_paused(self):
        if not self.paused():
            return
        await self.tank.halt_now()
        print("⏸ 和平台断开了，闲逛暂停，重连后继续")
        while self.paused():
            await asyncio.sleep(0.5)
        print("▶ 已重连，继续闲逛")

    # ---- 主循环 ----
    async def run(self):
        blind = 0
        state = {"heading": None, "prev": None}
        while True:
            await self._wait_if_paused()
            step = self._step_external if self.view == "overhead" else self._step_front
            try:
                ok = await step(state)
            except asyncio.CancelledError:
                raise  # 用户说了“停”
            except Exception as e:  # noqa: BLE001 — 网络抖动等意外不能让闲逛悄悄结束，记一次“看不清”
                print(f"[wander] 这一步出错：{e!r}")
                await self.tank.halt_now()
                state.update(prev=None, heading=None)
                ok = False
            if ok:
                blind = 0
            else:
                blind += 1
                if blind >= MAX_BLIND:
                    msg = "看不清画面了，先停下来。检查一下摄像头再叫我"
                    print(f"🛑 {msg}")
                    if self.say:
                        await self.say(msg)
                    return
                await asyncio.sleep(1)
                continue
            if self.rng.random() < FLOURISH:
                await self._flourish()

    async def _ask_parsed(self, question: str, parse):
        try:
            for _ in range(2):
                r = parse(await self.ask(question))
                if r is not None:
                    return r
        except Exception as e:  # noqa: BLE001 — 拍照或视觉接口出错，按“看不清”处理
            print(f"[wander] 看图失败：{e}")
        return None

    async def _step_external(self, state) -> bool:
        q = ("画面里有一辆履带小坦克。给出它中心在画面中的位置（百分比坐标：左上角 0,0，右下角 100,100），"
             "再看它周围一个车身长以内，哪些方向是空地、没有障碍物、墙、桌腿或台阶"
             "（方向按画面说：上、下、左、右、左上、右上、左下、右下）。"
             "只用这个格式回答一行，不要解释：坦克=x,y；空地=上,左上。四周都被挡住写 空地=无；看不到坦克写 坦克=无")
        r = await self._ask_parsed(q, parse_surroundings)
        if r is None or r["car"] is None:
            return False
        car, free = r["car"], set(r["free"])
        prev, heading = state["prev"], state["heading"]
        if prev is not None:
            move = (car[0] - prev[0], car[1] - prev[1])
            if math.hypot(*move) < MIN_MOVE_PCT:
                await self._back_off()  # 走了却没挪动：撞到东西或卡住了
                state.update(prev=None, heading=None)
                return True
            heading = move
        # 靠近画面边缘时不往外走
        x, y = car
        free = {d for d in free if not ((x < EDGE_PCT and DIRS[d][0] < 0) or (x > 100 - EDGE_PCT and DIRS[d][0] > 0)
                                        or (y < EDGE_PCT and DIRS[d][1] < 0) or (y > 100 - EDGE_PCT and DIRS[d][1] > 0))}
        if not free:
            await self._back_off()
            state.update(prev=None, heading=None)
            return True
        if heading is None:
            # 还不知道车头朝哪：往前试探一小段（够短，撞上也没事）
            state.update(prev=car, heading=None)
            await self._forward(self.seg_ms[0])
            return True
        ahead = [d for d in free if abs(turn_needed(heading, DIRS[d])) <= 45]
        if ahead and self.rng.random() < KEEP_HEADING:
            angle = 0.0
        else:
            angle = turn_needed(heading, DIRS[self.rng.choice(sorted(free))])
        await self._turn(angle)
        state.update(prev=car, heading=rotate(heading, angle) if abs(angle) >= 10 else heading)
        await self._forward(self.rng.randint(*self.seg_ms))
        return True

    async def _step_front(self, state) -> bool:
        q = ("这是装在小车上朝前拍的画面。看前方大约 30 厘米以内：正前方、左前方、右前方有没有障碍物"
             "（墙、桌腿、箱子、台阶等）？只用这个格式回答一行，不要解释：前方=通或堵；左=通或堵；右=通或堵")
        r = await self._ask_parsed(q, parse_front_path)
        if r is None:
            return False
        if r["前方"]:
            if self.rng.random() > KEEP_HEADING:
                await self._turn(self.rng.choice((-1, 1)) * self.rng.randint(15, 40))  # 偶尔拐个弯
            await self._forward(self.rng.randint(*self.seg_ms))
        elif r["左"] or r["右"]:
            side = self.rng.choice([s for s, ok in ((1, r["左"]), (-1, r["右"])) if ok])
            await self._turn(side * self.rng.randint(60, 110))
        else:
            await self._back_off()
        return True
