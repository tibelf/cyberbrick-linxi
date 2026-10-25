"""标记闭环寻的（--marker 时代替 seek.py 的视觉模型版）：每 0.1 秒看一帧，边开边修正方向。

每一帧由 marker.MarkerTracker 算出“目标在车头左/右多少度、多远”（以车为参照，摄像头可以手拿着动）：
- 偏得多（> PIVOT_DEG）：先停，用校准过的原地转向转过去（低速原地转会打滑，所以不用连续小转）
- 偏得少：两条履带差速，一边往前开一边把车头掰正；离得近了减速
- 走到（< ARRIVE_CM）就停；hit 时靠近后全速撞上去，再看目标倒了没有
- 看不到车或目标：立刻停车等，LOST_S 秒还看不到就放弃并说明原因
- 往前开却越开越远：车顶标记贴反了，自动把车头方向反过来算并提醒（2026-10-25 用户实测“去撞目标却往反方向跑”）
车顶没贴标记、目标既没有标记也说不出颜色时，退回视觉模型版（fallback）。
所有动作走 TankExecutor：steer 每条只管 0.4 秒（画面卡住车就自己停），速度裁剪到限速，stop() 随时取消。
"""

import asyncio
import time
from typing import Awaitable, Callable

from .marker import MarkerTracker, Sighting, Target, resolve_target
from .seek import GOALS, parse_yes_no
from .tank import TankExecutor

LOOP_S = 0.1
ARRIVE_CM = 14.0  # 车中心到目标小于这个算走到（车身半长约 9 cm）
RAM_ZONE_CM = 28.0  # hit：进到这个距离、而且对准了，就冲上去
RAM_ALIGN_DEG = 12
RAM_MS = 900
MAX_RAMS = 3
PIVOT_DEG = 25.0
AIM_TOLERANCE_DEG = 5.0
FAR_SPEED, NEAR_SPEED, NEAR_CM = 80, 65, 35.0
STEER_GAIN = 1.0  # 每偏 1 度，两条履带速度差多少
MAX_DIFF = 25
LOST_S = 8.0
NO_MARKER_FALLBACK_S = 2.0  # 一开始这么久都看不到车顶标记，就改用视觉模型版
TIME_LIMIT_S = 120.0
PUBLISH_S = 1.0  # 网页控制台每秒刷新一张调试图
KNOCKED_TILT_DEG = 40.0
# 往前开着（偏角不大）却越开越远：车顶标记贴反了（箭头朝车尾），程序以为的车头其实是车尾
REVERSE_CHECK_S = 0.3  # 至少隔这么久的两次观测
REVERSE_CM = 6.0  # 距离变大超过 max(这么多, 10%) 才算（手持时距离会有几厘米的抖动）

Say = Callable[[str], Awaitable[None]]


class MarkerSeeker:
    def __init__(self, tank: TankExecutor, camera, tracker: MarkerTracker | None = None,
                 labels: dict[int, str] | None = None, ask=None, say: Say | None = None, fallback=None,
                 on_photo=None, time_limit_s: float = TIME_LIMIT_S, lost_s: float = LOST_S, loop_s: float = LOOP_S):
        self.tank = tank
        self.camera = camera
        self.tracker = tracker or MarkerTracker()
        self.labels = labels or {}
        self.ask = ask  # 视觉模型（可选）：只用来确认“撞倒了没有”
        self.say = say
        self.fallback = fallback  # seek.Seeker：没贴标记时退回去用
        self.on_photo = on_photo  # (jpeg, 标题, 说明) → 网页控制台
        self.time_limit_s = time_limit_s
        self.lost_s = lost_s
        self.loop_s = loop_s
        self._last_pub = 0.0

    def __call__(self, target: str, goal: str = "approach", fire: bool = False) -> str:
        desc = f"{GOALS.get(goal, '走到')}{target}" + ("并开火" if fire else "")
        return self.tank.start_mission(desc, lambda: self.run(target, goal, fire)) + \
            "（看着标记边开边调，没完成不会停；完成后会播报结果；说“停”随时中止）"

    async def run(self, target: str, goal: str, fire: bool) -> str:
        spec = resolve_target(target, self.labels)
        r = await self._loop(target, spec, goal)
        if r is None:  # 没贴标记：退回视觉模型版，同一个任务里接着跑
            print("🔁 看不到车顶标记（或目标没有标记、也说不出颜色），改用视觉模型估位置")
            return await self.fallback.run(target, goal, fire)
        ok, result = r
        if ok and fire:
            try:
                self.tank.fire(1)
                await self.tank.wait_idle()
                result += "，已开火"
            except Exception as e:  # noqa: BLE001 — 例如 --no-fire 锁定，如实播报
                result += f"，但没有开火：{e}"
        print(f"🎯 {result}")
        if self.say:
            await self.say(result)
        return result

    # ---- 看 ----
    async def _look(self, spec: Target) -> Sighting:
        frame = await asyncio.to_thread(self.camera.frame)
        s = self.tracker.locate(frame, spec)
        if self.on_photo and time.monotonic() - self._last_pub >= PUBLISH_S:
            self._last_pub = time.monotonic()
            from .camera import _encode

            info = (f"目标在车头{'左' if s.bearing > 0 else '右'} {abs(s.bearing):.0f}°，{s.distance:.0f} 厘米"
                    if s.car and s.target else ("看不到车顶标记" if not s.car else "看不到目标"))
            self.on_photo(_encode(self.tracker.annotate(frame, s)), "标记跟踪", info)
        return s

    # ---- 动 ----
    async def _turn(self, degrees: float):
        await self.tank.halt_now()
        if abs(degrees) >= 3:
            self.tank.turn("left" if degrees > 0 else "right", max(round(abs(degrees)), 5))
            await self.tank.wait_idle()
        await asyncio.sleep(0.25)  # 等画面跟上（摄像头有延迟）

    async def _ram(self):
        await self.tank.halt_now()
        self.tank.move("forward", RAM_MS, 100)
        self.tank.move("backward", 400, 70)  # 退一点，看得清
        await self.tank.wait_idle()
        await asyncio.sleep(0.4)

    async def _knocked(self, spec: Target, target: str, before: Sighting) -> bool:
        if self.ask:
            try:
                a = await self.ask(f"画面里的{target}是不是已经倒下、被撞翻或被撞开了？只回答“是”或“否”，不要解释。")
                yes = parse_yes_no(a)
                if yes is not None:
                    return yes
            except Exception as e:  # noqa: BLE001 — 没开视觉分析等：用标记自己判断
                print(f"[marker-seek] 视觉确认失败，改用标记判断：{e}")
        for _ in range(5):
            s = await self._look(spec)
            if s.car:
                if not s.target:
                    return True  # 目标不见了（倒下后标记朝下，或被撞出画面）
                if before.target_tilt is not None and s.target_tilt is not None:
                    return abs(s.target_tilt - before.target_tilt) > KNOCKED_TILT_DEG
                return s.distance > before.distance + 10  # 颜色目标：被撞远了
            await asyncio.sleep(self.loop_s)
        return False

    # ---- 主循环 ----
    async def _loop(self, target: str, spec: Target, goal: str):
        """返回 (是否完成, 播报)；返回 None 表示该退回视觉模型版。"""
        verb = GOALS.get(goal, "走到")
        t0 = time.monotonic()
        lost_since = None
        seen_car = False
        rams = 0
        fwd_since = fwd_d0 = None  # 这一段连续往前开从什么时候、多远开始
        try:
            while True:
                now = time.monotonic()
                if now - t0 > self.time_limit_s:
                    return False, f"找了 {round(now - t0)} 秒还没{verb}{target}，先停下"
                s = await self._look(spec)
                seen_car = seen_car or s.car
                if not (s.car and s.target):
                    fwd_since = None
                    await self.tank.halt_now()  # 看不到就别闭着眼开
                    if self.fallback and not seen_car and now - t0 > NO_MARKER_FALLBACK_S:
                        return None
                    if self.fallback and s.car and spec.kind == "any_marker" and now - t0 > NO_MARKER_FALLBACK_S \
                            and not any(i for i in (s.corners or {}) if i != 0):
                        return None  # 目标没贴标记、也没说颜色
                    lost_since = lost_since or now
                    if now - lost_since > self.lost_s:
                        if not s.car:
                            return False, "看不到车顶的标记，先停下：让摄像头拍到车顶，别挡住标记"
                        return False, f"看不到{target}，先停下：让车和{target}都在画面里"
                    await asyncio.sleep(self.loop_s)
                    continue
                lost_since = None
                b, d = s.bearing, s.distance
                if goal == "aim":
                    if abs(b) <= AIM_TOLERANCE_DEG:
                        again = await self._look(spec)
                        if again.car and again.target and abs(again.bearing) <= AIM_TOLERANCE_DEG + 2:
                            return True, f"已对准{target}"
                        continue
                    await self._turn(b)
                    continue
                if goal == "approach" and d <= ARRIVE_CM:
                    # 单帧可能看错（标记位姿偶尔会翻转），停下再看一眼，两次都说到了才算
                    await self.tank.halt_now()
                    await asyncio.sleep(0.2)
                    again = await self._look(spec)
                    if again.car and again.target and again.distance <= ARRIVE_CM + 3:
                        return True, f"已走到{target}跟前"
                    continue
                if goal == "hit" and d <= RAM_ZONE_CM and abs(b) <= RAM_ALIGN_DEG:
                    rams += 1
                    await self._ram()
                    if await self._knocked(spec, target, s):
                        return True, f"已把{target}撞倒了"
                    if rams >= MAX_RAMS:
                        return False, f"撞了 {rams} 次，{target}还没倒"
                    continue
                if abs(b) > PIVOT_DEG:
                    fwd_since = None
                    await self._turn(b)
                    continue
                if fwd_since is None or d < fwd_d0 - 2:
                    fwd_since, fwd_d0 = now, d  # 刚开始往前开，或者确实在靠近
                elif now - fwd_since >= REVERSE_CHECK_S and d > fwd_d0 + max(REVERSE_CM, 0.1 * fwd_d0):
                    self.tracker.flip = not self.tracker.flip
                    msg = "车顶标记好像贴反了（箭头要朝车头），已经自动按反的算；有空把标记转过来重新贴"
                    print(f"⚠️  {msg}")
                    if self.tank.on_error:
                        self.tank.on_error(msg)
                    fwd_since = None
                    await self.tank.halt_now()
                    continue
                base = FAR_SPEED if d > NEAR_CM else NEAR_SPEED
                diff = max(-MAX_DIFF, min(MAX_DIFF, STEER_GAIN * b))  # 目标在左（b>0）→ 右履带快
                await self.tank.steer(base - diff, base + diff)
                await asyncio.sleep(self.loop_s)
        finally:
            await self.tank.halt_now()
