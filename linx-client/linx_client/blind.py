"""一张图规划、闭眼走完：只拍一次俯拍照片交给灵矽视觉模型，之后按路线开环执行，不再拍照。

1. self.blind.map：拍一张从正上方往下拍的照片，按固定格式问视觉模型：坦克外框、炮管尖端、挡路东西的外框
   （百分比坐标）。不贴标记、不用 OpenCV 识别，坐标全来自视觉模型。
2. 俯拍时画面 ≈ 地面按比例缩小：用坦克外框和 Mini T 的实际尺寸（约 14×18cm）换算“1 像素 = 多少厘米”。
3. self.blind.goto：障碍物按“车身半径 + 余量”膨胀，A* 规划（复用 FloorMap.plan，已拉直成少拐弯的折线），
   翻译成“满速原地转 θ° + 速度 BLIND_SPEED 前进 t 毫秒”的序列，按校准参数开环执行。走完用计划终点更新位置估计，
   下一次 goto 接着用，所以误差会累积——说“重新拍地图”就重新定位。

精度：没有编码器/陀螺仪，履带打滑，1–2 米、拐一两个弯的路线终点大约偏十几厘米。所以障碍物额外多留 EXTRA_MARGIN_CM，
转向一律满速原地转（唯一校准过的转法），直行时长按 cm_per_s（`--blind-cm-per-s`，要实测）换算。
车头朝向按可靠程度依次取：①用户说的（“车头对着垃圾桶”“车头朝左”，工具参数 facing）；②视觉模型从 8 个画面方向里
选一个（“车头=左”）；③“车身中心 → 炮管尖”的坐标；④都没有就按朝画面上方。最后在回复里说出来，看错了用户一听就知道。
实测：车在画面里只有五十多像素宽，炮管尖离中心二十来像素，视觉模型坐标差 3% 方向就偏 30° 以上（真对着左边，算成左上方），
所以坐标只当兜底。facing="up" 强制约定车头朝上（用户经常没摆正，不再默认）。
要去的目标本身不算障碍物：额外余量是防撞“别的”东西的，对目标要一直开到跟前（车头离它边缘 STOP_GAP_CM）。
"""

import asyncio
import math
import re
from typing import Awaitable, Callable

from .floormap import CAR_RADIUS_CM, GRID_CM, MARGIN_CM, RES_CM, FloorMap, Obstacle
from .tank import TankExecutor
from .tank.executor import MAX_ACTION_MS

CAR_LEN_CM = 18.0  # Mini T 车身（含履带）长 × 宽，换算比例尺用
CAR_WID_CM = 14.0
BLIND_SPEED = 80
DEFAULT_CM_PER_S = 26.0  # 速度 80 直行每秒多少厘米（2026-10-26 用户在公司地毯上实测约 26；地面、电量不同会变）
EXTRA_MARGIN_CM = 15.0  # 闭眼走误差大，障碍物在“车身半径 + MARGIN_CM”之外再多留这么多
MARGIN_STEPS = (EXTRA_MARGIN_CM, 8.0, 0.0)  # 留不出路时逐步收窄，并如实告诉用户
MIN_TURN_DEG = 4
STOP_GAP_CM = 8.0  # 到了以后车头离目标边缘多远
# 去目标的哪一侧（从车看过去）。旁边 = 靠车这一侧；绕一圈 = 左边 → 后面 → 右边 → 回到出发点
WHERE = ("旁边", "后面", "左边", "右边", "绕一圈", "撞倒")
RAM_CM = 10.0  # 撞倒：车头碰到目标后再往前冲这么多
# 口述摆放（不拍照）：用户说“纸杯在车头正前方 40 厘米”，或者还没拍过俯拍图时默认 50 厘米；四周当作空地
ASSUMED_AHEAD_CM = 50
ASSUMED_SIZE_CM = 8  # 纸杯、瓶子大小
ASSUMED_FIELD_CM = 300
BACK_CM = 15.0  # 撞完退回来这么多
AROUND_GAP_CM = 4.0  # 绕到后面/侧面时，终点比膨胀区再往外这么多，保证落在能走的地方
GOAL_TOLERANCE_CM = 15.0  # 规划出来的终点离想去的点超过这么多，说明那边没地方（出了照片或被挡住）
VISION_TIMEOUT_S = 45  # 要视觉模型按格式列坦克、车头、炮管尖和每个障碍物，实测约 17s（简单问题约 2.6s）
CAR_NAMES = ("坦克", "小车", "小坦克", "车")


def question(target: str | None = None) -> str:
    want = f"其中“{target}”一定要列出来，名字就写“{target}”。" if target else ""
    return ("这是从正上方往下俯拍的地面照片，里面有一辆履带小坦克。用百分比坐标（左上角 0,0，右下角 100,100）回答，"
            "只输出一行，不要解释：坦克=左,上,右,下（外框紧贴车身，不要留空白）；车头=上/下/左/右/左上/右上/左下/右下 之一（炮管指向画面哪边）；"
            "炮管尖=x,y；然后列出地面上会挡住小坦克的东西"
            "（箱子、椅子腿、桌腿、瓶子、墙、家具等；地垫、地毯、地板花纹、纸片、影子不算），"
            f"每个写成 名字=左,上,右,下。{want}"
            "例如：坦克=40,60,52,75；车头=上；炮管尖=46,55；纸箱=10,20,30,40；红色瓶子=70,30,75,40")


# 画面方向 → 角度（x 向右、y 向上，逆时针为正）
DIRECTIONS = {"右上": 45, "左上": 135, "左下": -135, "右下": -45, "右": 0, "上": 90, "左": 180, "下": -90}
_DIR = re.compile(r"车头(?:朝向)?\s*[=:：]\s*(左上|右上|左下|右下|上|下|左|右)")


_FILLER = ("车头", "正对着", "对准", "对着", "朝着", "朝向", "面对", "面向", "朝", "向")


def names_match(name: str, said: str) -> bool:
    """视觉模型起的名字和用户的说法是不是同一个东西：“黑色垃圾桶” ~ “对着垃圾桶”“垃圾桶”。"""
    if not name or not said:
        return False
    for w in _FILLER:
        said = said.replace(w, "")
    said = said.strip(" ，,。的")
    return bool(said) and (said in name or name in said)


def direction_deg(text: str) -> float | None:
    """“左”“朝右上”“车头朝画面左边” → 角度；说不出方向返回 None。先认两个字的（左上），再认一个字的。"""
    for word, deg in DIRECTIONS.items():
        if word in text:
            return deg
    return None


_NUM = r"(\d+(?:\.\d+)?)"
_SEP = r"[,，\s]+"
_BOX = re.compile(r"([^\s=；;，,：:。]+)\s*[=:：]\s*" + _NUM + _SEP + _NUM + _SEP + _NUM + _SEP + _NUM + r"(?![\d.])")
_TIP = re.compile(r"炮管尖?\s*[=:：]\s*" + _NUM + _SEP + _NUM + r"(?![\d.,，\s]*\d)")


def parse_answer(answer: str):
    """→ (坦克外框, 炮管尖, [(名字, 外框)])，坐标都是 0-100 的百分比；0-1000 的回答自动缩放。"""
    car, tip, others = None, None, []
    for m in _BOX.finditer(answer):
        name, v = m.group(1), [float(g) for g in m.groups()[1:]]
        if max(v) > 100:
            v = [x / 10 for x in v]
        x1, x2 = sorted((v[0], v[2]))
        y1, y2 = sorted((v[1], v[3]))
        if name in CAR_NAMES and car is None:
            car = (x1, y1, x2, y2)
        elif not name.startswith("炮管"):
            others.append((name, (x1, y1, x2, y2)))
    m = _TIP.search(answer)
    if m:
        tip = tuple(float(g) / (10 if float(g) > 100 else 1) for g in m.groups())
    return car, tip, others


class BlindMap:
    """视觉模型的回答 → 以厘米为单位的地图（x 向右、y 向上，原点在照片左下角）+ 车的位置估计。"""

    def __init__(self, w_px: int, h_px: int, answer: str, facing: str = "auto", image=None, user_facing: str = "",
                 view_cm: float | None = None):
        car, tip, others = parse_answer(answer)
        if car is None:
            raise ValueError(f"视觉模型没找到坦克（回答：{answer[:80]}）")
        self.w_px, self.h_px = w_px, h_px
        px = lambda p, total: p / 100 * total  # noqa: E731
        cx = px((car[0] + car[2]) / 2, w_px)
        cy = px((car[1] + car[3]) / 2, h_px)
        # 车头朝向（图像坐标：u 向右、v 向下；heading 用 y 向上的角度）
        self.heading_source = "default"
        heading_img = math.pi / 2  # 都没有时按朝画面上方
        vision_dir = _DIR.search(answer)
        toward = next(((n, b) for n, b in others if names_match(n, user_facing)), None) if user_facing else None
        if toward:  # 用户说“车头对着垃圾桶”
            n, (x1, y1, x2, y2) = toward
            heading_img = math.atan2(-(px((y1 + y2) / 2, h_px) - cy), px((x1 + x2) / 2, w_px) - cx)
            self.heading_source = "user"
        elif user_facing and direction_deg(user_facing) is not None:  # 用户说“车头朝左”
            heading_img = math.radians(direction_deg(user_facing))
            self.heading_source = "user"
        elif facing == "auto" and vision_dir:
            heading_img = math.radians(DIRECTIONS[vision_dir.group(1)])
            self.heading_source = "vision"
        elif facing == "auto" and tip is not None and math.hypot(px(tip[0], w_px) - cx, px(tip[1], h_px) - cy) > 1:
            du, dv = px(tip[0], w_px) - cx, px(tip[1], h_px) - cy
            heading_img = math.atan2(-dv, du)
            self.heading_source = "tip"
        self.heading_from_tip = self.heading_source == "tip"
        self.tip_disagrees = False
        if facing == "up" and tip is not None:
            du, dv = px(tip[0], w_px) - cx, px(tip[1], h_px) - cy
            if math.hypot(du, dv) > 1:
                off = math.degrees(abs((math.atan2(-dv, du) - math.pi / 2 + math.pi) % (2 * math.pi) - math.pi))
                self.tip_disagrees = off > 60
        # 比例尺：车身转了 φ 时外框宽 = L|cosφ| + W|sinφ|，高 = L|sinφ| + W|cosφ|（φ 从 x 轴量）
        bw_px, bh_px = px(car[2] - car[0], w_px), px(car[3] - car[1], h_px)
        c, s = abs(math.cos(heading_img)), abs(math.sin(heading_img))
        bw_cm, bh_cm = CAR_LEN_CM * c + CAR_WID_CM * s, CAR_LEN_CM * s + CAR_WID_CM * c
        self.scale_from_car = (bw_px / bw_cm + bh_px / bh_cm) / 2
        # 摄像头架着不动时，量一次“照片横向拍到多宽的地面”最准；坦克外框只要框松一点，距离就整体缩小
        # （实测同一机位一次算出 2.4 米宽、一次 1.3 米宽）
        self.px_per_cm = w_px / view_cm if view_cm else self.scale_from_car
        if self.px_per_cm <= 0:
            raise ValueError("坦克外框大小不对")
        self.width_cm, self.height_cm = w_px / self.px_per_cm, h_px / self.px_per_cm
        self.pose = (cx / self.px_per_cm, (h_px - cy) / self.px_per_cm, heading_img)  # 估计的车位置
        self.obstacles = []
        for name, (x1, y1, x2, y2) in others:
            u1, v1, u2, v2 = px(x1, w_px), px(y1, h_px), px(x2, w_px), px(y2, h_px)
            self.obstacles.append((name, (u1 / self.px_per_cm, (h_px - v2) / self.px_per_cm,
                                          u2 / self.px_per_cm, (h_px - v1) / self.px_per_cm)))
        self.image = image
        self.legs = 0  # 闭眼走过几段：越多位置越不准

    def heading_words(self) -> str:
        """车头朝向说成人话（画面方向），让用户一听就知道有没有看错。"""
        names = ["右边", "右上方", "上方", "左上方", "左边", "左下方", "下方", "右下方"]
        return "画面" + names[round(math.degrees(self.pose[2]) / 45) % 8]

    def floor_map(self, extra_margin_cm: float, skip: str | None = None) -> FloorMap:
        import numpy as np

        W, H = int(self.width_cm / RES_CM) + 1, int(self.height_cm / RES_CM) + 1
        obstacle = np.zeros((H, W), np.uint8)
        fm = FloorMap(x0=0.0, y1=H * RES_CM, image=self._top_image(W, H), known=np.full((H, W), 255, np.uint8),
                      obstacle=obstacle)
        for name, (x1, y1, x2, y2) in self.obstacles:
            if name == skip:  # 要去的目标本身不挡路
                continue
            r1, c1 = int((fm.y1 - y2) / RES_CM), int(x1 / RES_CM)
            r2, c2 = int((fm.y1 - y1) / RES_CM), int(x2 / RES_CM)
            obstacle[max(r1, 0):max(r2 + 1, 0), max(c1, 0):max(c2 + 1, 0)] = 255
            ob = Obstacle((x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) * (y2 - y1), name)
            fm.obstacles.append(ob)
            fm.names[name] = ob
        # 膨胀：车身半径 + 余量 + 闭眼额外余量；照片外面本来就不在栅格里，车不会被规划出画面
        import cv2

        r = int((CAR_RADIUS_CM + MARGIN_CM + extra_margin_cm) / RES_CM)
        grown = cv2.dilate(obstacle, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0
        f = int(GRID_CM / RES_CM)
        Hc, Wc = H // f, W // f
        fm.blocked = grown[:Hc * f, :Wc * f].reshape(Hc, f, Wc, f).max(axis=(1, 3))
        return fm

    def _top_image(self, W, H):
        import numpy as np

        if self.image is None:
            return np.full((H, W, 3), 200, np.uint8)
        import cv2

        return cv2.resize(self.image, (W, H))


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def compile_route(pose, waypoints, cm_per_s: float, face=None) -> list[tuple[str, float]]:
    """路线点 → [("turn", 度，正数左转), ("forward", 毫秒)]。相邻的前进合并；face：最后原地转到正对这个点。"""
    x, y, h = pose
    steps, fwd_ms = [], 0.0

    def flush():
        nonlocal fwd_ms
        while fwd_ms > 0:  # 单个动作不超过 MAX_ACTION_MS
            chunk = min(fwd_ms, MAX_ACTION_MS)
            steps.append(("forward", chunk))
            fwd_ms -= chunk
        fwd_ms = 0.0

    for wx, wy in waypoints:
        dist = math.hypot(wx - x, wy - y)
        if dist < 1:
            continue
        bearing = math.atan2(wy - y, wx - x)
        turn = math.degrees(_wrap(bearing - h))
        if abs(turn) >= MIN_TURN_DEG:
            flush()
            steps.append(("turn", turn))
        h = bearing
        fwd_ms += dist / cm_per_s * 1000
        x, y = wx, wy
    flush()
    if face is not None and math.hypot(face[0] - x, face[1] - y) > 1:
        turn = math.degrees(_wrap(math.atan2(face[1] - y, face[0] - x) - h))
        if abs(turn) >= MIN_TURN_DEG:
            steps.append(("turn", turn))
    return steps


def route_end(pose, waypoints, face=None):
    x, y, h = pose
    for wx, wy in waypoints:
        if math.hypot(wx - x, wy - y) >= 1:
            h = math.atan2(wy - y, wx - x)
            x, y = wx, wy
    if face is not None and math.hypot(face[0] - x, face[1] - y) > 1:
        h = math.atan2(face[1] - y, face[0] - x)
    return x, y, h


def simplify(start, path, fm: FloorMap) -> list:
    """拉直：从当前点尽量直接连到最远的点，只要求 fm（基础余量的地图）上这段直线可走。
    规划用的地图多留了闭眼余量，路线会贴着膨胀区的圆弧拐很多小弯；每拐一次都多一份转向误差，所以能直连就直连。"""
    pts = [start] + list(path)
    out, i = [], 0
    while i < len(pts) - 1:
        j = len(pts) - 1
        while j > i + 1 and not fm.line_free(pts[i], pts[j]):
            j -= 1
        out.append(pts[j])
        i = j
    return out


def stop_short(start, path, center, stop_cm: float) -> list:
    """沿路线走，到离 center 还有 stop_cm 的地方就停：返回截短后的路线（不含起点）。起点已经够近时返回空。"""
    pts = [start] + list(path)
    if math.dist(start, center) <= stop_cm:
        return []
    out = []
    for a, b in zip(pts, pts[1:]):
        if math.dist(b, center) > stop_cm:
            out.append(b)
            continue
        lo, hi = 0.0, 1.0  # a 在外、b 在里：二分找到正好 stop_cm 的点
        for _ in range(30):
            mid = (lo + hi) / 2
            p = (a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid)
            lo, hi = (mid, hi) if math.dist(p, center) > stop_cm else (lo, mid)
        out.append((a[0] + (b[0] - a[0]) * lo, a[1] + (b[1] - a[1]) * lo))
        return out
    return out


def assumed_map(target: str, ahead_cm: float, size_cm: float = ASSUMED_SIZE_CM) -> "BlindMap":
    """不拍照：车在 3×3 米空地中间偏下、车头朝画面上方，target 在车头正前方 ahead_cm（中心到中心）。"""
    W = H = int(ASSUMED_FIELD_CM)  # 1 像素 = 1 厘米
    cx, cy = W / 2, H - 80
    ty = cy - ahead_cm
    pct = lambda v: v / W * 100  # noqa: E731  正方形，横纵同一个比例
    car = (pct(cx - CAR_WID_CM / 2), pct(cy - CAR_LEN_CM / 2), pct(cx + CAR_WID_CM / 2), pct(cy + CAR_LEN_CM / 2))
    tgt = (pct(cx - size_cm / 2), pct(ty - size_cm / 2), pct(cx + size_cm / 2), pct(ty + size_cm / 2))
    fmt = lambda b: ",".join(f"{v:.2f}" for v in b)  # noqa: E731
    return BlindMap(W, H, f"坦克={fmt(car)}；车头=上；{target}={fmt(tgt)}", view_cm=W)


Ask = Callable[..., Awaitable[str]]
Say = Callable[[str], Awaitable[None]]


class BlindNavigator:
    def __init__(self, tank: TankExecutor, ask: Ask, frame=None, say: Say | None = None,
                 cm_per_s: float = DEFAULT_CM_PER_S, facing: str = "auto", on_map=None,
                 view_cm: float | None = None):
        self.view_cm = view_cm  # 照片横向拍到的地面宽度（厘米）；给了就不靠坦克外框估比例尺
        self.tank = tank
        self.ask = ask  # camera.PhotoTool.ask
        self.frame = frame  # 拍一帧（numpy），用来知道照片尺寸、在网页上画地图；没有时按 1280×960 算
        self.say = say
        self.cm_per_s = cm_per_s
        self.facing = facing
        self.on_map = on_map  # (jpeg, 标题, 说明)：网页控制台显示规划的路线
        self.map: BlindMap | None = None

    async def build(self, target: str = "", facing: str = "") -> str:
        """self.blind.map：拍一张，记住场地。target：用户接下来要去的东西，让视觉模型一定标出来；
        facing：用户说的车头朝向（“对着垃圾桶”“朝左”），比视觉模型判断的可靠。"""
        img = await asyncio.to_thread(self.frame) if self.frame else None
        h, w = (img.shape[:2] if img is not None else (960, 1280))
        q = question(target or None)
        answer = await (self.ask(q, frame=img, timeout=VISION_TIMEOUT_S) if img is not None
                        else self.ask(q, timeout=VISION_TIMEOUT_S))
        try:
            self.map = BlindMap(w, h, answer, self.facing, image=img, user_facing=facing, view_cm=self.view_cm)
        except ValueError as e:
            return f"没看清场地：{e}。请让整个坦克都在画面里，摄像头尽量正对地面往下拍"
        m = self.map
        names = "、".join(n for n, _ in m.obstacles) or "没有障碍物"
        how = {"user": "按你说的", "vision": "视觉模型看", "tip": "按炮管尖估计",
               "default": "看不清车头，先按"}[m.heading_source]
        facing = f"{how}车头朝{m.heading_words()}"
        if m.heading_source != "user":
            facing += "（不对的话告诉我车头朝哪，例如“车头对着垃圾桶”）"
        if m.tip_disagrees:
            facing += "，但炮管看起来不是朝上，请把车头摆正朝上再拍一次"
        return (f"记住场地了：大约 {m.width_cm / 100:.1f}×{m.height_cm / 100:.1f} 米，障碍物：{names}；{facing}。"
                "之后去哪都闭着眼按路线走，不再拍照")

    def plan(self, target: str, where: str = "旁边"):
        """规划去 target 的路线 → (动作序列, 终点估计, 路线点, 目标中心, 用了多少额外余量, 规划用的地图)。"""
        m = self.map
        if m is None:
            raise RuntimeError("还没有记住场地：先说“拍一张俯拍图”")
        if where not in WHERE:
            raise ValueError(f"where 只能是 {'/'.join(WHERE)}")
        center, half, name = self._goal(target)
        if where not in ("旁边", "撞倒"):
            return self._plan_around(target, where, center, half)
        base = m.floor_map(0, skip=name)
        for extra in MARGIN_STEPS:
            fm = m.floor_map(extra, skip=name) if extra else base
            path = fm.plan(m.pose[:2], center)
            if path:
                break
        else:
            raise RuntimeError(f"找不到能绕开障碍物去“{target}”的路")
        path = simplify(m.pose[:2], path, base)
        path = stop_short(m.pose[:2], path, center, half + CAR_LEN_CM / 2 + STOP_GAP_CM)
        steps = compile_route(m.pose, path, self.cm_per_s, face=center)
        end = route_end(m.pose, path, face=center)
        if where == "撞倒":  # 已经正对目标、车头离它 STOP_GAP_CM：冲过去再退回来
            ram, back = STOP_GAP_CM + RAM_CM, BACK_CM
            steps += [("forward", ram / self.cm_per_s * 1000), ("backward", back / self.cm_per_s * 1000)]
            x, y, h = end
            end = (x + (ram - back) * math.cos(h), y + (ram - back) * math.sin(h), h)
        return steps, end, path, center, extra, fm

    def _plan_around(self, target, where, center, half):
        """绕到目标的后面/左边/右边，或绕一圈回来：目标本身也算障碍物，每段分开规划、分开拉直
        （整圈一起拉直的话，“起点 → 回到起点”是直线可走的，整圈会被拉成原地不动）。"""
        m = self.map
        start = m.pose[:2]
        d = math.dist(start, center)
        if d < 1:
            raise RuntimeError(f"车好像就在{target}上，没法判断前后")
        ux, uy = (center[0] - start[0]) / d, (center[1] - start[1]) / d  # 车 → 目标
        base = m.floor_map(0)
        for extra in MARGIN_STEPS:
            fm = m.floor_map(extra) if extra else base
            r = half + CAR_RADIUS_CM + MARGIN_CM + extra + AROUND_GAP_CM
            spot = {"后面": (center[0] + ux * r, center[1] + uy * r),
                    "左边": (center[0] - uy * r, center[1] + ux * r),  # 车看过去的左手边
                    "右边": (center[0] + uy * r, center[1] - ux * r)}
            goals = [spot["左边"], spot["后面"], spot["右边"], start] if where == "绕一圈" else [spot[where]]
            path, cur = [], start
            for g in goals:
                leg = fm.plan(cur, g)
                if not leg or math.dist(leg[-1], g) > GOAL_TOLERANCE_CM:
                    path = None
                    break
                path += simplify(cur, leg, base)
                cur = path[-1]
            if path:
                break
        else:
            raise RuntimeError(f"去不了{target}{where}：那边没有地方（出了照片范围或被别的东西挡住了）")
        steps = compile_route(m.pose, path, self.cm_per_s, face=center)
        end = route_end(m.pose, path, face=center)
        return steps, end, path, center, extra, fm

    def goto(self, target: str, where: str = "旁边", ahead_cm: int = 0) -> str:
        """self.blind.goto：规划好就开始闭眼走，立即返回计划。
        ahead_cm > 0，或者还没拍过俯拍图：不拍照，按 target 在车头正前方 ahead_cm（默认 50）厘米来规划。"""
        assumed = ""
        if ahead_cm or self.map is None:
            d = int(ahead_cm) or ASSUMED_AHEAD_CM
            if not 20 <= d <= 150:
                raise ValueError("目标离车要在 20–150 厘米之间")
            self.map = assumed_map(target, d)
            assumed = f"（没拍照，按{target}在车头正前方 {d} 厘米算）"
        m = self.map
        steps, end, path, center, extra, fm = self.plan(target, where)
        if where == "旁边" and not any(k == "forward" for k, _ in steps):
            raise RuntimeError(f"按地图看车已经在{target}旁边了，不用走。如果实际还没到，说明地图的比例尺不对："
                               "用 --blind-view-cm 填照片横向拍到的地面宽度，或者重新拍地图")
        self._publish(fm, path, center, target)
        total_s = sum(v for k, v in steps if k == "forward") / 1000 + sum(
            abs(v) / 90 * self.tank.ms_per_90 / 1000 for k, v in steps if k == "turn")
        narrow = f"，路比较窄，离障碍物只多留了 {extra:.0f} 厘米" if extra < EXTRA_MARGIN_CM else ""
        drift = f"（已经闭眼走了 {m.legs} 段，位置可能偏了，偏太多就说“重新拍地图”）" if m.legs >= 2 else ""
        desc = f"闭眼撞{target}" if where == "撞倒" else f"闭眼去{target}" + ("" if where == "旁边" else where)
        place = {"绕一圈": f"绕{target}一圈回来", "撞倒": f"撞过{target}"}.get(where, f"到{target}{where}")
        self.tank.start_mission(desc, lambda: self._run(steps, end, place))
        if where == "绕一圈":
            drift = drift or "（绕一圈拐弯多，回到起点可能偏二三十厘米）"
        return (f"开始{desc}{assumed}：{len(steps)} 个动作，约 {total_s:.0f} 秒，不再看摄像头{narrow}{drift}")

    def _goal(self, target: str):
        m = self.map
        for name, (x1, y1, x2, y2) in m.obstacles:
            if names_match(name, target):
                return ((x1 + x2) / 2, (y1 + y2) / 2), max(x2 - x1, y2 - y1) / 2, name
        known = "、".join(n for n, _ in m.obstacles) or "（地图上没有东西）"
        raise RuntimeError(f"地图上没有“{target}”，记住的东西有：{known}。"
                           f"可以重新调用 self.blind.map，target 填“{target}”，让视觉模型专门找它")

    async def _run(self, steps, end, place):
        prev = self.tank.persona
        self.tank.persona = "normal"  # 老爷爷走走停停、醉汉走 S 形都会改变走过的距离，闭眼走时先按正常走
        try:
            for kind, v in steps:
                if kind == "turn":
                    self.tank.turn("left" if v > 0 else "right", max(round(abs(v)), 1))
                else:
                    self.tank.move(kind, int(v), BLIND_SPEED)  # forward / backward
                await self.tank.wait_idle()
            self.map.pose = end
            self.map.legs += 1
            if self.say:
                await self.say(f"{place}了（闭眼走的，位置是估计的）")
        finally:
            self.tank.persona = prev

    def _publish(self, fm, path, goal, target):
        if not self.on_map:
            return
        try:
            from .camera import _encode

            self.on_map(_encode(fm.render(car=self.map.pose, path=path, goal=goal)), "闭眼路线",
                        f"去{target}：{len(path)} 个拐点")
        except Exception as e:  # noqa: BLE001 — 只是显示，画不出来不影响开车
            print(f"[blind] 画路线失败：{e}")
