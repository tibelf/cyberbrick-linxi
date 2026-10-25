"""自由闲逛（self.tank.wander）：一直逛下去，只有用户说“停”（或回车、网页急停）才停。

流水线：车往前走的同时就拍照分析，算好下一步先排进队列，当前这段一走完马上接上，不停下来“发呆”。
每次看完：前方空 → 排下一段（多数时候顺着走，偶尔换方向）；前方堵、四周堵死、或走着却没挪动（撞到/卡住）
→ 立刻刹车，转向空的方向或后退掉头。偶尔停下来做个小动作（扭一扭、点头、摇炮管、看看）。

两种摄像头摆法与 seek 相同：
- overhead（摄像头固定在场地旁边）：问坦克位置和“周围哪些方向是空地”（按画面方向说）；车头方向由
  前进前后的位移算出，转向后按转过的角度推算。坦克靠近画面边缘时不往外走，免得开出画面看不见。
- front（摄像头装在车上）：问正前方、左前方、右前方通不通。

安全：
- 只用移动、转向、炮管和情绪小动作，**闲逛时绝不开火**。
- 每个动作仍经过 TankExecutor 的限速和时长上限；最多提前排一段，走的过程中一直在看，看到障碍立刻刹车。
- 看不清（拍照失败、回答格式不对）时先停一下；连续 3 次看不清就改成**闭眼慢逛**（降速、走短段、常转向），
  并在后台每隔几秒试一次摄像头，看清了自动回到边看边逛。闲逛不会因为看不清而结束，只有说“停”才停
  （2026-10-25 用户反馈：关了摄像头闲逛就被打断）。没开摄像头（ask=None）时一开始就是闭眼慢逛。
- 和平台断开时（听不到“停”了）先停在原地，重连后自动继续。
"""

import asyncio
import math
import random
import re
import time
from typing import Awaitable, Callable

from .seek import MIN_MOVE_PCT, _point, turn_needed
from .tank import TankExecutor

SPEED = 70  # 闲逛速度：比冲刺慢，留出看路的余量
SEG_MS = (1200, 2500)  # 每段前进的时长范围（步子大一点，看起来不呆）
PROBE_MS = 700  # 还不知道车头朝哪时，先走这么一小段
LOOK_GAP_S = 0.6  # 两次看图之间至少隔这么久，免得视觉接口太快时刷太多次
STUCK_MIN_S = 0.4  # 两张照片之间一直在往前走超过这么久、却几乎没挪动，才算卡住
EDGE_PCT = 12.0  # 坦克离画面边缘小于这个（%）时，不再往那个方向走
KEEP_HEADING = 0.7  # 有空地时，顺着当前方向继续走的概率
FLOURISH = 0.15  # 每排一段之前，先停下来做个小动作的概率
MAX_BLIND = 3  # 连续看不清几次就改成闭眼慢逛
BLIND_SPEED = 60  # 闭眼慢逛：速度低一点，撞到也不疼（再低履带带不动）
BLIND_SEG_MS = (500, 1100)  # 走短段，经常换方向，不会一头扎进角落里
BLIND_TURN = 0.5  # 每段之前转个向的概率
BLIND_RETRY_S = 4.0
MAP_RETRY_S = 15.0  # 地图模式看不到锚点后，隔这么久再试  # 闭眼时每隔这么久在后台试一次摄像头

DIRS = {  # 画面方向（x 向右，y 向下）
    "上": (0, -1), "下": (0, 1), "左": (-1, 0), "右": (1, 0),
    "左上": (-1, -1), "右上": (1, -1), "左下": (-1, 1), "右下": (1, 1),
}

Q_EXTERNAL = ("画面里有一辆履带小坦克。给出它中心在画面中的位置（百分比坐标：左上角 0,0，右下角 100,100），"
    "再看它周围一个车身长以内，哪些方向是空地、没有障碍物、墙、桌腿或台阶"
    "（方向按画面说：上、下、左、右、左上、右上、左下、右下）。"
    "只用这个格式回答一行，不要解释：坦克=x,y；空地=上,左上。四周都被挡住写 空地=无；看不到坦克写 坦克=无")
Q_FRONT = ("这是装在小车上朝前拍的画面。看前方大约 30 厘米以内：正前方、左前方、右前方有没有障碍物"
    "（墙、桌腿、箱子、台阶等）？只用这个格式回答一行，不要解释：前方=通或堵；左=通或堵；右=通或堵")

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
                 seg_ms: tuple[int, int] = SEG_MS, probe_ms: int = PROBE_MS, look_gap_s: float = LOOK_GAP_S):
        self.tank = tank
        self.ask = ask
        self.say = say
        self.view = view
        self.paused = paused or (lambda: False)
        self.rng = rng or random.Random()
        self.seg_ms = seg_ms
        self.probe_ms = probe_ms
        self.look_gap_s = look_gap_s
        self.navigator = None  # navigate.Navigator：有地图时在地图上随机挑空地、沿安全路线逛
        self._map_retry_at = 0.0  # 看不到锚点时，过一会儿再试地图模式
        self._reset()

    def __call__(self) -> str:
        self._reset()
        return self.tank.start_mission("自由闲逛", self.run) + "（会一直逛下去，说“停”才停）"

    def _reset(self):
        self.last = None  # 上一张照片：(坦克位置, 运动快照, 时间)
        self.heading = None  # 车头方向（画面坐标向量）
        self.heading_tt = 0.0  # heading 对应的 tank.turn_total；之后转过的角度靠它推算

    # ---- 基本动作 ----
    async def _do(self, start: Callable[[], str | None]):
        if start() is not None:
            await self.tank.wait_idle()

    def _queue_forward(self, ms: int):
        self.tank.move("forward", ms, SPEED)

    def _queue_turn(self, degrees: float):
        """正数左转。小于 10° 的忽略。"""
        if abs(degrees) >= 10:
            self.tank.turn("left" if degrees > 0 else "right", round(min(abs(degrees), 360)))

    async def _brake_and_turn(self, degrees: float):
        await self.tank.halt_now()
        self._queue_turn(degrees)
        await self.tank.wait_idle()
        self.last = None

    async def _back_off(self):
        await self.tank.halt_now()
        await self._do(lambda: self.tank.move("backward", 600, SPEED))
        await self._brake_and_turn(self.rng.choice((-1, 1)) * self.rng.randint(120, 180))

    async def _flourish(self):
        """停下来做个小动作，看起来像在玩。扭动成对抵消，不改变车头方向。"""
        await self.tank.wait_idle()
        pick = self.rng.choice(["happy", "thinking", "surprised", "barrel", "look"])
        if pick == "barrel":
            await self._do(lambda: self.tank.barrel("up", 300))
            await self._do(lambda: self.tank.barrel("down", 300))
        elif pick == "look":
            await asyncio.sleep(self.rng.uniform(0.8, 2.0))  # 停下来“看看”
        else:
            await self._do(lambda: self.tank.emote(pick, from_mission=True))
        self.last = None

    async def _wait_if_paused(self):
        if not self.paused():
            return
        await self.tank.halt_now()
        print("⏸ 和平台断开了，闲逛暂停，重连后继续")
        while self.paused():
            await asyncio.sleep(0.5)
        print("▶ 已重连，继续闲逛")
        self.last = None

    def _snapshot(self):
        t = self.tank
        return t.motion_epoch, t.idle_epoch, t.busy

    def _current_heading(self):
        if self.heading is None:
            return None
        return rotate(self.heading, self.tank.turn_total - self.heading_tt)

    # ---- 有地图：在地图上逛 ----
    async def _map_roam_once(self) -> bool:
        """在地图上随便挑一块能到的空地，沿绕开障碍物的路线开过去。看不到锚点/车时返回 False，交回看图模式。"""
        nav = self.navigator
        start = nav.car[:2] if nav.car else None
        goal = nav.map.random_free_point(self.rng, near=start, min_cm=40)
        if goal is None:
            return False
        ok, msg = await nav.goto(goal, stop_cm=12, time_limit_s=30)
        if not ok:
            print(f"[wander] 地图上这一段没走成：{msg}")
            if "看不到" in msg:
                return False
        if self.rng.random() < FLOURISH * 2:
            await self._flourish()
        return True

    # ---- 闭眼慢逛 ----
    async def _blind_step(self):
        if self.tank.queued:
            return  # 保持只提前排一段
        if self.rng.random() < FLOURISH:
            await self._flourish()
        if self.rng.random() < BLIND_TURN:
            self._queue_turn(self.rng.choice((-1, 1)) * self.rng.randint(30, 150))
        self.tank.move("forward", self.rng.randint(*BLIND_SEG_MS), BLIND_SPEED)

    async def _camera_ok(self) -> bool:
        """后台试一次摄像头：拍得到、视觉模型按格式回答、（外部摄像头）找得到车，就算看清了。"""
        try:
            if self.view == "overhead":
                r = await self._ask_parsed(Q_EXTERNAL, parse_surroundings)
                return r is not None and r["car"] is not None
            return await self._ask_parsed(Q_FRONT, parse_front_path) is not None
        except Exception:  # noqa: BLE001
            return False

    async def _run_blind(self):
        """闭眼慢逛，直到摄像头又看得清（返回）或被 stop() 取消。"""
        probe = None
        last_try = time.monotonic()
        try:
            while True:
                await self._wait_if_paused()
                if self.ask and probe is None and time.monotonic() - last_try >= BLIND_RETRY_S:
                    probe = asyncio.create_task(self._camera_ok())
                if probe is not None and probe.done():
                    if probe.result():
                        await self.tank.halt_now()
                        self.last = None
                        print("👀 又看得清了，接着边看边逛")
                        if self.say:
                            await self.say("又看得清了，接着边看边逛")
                        return
                    probe, last_try = None, time.monotonic()
                await self._blind_step()
                await asyncio.sleep(0.2)
        finally:
            if probe is not None:
                probe.cancel()

    # ---- 主循环 ----
    async def run(self):
        blind = 0
        last_look = 0.0
        if self.ask is None:
            print("🙈 没有摄像头：闭着眼慢慢逛，说“停”才停")
            await self._run_blind()  # 没有摄像头时永远不会返回
        while True:
            await self._wait_if_paused()
            if self.navigator and self.navigator.map and time.monotonic() >= self._map_retry_at:
                if await self._map_roam_once():
                    continue  # 有地图：按地图逛
                self._map_retry_at = time.monotonic() + MAP_RETRY_S  # 看不到锚点：先用看图模式逛一会儿
            wait = self.look_gap_s - (time.monotonic() - last_look)
            if wait > 0:
                await asyncio.sleep(wait)
            last_look = time.monotonic()
            snap = self._snapshot()  # 拍照这一刻车在做什么
            try:
                if self.view == "overhead":
                    ok = await self._step_external(snap, last_look)
                else:
                    ok = await self._step_front()
            except asyncio.CancelledError:
                raise  # 用户说了“停”
            except Exception as e:  # noqa: BLE001 — 网络抖动等意外不能让闲逛悄悄结束，记一次“看不清”
                print(f"[wander] 这一步出错：{e!r}")
                ok = False
            if ok:
                blind = 0
                continue
            await self.tank.halt_now()  # 先停一下，再看一次
            self.last = None
            blind += 1
            if blind >= MAX_BLIND:
                msg = "看不清画面了，先闭着眼慢慢逛，摄像头好了我会自动接着看"
                print(f"🙈 {msg}")
                if self.say:
                    await self.say(msg)
                await self._run_blind()
                blind = 0
                continue
            await asyncio.sleep(1)

    async def _ask_parsed(self, question: str, parse):
        try:
            for _ in range(2):
                r = parse(await self.ask(question))
                if r is not None:
                    return r
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 拍照或视觉接口出错，按“看不清”处理
            print(f"[wander] 看图失败：{e}")
        return None

    async def _plan_next(self, turn_deg: float = 0.0, flourish_ok: bool = True):
        """前方是空的：如果还没有排下一段，就排上（转向可选）。已经排了一段就不再加，保持只提前一段。"""
        if self.tank.queued:
            return
        if flourish_ok and self.rng.random() < FLOURISH:
            await self._flourish()
        self._queue_turn(turn_deg)
        self._queue_forward(self.rng.randint(*self.seg_ms))

    async def _step_external(self, snap, t) -> bool:
        q = Q_EXTERNAL
        r = await self._ask_parsed(q, parse_surroundings)
        if r is None or r["car"] is None:
            return False
        car, free = r["car"], set(r["free"])
        last, self.last = self.last, (car, snap, t)
        # 上一张到这一张之间只做过前进（没转、中途没停；上一张时可以还没开动）：位移就是车头方向
        if last and last[1][:2] == snap[:2] and snap[2]:
            move = (car[0] - last[0][0], car[1] - last[0][1])
            if math.hypot(*move) < MIN_MOVE_PCT:
                if t - last[2] >= STUCK_MIN_S:
                    await self._back_off()  # 一直在走却没挪动：撞到东西或卡住了
                return True
            self.heading, self.heading_tt = move, self.tank.turn_total
        # 靠近画面边缘时不往外走
        x, y = car
        free = {d for d in free if not ((x < EDGE_PCT and DIRS[d][0] < 0) or (x > 100 - EDGE_PCT and DIRS[d][0] > 0)
                                        or (y < EDGE_PCT and DIRS[d][1] < 0) or (y > 100 - EDGE_PCT and DIRS[d][1] > 0))}
        if not free:
            await self._back_off()
            return True
        heading = self._current_heading()
        if heading is None:
            if not self.tank.busy:  # 还不知道车头朝哪：先走一小段，下一张照片就知道了
                self._queue_forward(self.probe_ms)
            return True
        ahead = [d for d in free if abs(turn_needed(heading, DIRS[d])) <= 45]
        if not ahead:
            # 前方被挡住：立刻刹车，转向一个空的方向再走
            await self._brake_and_turn(turn_needed(heading, DIRS[self.rng.choice(sorted(free))]))
            self._queue_forward(self.rng.randint(*self.seg_ms))
            return True
        turn = 0.0 if self.rng.random() < KEEP_HEADING else turn_needed(heading, DIRS[self.rng.choice(sorted(free))])
        await self._plan_next(turn)
        return True

    async def _step_front(self) -> bool:
        q = Q_FRONT
        r = await self._ask_parsed(q, parse_front_path)
        if r is None:
            return False
        if r["前方"]:
            turn = 0.0 if self.rng.random() < KEEP_HEADING else self.rng.choice((-1, 1)) * self.rng.randint(15, 40)
            await self._plan_next(turn)
        elif r["左"] or r["右"]:
            side = self.rng.choice([s for s, ok in ((1, r["左"]), (-1, r["右"])) if ok])
            await self._brake_and_turn(side * self.rng.randint(60, 110))
        else:
            await self._back_off()
        return True
