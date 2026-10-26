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
STEER_MS = 400  # steer 一条指令管多久（含看门狗余量）：任务每 0.1s 刷新，卡住 0.4s 车就自己停
STEER_MAX_MS = 600
MANUAL_MS = 400  # 手机手柄一条指令管多久：手柄每 0.1s 刷新，手机断线/锁屏 0.4s 内车自己停
MANUAL_HOLD_S = 0.6  # 最近一次手柄指令后这么久内算“正在手动”，情绪小动作不插进来
HEARTBEAT_S = 5  # 空闲时每隔这么久问一下车还在不在（网页控制台显示在线状态、及时发现车重启）
CHECK_TIMEOUT_S = 3
GRANDPA_SPEED = 65  # 老爷爷模式的速度上限；再低落地就带不动履带（40% 实测带不动）

# 人格只改变前进/后退的风格；转向按满速校准过角度，不受人格影响
PERSONAS = {"normal": "正常", "soldier": "特种兵", "grandpa": "老爷爷", "drunk": "醉汉"}
EMOTES = {"happy": "开心", "angry": "生气", "sad": "难过", "surprised": "惊讶", "thinking": "思考"}


@dataclass
class Action:
    desc: str
    duration_ms: int
    run: Callable[[], Awaitable[None]]
    kind: str = "other"  # forward = 纯前进；闲逛等任务据此判断两张照片之间车是不是一直在往前走
    turn_deg: float = 0  # 转向动作转过的角度，正数左转


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
        self._manual_t = -1e9  # 最近一次手柄指令的时间
        self._mission: asyncio.Task | None = None  # 闭环任务（如看图寻的），同一时间只有一个
        self._mission_desc: str | None = None
        self._worker: asyncio.Task | None = None
        self._last_fire = 0.0
        self.persona = "normal"
        # 给闭环任务看的运动记录：两张照片之间这两个计数都没变，说明车一直在往前走
        self.motion_epoch = 0  # 每开始一个“不是纯前进”的动作就加 1
        self.idle_epoch = 0  # 每次队列做空、车停下就加 1
        self.turn_total = 0.0  # 累计转过的角度（正数左转），用来推算车头方向
        # 车没反应时的排查依据：工具调用早就回了“已安排”，动作在后台失败时大模型和用户都看不到
        self.last_error: dict | None = None  # {"time", "message"}
        self.link: dict | None = None  # 最近一次和车通信的结果（心跳或 check() 更新）
        self.on_error: Callable[[str], None] | None = None  # 出问题时通知（网页控制台、语音播报）
        self._heartbeat: asyncio.Task | None = None
        # 动作宏“边做边学”：录制时，用户下令的 move/turn/barrel/fire/dance 按宏的写法记下来（任务自己下发的不记）
        self.recorder: list[str] | None = None

    # ---- 生命周期 ----
    async def start(self):
        await self.backend.connect()
        self._worker = asyncio.create_task(self._run())
        if hasattr(self.backend, "ping"):
            self._heartbeat = asyncio.create_task(self._heartbeat_loop())

    async def close(self):
        await self.stop()
        for task in (self._worker, self._heartbeat):
            if task:
                task.cancel()
        await self.backend.close()

    async def _run(self):
        while True:
            await self._wakeup.wait()
            while self._queue:
                action = self._queue.pop(0)
                if action.kind != "forward":
                    self.motion_epoch += 1
                    self.turn_total += action.turn_deg
                self._current_desc = action.desc
                self._current = asyncio.create_task(action.run())
                try:
                    await self._current
                except asyncio.CancelledError:
                    if not self._current.cancelled():
                        raise  # worker 自己被取消
                except Exception as e:  # noqa: BLE001
                    self._report(f"「{action.desc}」没执行成功：{e}")
                    await self._safe_halt()
                finally:
                    self._current = None
                    self._current_desc = None
            self.idle_epoch += 1
            self._wakeup.clear()

    def _report(self, message: str):
        self.last_error = {"time": time.strftime("%H:%M:%S"), "message": message}
        print(f"⚠️  [tank] {message}")
        if self.on_error:
            try:
                self.on_error(message)
            except Exception as e:  # noqa: BLE001 — 通知失败不能影响控车
                print(f"[tank] 通知失败：{e!r}")

    async def check(self) -> dict:
        """真的给车发一条消息，看它在不在、延迟多少、是不是刚重启过。"""
        ping = getattr(self.backend, "ping", None)
        if ping is None:
            return {"online": None, "detail": "这个后端不支持检查连接"}
        try:
            info = await asyncio.wait_for(ping(), CHECK_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            info = {"online": False, "error": str(e) or type(e).__name__}
        was_online = self.link is None or self.link.get("online") is not False
        self.link = {**info, "checked_at": time.strftime("%H:%M:%S")}
        if info.get("online") is False and was_online:
            self._report(f"车没有应答（{info['error']}）：确认车开着、电量够、和电脑在同一个网络")
        if info.get("rebooted"):
            self._report("车刚刚自己重启过，可能是电量低或电机启动时电压被拉低，之前的动作可能没做完")
        return self.link

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            if not self.busy:  # 动作执行时每条指令本身就是检查，不用额外打扰
                await self.check()

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
        所以安全约束照常生效；stop() 会连任务一起取消。
        已有任务在跑时，新任务直接接替它（用户最新的要求优先，例如闲逛中说“去瓶子那里”）。"""
        replaced = self._mission_desc if self._mission and not self._mission.done() else None
        if replaced:
            self._mission.cancel()
            self._queue.clear()
            if self._current and not self._current.done():
                self._current.cancel()  # 被取消的动作会在 finally 里 halt
        self._mission_desc = desc
        self._mission = asyncio.create_task(self._guard_mission(desc, run))
        return f"开始：{desc}" + (f"（已停止之前的「{replaced}」）" if replaced else "")

    async def _guard_mission(self, desc: str, run: Callable[[], Awaitable[None]]):
        """任务出错（拍照失败、视觉接口超时等）不能悄悄结束：停车，并像动作失败一样报告出来。"""
        try:
            await run()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._queue.clear()
            if self._current and not self._current.done():
                self._current.cancel()
            await self._safe_halt()
            self._report(f"「{desc}」中断了：{e}")

    @property
    def queued(self) -> int:
        """排队中（还没开始）的动作数。"""
        return len(self._queue)

    @property
    def manual_active(self) -> bool:
        return time.monotonic() - self._manual_t < MANUAL_HOLD_S

    async def manual(self, left: int, right: int, barrel: int = 0):
        """手机手柄：直接设两条履带速度（正数前进）和炮管（1 抬、-1 压、0 不动），不排队。
        手动优先：有任务在跑或语音排了动作，先全部停掉。每条指令只管 MANUAL_MS，手柄不刷新车就自己停。"""
        if (self._mission and not self._mission.done()) or self._queue or self._current:
            await self.stop()
            # 等被取消的动作真正收尾（它的 finally 会发 halt），否则那次 halt 可能落在下面的行驶指令之后
            for _ in range(100):
                if not self._current:
                    break
                await asyncio.sleep(0.01)
        self._manual_t = time.monotonic()
        clip = lambda v: max(-self.max_speed, min(int(v), self.max_speed))  # noqa: E731
        left, right, barrel = clip(left), clip(right), max(-1, min(int(barrel), 1))
        if not (left or right or barrel):
            await self._safe_halt()
            return
        if left or right:
            self.motion_epoch += 1
            await self.backend.drive(left, right, MANUAL_MS)
        if barrel:
            await self.backend.turret(barrel * BARREL_UP_SPEED, MANUAL_MS)

    @property
    def busy(self) -> bool:
        return bool(self._current or self._queue)

    async def wait_idle(self):
        """等队列里的动作全部做完（闭环任务每一步动完再拍下一张）。"""
        while self._current or self._queue:
            await asyncio.sleep(0.05)

    async def halt_now(self):
        """停下当前动作、清空队列，但不取消任务（任务自己决定之后怎么继续，例如断线暂停）。"""
        self._queue.clear()
        if self._current and not self._current.done():
            self._current.cancel()
        await self._safe_halt()

    async def stop(self) -> str:
        if self._mission and not self._mission.done():
            self._mission.cancel()
        self._queue.clear()
        if self._current and not self._current.done():
            self._current.cancel()
        await self._safe_halt()
        return "已停车"

    def _record(self, step: str):
        if self.recorder is None:
            return
        try:
            in_mission = self._mission is not None and asyncio.current_task() is self._mission
        except RuntimeError:  # 不在事件循环里
            in_mission = False
        if not in_mission:
            self.recorder.append(step)

    def _speed(self, speed: int) -> int:
        return max(0, min(int(speed), self.max_speed))

    def move(self, direction: str, duration_ms: int, speed: int) -> str:
        sign = 1 if direction == "forward" else -1
        kind = "forward" if sign > 0 else "other"
        ms = min(int(duration_ms), MAX_ACTION_MS)
        name = "前进" if sign > 0 else "后退"
        self._record(f"{name} {ms}" + (f" 速度 {int(speed)}" if int(speed) != 80 else ""))
        if self.persona == "soldier":
            s = self._speed(100) * sign
            return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}（特种兵）", ms, lambda: self._drive_for(s, s, ms), kind))
        if self.persona == "grandpa":
            s = min(self._speed(speed), GRANDPA_SPEED) * sign
            total = ms + (ms // 300) * 150
            return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}（老爷爷）", total, lambda: self._shuffle(s, ms), kind))
        s = self._speed(speed) * sign
        if self.persona == "drunk":
            return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}（醉汉）", ms, lambda: self._stagger(s, ms), kind))
        return self._enqueue(Action(f"{name} {ms}ms 速度{abs(s)}", ms, lambda: self._drive_for(s, s, ms), kind))

    async def steer(self, left: int, right: int, ms: int = STEER_MS):
        """闭环任务专用：直接设两条履带速度（正数前进），只管 ms 毫秒——设备端看门狗到点自停，
        所以任务每 0.1 秒左右刷新一次就能连续开，画面一卡车就停。只能在当前任务里调用；速度照样裁剪到限速。"""
        if not self._mission or asyncio.current_task() is not self._mission:
            raise RuntimeError("steer 只能由正在运行的闭环任务调用")
        if self._queue or self._current:
            raise RuntimeError("队列里还有动作，先等它做完")
        clip = lambda v: max(-self.max_speed, min(int(v), self.max_speed))  # noqa: E731
        left, right = clip(left), clip(right)
        if left != right or left <= 0:
            self.motion_epoch += 1  # 不是纯前进
        await self.backend.drive(left, right, min(int(ms), STEER_MAX_MS))

    def turn(self, direction: str, angle: int, speed: int = 100) -> str:
        s = self._speed(speed)
        # 只在满速下校准过：低速时履带打滑严重，80% 速度实测转速不到一半，所以语音转向固定满速。
        # 只有 --max-speed 限速时才会低于满速，那时按比例换算只是近似
        ms = min(int(int(angle) / 90 * MS_PER_90_AT_FULL * 100 / max(s, 1)), MAX_ACTION_MS)
        # 原地转向：两条履带反向，比单侧履带转快一倍、几乎不画弧
        left, right = (-s, s) if direction == "left" else (s, -s)
        name = "左转" if direction == "left" else "右转"
        self._record(f"{name} {int(angle)}")
        deg = float(angle) if direction == "left" else -float(angle)
        return self._enqueue(Action(f"{name} {int(angle)}° {ms}ms", ms, lambda: self._drive_for(left, right, ms),
                                    "turn", deg))

    def barrel(self, direction: str, duration_ms: int) -> str:
        sp = BARREL_UP_SPEED if direction == "up" else -BARREL_UP_SPEED
        ms = min(int(duration_ms), MAX_BARREL_MS)
        name = "炮管抬高" if direction == "up" else "炮管压低"
        self._record(f"{'炮管抬' if direction == 'up' else '炮管压'} {ms}")
        return self._enqueue(Action(f"{name} {ms}ms", ms, lambda: self._barrel_for(sp, ms)))

    def fire(self, shots: int = 1) -> str:
        if not self.fire_enabled:
            raise PermissionError("发射功能已被锁定（启动参数 --no-fire）")
        n = max(1, min(int(shots), self.max_shots))
        ms = int(n * (self.fire_pulse_ms + self.fire_cooldown_s * 1000))
        self._record(f"开火 {n}")
        return self._enqueue(Action(f"发射 {n} 发", ms, lambda: self._fire(n)))

    def dance(self) -> str:
        async def run():
            s = self._speed(80)
            for _ in range(2):
                await self._drive_for(-s, s, 400)
                await self._drive_for(s, -s, 400)
            await self._barrel_for(BARREL_UP_SPEED, 300)
            await self._barrel_for(-BARREL_UP_SPEED, 300)

        self._record("跳舞")
        return self._enqueue(Action("跳舞", 2400, run))

    def set_persona(self, persona: str) -> str:
        if persona not in PERSONAS:
            raise ValueError(f"没有这个人格：{persona}")
        self.persona = persona
        return f"已切换为{PERSONAS[persona]}模式"

    def emote(self, kind: str, from_mission: bool = False) -> str | None:
        """情绪小动作。车正忙（有动作在做或在排队、或有任务在跑）时跳过，不打扰正事；动作都成对抵消，车和炮管回到原位。
        from_mission=True 表示是任务自己要做的小动作（例如闲逛时扭一扭），不受“有任务在跑”的限制。"""
        busy_mission = self._mission and not self._mission.done() and not from_mission
        if kind not in EMOTES or self._current or self._queue or busy_mission or self.manual_active:
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
            "link": self.link,
            "last_error": self.last_error,
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
