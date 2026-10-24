"""动作队列：按顺序执行、时长控制、安全约束。"停"绕过队列立即生效。"""

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from .backend import TankBackend

MAX_ACTION_MS = 5000
MAX_QUEUE_MS = 15000
FAILSAFE_SLACK_MS = 300
MAX_TURRET_MS = 3000
TURRET_SPEED = 45  # 与原厂遥控（右肩摇杆 ±45）一致


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
        self._worker: asyncio.Task | None = None
        self._last_fire = 0.0

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

    async def stop(self) -> str:
        self._queue.clear()
        if self._current and not self._current.done():
            self._current.cancel()
        await self._safe_halt()
        return "已停车"

    def _speed(self, speed: int) -> int:
        return max(0, min(int(speed), self.max_speed))

    def move(self, direction: str, duration_ms: int, speed: int) -> str:
        sign = 1 if direction == "forward" else -1
        s = self._speed(speed) * sign
        ms = min(int(duration_ms), MAX_ACTION_MS)
        name = "前进" if sign > 0 else "后退"
        return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}", ms, lambda: self._drive_for(s, s, ms)))

    def turn(self, direction: str, duration_ms: int, speed: int) -> str:
        s = self._speed(speed)
        ms = min(int(duration_ms), MAX_ACTION_MS)
        # 单侧履带转向
        left, right = (0, s) if direction == "left" else (s, 0)
        name = "左转" if direction == "left" else "右转"
        return self._enqueue(Action(f"{name} {ms}ms", ms, lambda: self._drive_for(left, right, ms)))

    def turret(self, direction: str, duration_ms: int) -> str:
        sp = TURRET_SPEED if direction == "left" else -TURRET_SPEED
        ms = min(int(duration_ms), MAX_TURRET_MS)
        name = "炮塔左转" if direction == "left" else "炮塔右转"
        return self._enqueue(Action(f"{name} {ms}ms", ms, lambda: self._turret_for(sp, ms)))

    def fire(self, shots: int = 1) -> str:
        if not self.fire_enabled:
            raise PermissionError("发射功能已被锁定（启动参数 --no-fire）")
        n = max(1, min(int(shots), self.max_shots))
        ms = int(n * (self.fire_pulse_ms + self.fire_cooldown_s * 1000))
        return self._enqueue(Action(f"发射 {n} 发", ms, lambda: self._fire(n)))

    def dance(self) -> str:
        async def run():
            for _ in range(2):
                await self._drive_for(0, self._speed(80), 500)
                await self._drive_for(self._speed(80), 0, 500)
            await self._turret_for(TURRET_SPEED, 400)
            await self._turret_for(-TURRET_SPEED, 400)

        return self._enqueue(Action("跳舞", 3200, run))

    def status(self) -> dict:
        return {
            "current": self._current_desc,
            "queued": [a.desc for a in self._queue],
            "max_speed": self.max_speed,
            "fire_enabled": self.fire_enabled,
            "shots_fired": self.shots_fired,
        }

    # ---- 内部 ----
    async def _drive_for(self, left: int, right: int, ms: int):
        try:
            await self.backend.drive(left, right, ms + FAILSAFE_SLACK_MS)
            await asyncio.sleep(ms / 1000)
        finally:
            await self._safe_halt()

    async def _turret_for(self, speed: int, ms: int):
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
