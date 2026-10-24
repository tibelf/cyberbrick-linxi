"""动作队列：按顺序执行、时长控制、安全约束。"停"绕过队列立即生效。"""

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from .backend import TankBackend

MAX_ACTION_MS = 5000
MAX_QUEUE_MS = 15000
FAILSAFE_SLACK_MS = 300
MS_PER_90_AT_FULL = 400  # 满速原地转 90° 需要的毫秒数（2026-10-24 实车落地实测：400ms ≈ 90°）
MAX_BARREL_MS = 1500  # 俯仰有机械限位，转太久会顶住舵机
# 炮管俯仰是 SERVO1 上的连续旋转舵机（原厂右肩摇杆上下控制），速度 45 与原厂一致；
# 正负号决定哪边是“抬高”，2026-10-24 实车发现装反过一次，以实测为准
BARREL_UP_SPEED = 45
GRANDPA_SPEED = 65  # 老爷爷模式的速度上限；再低落地就带不动履带（40% 实测带不动）

# 人格只改变前进/后退的风格；转向按满速校准过角度，不受人格影响
PERSONAS = {"normal": "正常", "soldier": "特种兵", "grandpa": "老爷爷", "drunk": "醉汉"}
EMOTES = {"happy": "开心", "angry": "生气", "sad": "难过", "surprised": "惊讶", "thinking": "思考"}


@dataclass
class Action:
    desc: str
    duration_ms: int
    run: Callable[[], Awaitable[None]]


class TankExecutor:
    def __init__(
        self,
        backend: TankBackend,
        max_speed: int = 60,
        fire_enabled: bool = True,
        fire_pulse_ms: int = 500,
        fire_cooldown_s: float = 1.5,
        max_shots: int = 3,
    ):
        self.backend = backend
        self.max_speed = max_speed
        self.fire_enabled = fire_enabled
        self.fire_pulse_ms = fire_pulse_ms
        self.fire_cooldown_s = fire_cooldown_s
        self.max_shots = max_shots
        self.shots_fired = 0
        self._queue: list[Action] = []
        self._wakeup = asyncio.Event()
        self._current: asyncio.Task | None = None
        self._current_desc: str | None = None
        self._mission: asyncio.Task | None = None  # 闭环任务（如看图寻的），同一时间只有一个
        self._mission_desc: str | None = None
        self._worker: asyncio.Task | None = None
        self._last_fire = 0.0
        self.persona = "normal"

    # ---- 生命周期 ----
    async def start(self):
        await self.backend.connect()
        self._worker = asyncio.create_task(self._run())

    async def close(self):
        await self.stop()
        if self._worker:
            self._worker.cancel()
        await self.backend.close()

    async def _run(self):
        while True:
            await self._wakeup.wait()
            while self._queue:
                action = self._queue.pop(0)
                self._current_desc = action.desc
                self._current = asyncio.create_task(action.run())
                try:
                    await self._current
                except asyncio.CancelledError:
                    if not self._current.cancelled():
                        raise  # worker 自己被取消
                except Exception as e:  # noqa: BLE001
                    print(f"[tank] 动作失败 {action.desc}: {e}")
                    await self._safe_halt()
                finally:
                    self._current = None
                    self._current_desc = None
            self._wakeup.clear()

    # ---- 对外接口（MCP 工具调用这些） ----
    def _enqueue(self, action: Action) -> str:
        queued = sum(a.duration_ms for a in self._queue)
        if queued + action.duration_ms > MAX_QUEUE_MS:
            raise ValueError("动作排太多了，先等当前动作做完或说“停”")
        self._queue.append(action)
        self._wakeup.set()
        return f"已安排：{action.desc}"

    def start_mission(self, desc: str, run: Callable[[], Awaitable[None]]) -> str:
        """启动一个会自己反复下发动作的闭环任务。它内部仍然只通过 move/turn/fire 等方法动车，
        所以安全约束照常生效；stop() 会连任务一起取消。"""
        if self._mission and not self._mission.done():
            raise ValueError(f"正在执行「{self._mission_desc}」，先说“停”再换新任务")
        self._mission_desc = desc
        self._mission = asyncio.create_task(run())
        self._mission.add_done_callback(lambda t: t.cancelled() or t.exception())  # 吞掉未取出的异常警告
        return f"开始：{desc}"

    async def wait_idle(self):
        """等队列里的动作全部做完（闭环任务每一步动完再拍下一张）。"""
        while self._current or self._queue:
            await asyncio.sleep(0.05)

    async def stop(self) -> str:
        if self._mission and not self._mission.done():
            self._mission.cancel()
        self._queue.clear()
        if self._current and not self._current.done():
            self._current.cancel()
        await self._safe_halt()
        return "已停车"

    def _speed(self, speed: int) -> int:
        return max(0, min(int(speed), self.max_speed))

    def move(self, direction: str, duration_ms: int, speed: int) -> str:
        sign = 1 if direction == "forward" else -1
        ms = min(int(duration_ms), MAX_ACTION_MS)
        name = "前进" if sign > 0 else "后退"
        if self.persona == "soldier":
            s = self._speed(100) * sign
            return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}（特种兵）", ms, lambda: self._drive_for(s, s, ms)))
        if self.persona == "grandpa":
            s = min(self._speed(speed), GRANDPA_SPEED) * sign
            total = ms + (ms // 300) * 150
            return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}（老爷爷）", total, lambda: self._shuffle(s, ms)))
        s = self._speed(speed) * sign
        if self.persona == "drunk":
            return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}（醉汉）", ms, lambda: self._stagger(s, ms)))
        return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}", ms, lambda: self._drive_for(s, s, ms)))

    def turn(self, direction: str, angle: int, speed: int = 100) -> str:
        s = self._speed(speed)
        # 只在满速下校准过：低速时履带打滑严重，80% 速度实测转速不到一半，所以语音转向固定满速。
        # 只有 --max-speed 限速时才会低于满速，那时按比例换算只是近似
        ms = min(int(int(angle) / 90 * MS_PER_90_AT_FULL * 100 / max(s, 1)), MAX_ACTION_MS)
        # 原地转向：两条履带反向，比单侧履带转快一倍、几乎不画弧
        left, right = (-s, s) if direction == "left" else (s, -s)
        name = "左转" if direction == "left" else "右转"
        return self._enqueue(Action(f"{name} {int(angle)}° {ms}ms", ms, lambda: self._drive_for(left, right, ms)))

    def barrel(self, direction: str, duration_ms: int) -> str:
        sp = BARREL_UP_SPEED if direction == "up" else -BARREL_UP_SPEED
        ms = min(int(duration_ms), MAX_BARREL_MS)
        name = "炮管抬高" if direction == "up" else "炮管压低"
        return self._enqueue(Action(f"{name} {ms}ms", ms, lambda: self._barrel_for(sp, ms)))

    def fire(self, shots: int = 1) -> str:
        if not self.fire_enabled:
            raise PermissionError("发射功能已被锁定（启动参数 --no-fire）")
        n = max(1, min(int(shots), self.max_shots))
        ms = int(n * (self.fire_pulse_ms + self.fire_cooldown_s * 1000))
        return self._enqueue(Action(f"发射 {n} 发", ms, lambda: self._fire(n)))

    def dance(self) -> str:
        async def run():
            s = self._speed(80)
            for _ in range(2):
                await self._drive_for(-s, s, 400)
                await self._drive_for(s, -s, 400)
            await self._barrel_for(BARREL_UP_SPEED, 300)
            await self._barrel_for(-BARREL_UP_SPEED, 300)

        return self._enqueue(Action("跳舞", 2400, run))

    def set_persona(self, persona: str) -> str:
        if persona not in PERSONAS:
            raise ValueError(f"没有这个人格：{persona}")
        self.persona = persona
        return f"已切换为{PERSONAS[persona]}模式"

    def emote(self, kind: str) -> str | None:
        """情绪小动作。车正忙（有动作在做或在排队）时跳过，不打扰正事；动作都成对抵消，车和炮管回到原位。"""
        if kind not in EMOTES or self._current or self._queue or (self._mission and not self._mission.done()):
            return None
        s = self._speed(100)
        moves = {
            "happy": [("drive", -s, s, 120), ("drive", s, -s, 240), ("drive", -s, s, 120)],  # 原地扭一扭
            "angry": [("barrel", BARREL_UP_SPEED, 300), ("drive", s, s, 150), ("drive", -s, -s, 150),
                      ("barrel", -BARREL_UP_SPEED, 300)],  # 炮管抬起、往前一冲
            "sad": [("barrel", -BARREL_UP_SPEED, 300), ("drive", -s, -s, 200), ("drive", s, s, 200),
                    ("barrel", BARREL_UP_SPEED, 300)],  # 炮管耷拉、缩一下
            "surprised": [("drive", -s, -s, 150), ("drive", s, s, 150)],  # 往后一缩
            "thinking": [("barrel", BARREL_UP_SPEED, 150), ("barrel", -BARREL_UP_SPEED, 150)] * 2,  # 点头琢磨
        }[kind]

        async def run():
            for m in moves:
                if m[0] == "drive":
                    await self._drive_for(m[1], m[2], m[3])
                else:
                    await self._barrel_for(m[1], m[2])

        return self._enqueue(Action(f"情绪：{EMOTES[kind]}", sum(m[-1] for m in moves), run))

    async def led(self, r: int, g: int, b: int):
        """车灯颜色。纯装饰：车上程序不支持或网络出错都忽略，不影响控车。"""
        try:
            await self.backend.led(r, g, b)
        except Exception:  # noqa: BLE001
            pass

    def status(self) -> dict:
        return {
            "current": self._current_desc,
            "mission": self._mission_desc if self._mission and not self._mission.done() else None,
            "queued": [a.desc for a in self._queue],
            "max_speed": self.max_speed,
            "fire_enabled": self.fire_enabled,
            "shots_fired": self.shots_fired,
            "persona": PERSONAS[self.persona],
        }

    # ---- 内部 ----
    async def _drive_for(self, left: int, right: int, ms: int):
        try:
            await self.backend.drive(left, right, ms + FAILSAFE_SLACK_MS)
            await asyncio.sleep(ms / 1000)
        finally:
            await self._safe_halt()

    async def _shuffle(self, s: int, ms: int):
        """老爷爷：每走 300ms 歇 150ms。"""
        left = ms
        while left > 0:
            step = min(300, left)
            await self._drive_for(s, s, step)
            left -= step
            if left > 0:
                await asyncio.sleep(0.15)

    async def _stagger(self, s: int, ms: int):
        """醉汉：左右履带一快一慢交替，走成 S 形。"""
        weak = int(s * 0.4)
        left, i = ms, 0
        try:
            while left > 0:
                step = min(250, left)
                l, r = (s, weak) if i % 2 == 0 else (weak, s)
                await self.backend.drive(l, r, step + FAILSAFE_SLACK_MS)
                await asyncio.sleep(step / 1000)
                left -= step
                i += 1
        finally:
            await self._safe_halt()

    async def _barrel_for(self, speed: int, ms: int):
        try:
            await self.backend.turret(speed, ms + FAILSAFE_SLACK_MS)
            await asyncio.sleep(ms / 1000)
        finally:
            await self._safe_halt()

    async def _fire(self, n: int):
        for _ in range(n):
            wait = self.fire_cooldown_s - (time.monotonic() - self._last_fire)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                await self.backend.fire(self.fire_pulse_ms)
                await asyncio.sleep(self.fire_pulse_ms / 1000)
            finally:
                await self._safe_halt()
            self._last_fire = time.monotonic()
            self.shots_fired += 1

    async def _safe_halt(self):
        try:
            await asyncio.shield(self.backend.halt())
        except Exception as e:  # noqa: BLE001
            print(f"[tank] halt 失败: {e}")
