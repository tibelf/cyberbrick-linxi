"""按地图开车：每 0.1 秒看一帧，用地上的锚点标记定位车（x, y, 车头朝向），沿 A* 路线一个点一个点开过去。

- 锚点不动，所有位置都以它为准，所以摄像头固定或手拿着都行（每一帧都要同时拍到锚点和车顶标记）。
- 偏离路线（下一段直线穿过障碍物膨胀区）就从当前位置重新规划；地图上没有能绕过去的路就停下说明。
- 看不到锚点或车：立刻停车等，LOST_S 秒还看不到就放弃。
- 往前开却朝车头的反方向移动：车顶标记贴反了，自动翻转（与 marker_seek 一致）。
- 车走过的地方记下来（visited），下次重建地图时当作“肯定是地面”的颜色样本。
"""

import asyncio
import math
import time

from .floormap import FloorMap
from .marker_seek import FAR_SPEED, LOST_S, MAX_DIFF, NEAR_CM, NEAR_SPEED, PIVOT_DEG, PUBLISH_S, STEER_GAIN
from .tank import TankExecutor

WP_REACH_CM = 8.0  # 离路线点这么近就换下一个
REPLAN_GAP_S = 0.5
GOTO_TIME_S = 90.0


def _wrap_deg(a):
    return (a + 180) % 360 - 180


class Navigator:
    def __init__(self, tank: TankExecutor, camera, tracker, anchor_id: int, on_photo=None,
                 loop_s: float = 0.1, lost_s: float = LOST_S):
        self.tank = tank
        self.camera = camera
        self.tracker = tracker
        self.anchor_id = anchor_id
        self.on_photo = on_photo
        self.loop_s = loop_s
        self.lost_s = lost_s
        self.map: FloorMap | None = None
        self.path: list | None = None
        self.goal = None
        self.car = None
        self.visited: list = []
        self._last_pub = 0.0
        self._last_frame = None

    async def look(self):
        frame = await asyncio.to_thread(self.camera.frame)
        self._last_frame = frame
        v = self.tracker.anchor_view(frame, self.anchor_id)
        if v and v.car:
            self.car = v.car
            if not self.visited or math.dist(self.visited[-1], v.car[:2]) > 15:
                self.visited.append(v.car[:2])
                self.visited = self.visited[-200:]
        self._publish(v)
        return v

    def _publish(self, v, force=False):
        if not (self.on_photo and self.map) or (not force and time.monotonic() - self._last_pub < PUBLISH_S):
            return
        self._last_pub = time.monotonic()
        from .camera import _encode

        car = v.car if v and v.car else None
        info = (f"车在 ({car[0]:.0f}, {car[1]:.0f}) cm" if car else "看不到车或锚点") + \
            (f"，路线还剩 {len(self.path)} 个点" if self.path else "")
        self.on_photo(_encode(self.map.render(car=car, path=self.path, goal=self.goal)), "地图", info)

    async def _turn(self, degrees: float):
        await self.tank.halt_now()
        if abs(degrees) >= 3:
            self.tank.turn("left" if degrees > 0 else "right", max(round(abs(degrees)), 5))
            await self.tank.wait_idle()
        await asyncio.sleep(0.25)

    async def face(self, xy) -> bool:
        """原地转到正对 xy（地图坐标）。"""
        for _ in range(6):
            v = await self.look()
            if not (v and v.car):
                await asyncio.sleep(self.loop_s)
                continue
            x, y, h = v.car
            b = _wrap_deg(math.degrees(math.atan2(xy[1] - y, xy[0] - x) - h))
            if abs(b) <= 6:
                return True
            await self._turn(b)
        return False

    async def goto(self, goal, stop_cm: float = WP_REACH_CM, time_limit_s: float = GOTO_TIME_S) -> tuple[bool, str]:
        """沿地图路线开到 goal（地图坐标）。goal 本身在障碍物里时，开到离它最近的能到的地方。"""
        if self.map is None:
            return False, "还没有地图：先说“拍一张地图”"
        self.goal = goal
        self.path = None
        t0 = time.monotonic()
        lost_since = None
        last_plan = 0.0
        i = 0
        fwd = None  # 往前开的起点 (x, y, 车头向量, 时间)，用来发现标记贴反
        try:
            while True:
                now = time.monotonic()
                if now - t0 > time_limit_s:
                    return False, f"开了 {round(now - t0)} 秒还没到，先停下"
                v = await self.look()
                if not (v and v.car):
                    fwd = None
                    await self.tank.halt_now()
                    lost_since = lost_since or now
                    if now - lost_since > self.lost_s:
                        what = f"地上的 {self.anchor_id} 号锚点" if v is None else "车顶的标记"
                        return False, f"看不到{what}，先停下：让车和锚点都在画面里"
                    await asyncio.sleep(self.loop_s)
                    continue
                lost_since = None
                x, y, h = v.car
                if math.dist((x, y), goal) <= stop_cm:
                    return True, "到了"
                if self.path is None or (now - last_plan > REPLAN_GAP_S and self.map.free(x, y)
                                         and not self.map.line_free((x, y), self.path[i])):
                    self.path = self.map.plan((x, y), goal)
                    last_plan, i = now, 0
                    self._publish(v, force=True)
                    if not self.path:
                        return False, "地图上找不到能绕过障碍物开过去的路"
                wp = self.path[i]
                if math.dist((x, y), wp) <= WP_REACH_CM:
                    if i == len(self.path) - 1:
                        return True, "到了（目标本身被挡着，停在离它最近的地方）"
                    i += 1
                    continue
                b = _wrap_deg(math.degrees(math.atan2(wp[1] - y, wp[0] - x) - h))
                if abs(b) > PIVOT_DEG:
                    fwd = None
                    await self._turn(b)
                    continue
                # 标记贴反：往前开了一段，位移却和车头方向相反
                if fwd is None:
                    fwd = (x, y, (math.cos(h), math.sin(h)), now)
                elif now - fwd[3] >= 0.3 and math.dist((x, y), fwd[:2]) > 5:
                    dx, dy = x - fwd[0], y - fwd[1]
                    if dx * fwd[2][0] + dy * fwd[2][1] < -0.5 * math.hypot(dx, dy):
                        self.tracker.flip = not self.tracker.flip
                        msg = "车顶标记好像贴反了（箭头要朝车头），已经自动按反的算；有空把标记转过来重新贴"
                        print(f"⚠️  {msg}")
                        if self.tank.on_error:
                            self.tank.on_error(msg)
                        await self.tank.halt_now()
                        fwd = None
                        continue
                    fwd = (x, y, (math.cos(h), math.sin(h)), now)
                d = math.dist((x, y), wp) + sum(math.dist(a, c) for a, c in zip(self.path[i:], self.path[i + 1:]))
                base = FAR_SPEED if d > NEAR_CM else NEAR_SPEED
                diff = max(-MAX_DIFF, min(MAX_DIFF, STEER_GAIN * b))
                await self.tank.steer(base - diff, base + diff)
                await asyncio.sleep(self.loop_s)
        finally:
            await self.tank.halt_now()


class MapBuilder:
    """self.map.build：拍一张照片建地图。视觉模型（有的话）只负责说出“有哪些障碍物、大概在哪个框里”，
    坐标全由本地按锚点算。"""

    def __init__(self, navigator: Navigator, ask=None):
        self.nav = navigator
        self.ask = ask  # camera.PhotoTool.ask（可以传 frame）

    async def __call__(self) -> str:
        from .floormap import OBSTACLE_QUESTION, build_map, parse_obstacle_boxes

        nav = self.nav
        frame = await asyncio.to_thread(nav.camera.frame)
        boxes = None
        if self.ask:
            try:
                answer = await self.ask(OBSTACLE_QUESTION, frame=frame)
                boxes = parse_obstacle_boxes(answer, frame.shape[1], frame.shape[0])
            except Exception as e:  # noqa: BLE001 — 没开视觉分析也能建图，只是更保守
                print(f"[map] 视觉模型没能列出障碍物，只按颜色判断：{e}")
        fm = await asyncio.to_thread(build_map, frame, nav.tracker, nav.anchor_id, list(nav.visited), boxes)
        nav.map, nav.path, nav.goal = fm, None, None
        v = nav.tracker.anchor_view(frame, nav.anchor_id)
        nav._publish(v, force=True)
        w, h = fm.size_cm
        names = [o.name for o in fm.obstacles if o.name]
        unnamed = len(fm.obstacles) - len(names)
        what = "、".join(names + ([f"{unnamed} 个没认出名字的东西"] if unnamed else [])) or "没有障碍物"
        car = "车也在地图上" if v and v.car else "但照片里没看到车顶标记"
        return (f"地图建好了：能看到大约 {w / 100:.1f}×{h / 100:.1f} 米的地面，障碍物：{what}；{car}。"
                f"之后去哪都会绕开它们，东西挪了就再说一次“拍地图”")
