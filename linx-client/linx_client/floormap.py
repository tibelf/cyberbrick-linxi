"""俯视地图：拍一张照片 → 以地上的锚点标记为原点，换算成真正的俯视地面图（厘米）→ 标出障碍物 → A* 规划绕开的路线。

为什么不让视觉大模型算坐标：实测它给的位置误差大、每次还不一样。这里坐标全由本地 OpenCV 算：
1. 锚点标记平放在地上，边长已知 → solvePnP 得到地面相对摄像头的位姿 → 地面到画面的单应矩阵，
   把照片“拉平”成每像素 1 厘米的俯视图（只取摄像头能看清的范围，最远 MAX_RANGE_CM）。
2. 障碍物 = 和地面颜色不一样的区域。地面长什么样，从锚点四周和车四周（这两处肯定是地面）取样，
   聚成几种颜色（木地板 + 地垫这种两种地面也行）；离这几种颜色都远的像素算障碍物。
   车、标记本身不算障碍物。照片外面、看不清的地方一律当成走不通。
3. 障碍物向外膨胀“车身半径 + 余量”，在 2 厘米一格的栅格上跑 A*，再把路线拉直成几个路线点。
局限（如实）：高的东西在照片里会“倒”向远离摄像头的方向，地图上占的地方比实际大（偏保守，宁可绕远）；
桌子底下这种从上面看不到的地方不知道；花纹很多的地毯可能被当成障碍物。
"""

import heapq
import math
from dataclasses import dataclass, field

RES_CM = 1.0  # 俯视图每像素多少厘米
GRID_CM = 2.0  # 规划栅格
MAX_RANGE_CM = 300.0  # 离摄像头太远的地面分辨率太差，不要
CAR_RADIUS_CM = 11.0  # Mini T 车身外接圆半径（约 14×18 cm）
MARGIN_CM = 4.0
FLOOR_TOL = 22.0  # Lab 颜色距离：离地面颜色超过这个算障碍物
MIN_OBSTACLE_CM2 = 30.0
FLOOR_RING_CM = (8.0, 30.0)  # 在锚点/车四周这个距离范围内取地面颜色样本
CAR_CLEAR_CM = 13.0  # 车自己占的地方不算障碍物


def _cv():
    import cv2
    import numpy as np

    return cv2, np


@dataclass
class Obstacle:
    x: float  # 中心（锚点坐标系，厘米）
    y: float
    area_cm2: float
    name: str = ""


@dataclass
class FloorMap:
    x0: float  # 俯视图左上角对应的世界坐标
    y1: float
    image: object  # 拉平后的俯视图（BGR）
    known: object  # 看得到的地面（uint8 0/255）
    obstacle: object  # 障碍物（uint8 0/255，每像素 RES_CM）
    blocked: object = None  # 规划用：膨胀后走不通的格子（bool，每格 GRID_CM）
    obstacles: list = field(default_factory=list)
    names: dict = field(default_factory=dict)  # 视觉模型给障碍物起的名字 → Obstacle

    # ---- 坐标换算 ----
    def to_px(self, x, y):
        return (x - self.x0) / RES_CM, (self.y1 - y) / RES_CM

    def to_cell(self, x, y):
        return int((self.y1 - y) // GRID_CM), int((x - self.x0) // GRID_CM)

    def cell_xy(self, r, c):
        return self.x0 + (c + 0.5) * GRID_CM, self.y1 - (r + 0.5) * GRID_CM

    def in_grid(self, r, c):
        return 0 <= r < self.blocked.shape[0] and 0 <= c < self.blocked.shape[1]

    def free(self, x, y) -> bool:
        r, c = self.to_cell(x, y)
        return self.in_grid(r, c) and not self.blocked[r, c]

    @property
    def size_cm(self):
        h, w = self.obstacle.shape[:2]
        return w * RES_CM, h * RES_CM

    # ---- 规划 ----
    def _nearest_free(self, cell, max_r=40):
        r0, c0 = cell
        if self.in_grid(r0, c0) and not self.blocked[r0, c0]:
            return cell
        best = None
        for rad in range(1, max_r):
            for dr in range(-rad, rad + 1):
                for dc in (-rad, rad) if abs(dr) != rad else range(-rad, rad + 1):
                    r, c = r0 + dr, c0 + dc
                    if self.in_grid(r, c) and not self.blocked[r, c]:
                        d = dr * dr + dc * dc
                        if best is None or d < best[0]:
                            best = (d, (r, c))
            if best:
                return best[1]
        return None

    def line_free(self, a, b) -> bool:
        """世界坐标两点之间的直线是否全程可走（按半格步长检查）。"""
        n = max(2, int(math.dist(a, b) / (GRID_CM / 2)))
        for i in range(n + 1):
            x = a[0] + (b[0] - a[0]) * i / n
            y = a[1] + (b[1] - a[1]) * i / n
            if not self.free(x, y):
                return False
        return True

    def plan(self, start, goal) -> list | None:
        """A*：返回路线点（世界坐标，不含起点），走不通返回 None。起点或终点在障碍物膨胀区里时，
        先挪到最近的可走格子（车刚好贴着东西、或目标本身就是障碍物时都会这样）。"""
        s = self._nearest_free(self.to_cell(*start))
        g = self._nearest_free(self.to_cell(*goal))
        if s is None or g is None:
            return None
        rows, cols = self.blocked.shape
        INF = float("inf")
        dist = {s: 0.0}
        prev = {}
        heap = [(0.0, s)]
        steps = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1),
                 (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414), (1, 1, 1.414)]
        while heap:
            _, cur = heapq.heappop(heap)
            if cur == g:
                break
            d0 = dist[cur]
            for dr, dc, w in steps:
                r, c = cur[0] + dr, cur[1] + dc
                if not (0 <= r < rows and 0 <= c < cols) or self.blocked[r, c]:
                    continue
                nd = d0 + w
                if nd < dist.get((r, c), INF):
                    dist[(r, c)] = nd
                    prev[(r, c)] = cur
                    heapq.heappush(heap, (nd + math.hypot(r - g[0], c - g[1]), (r, c)))
        if g not in dist:
            return None
        cells = [g]
        while cells[-1] != s:
            cells.append(prev[cells[-1]])
        pts = [self.cell_xy(*c) for c in reversed(cells)]
        # 拉直：从当前点出发，尽量直接连到最远的、中间没有障碍的点
        out, i = [], 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self.line_free(pts[i], pts[j]):
                j -= 1
            out.append(pts[j])
            i = j
        return out or [pts[-1]]

    def random_free_point(self, rng, near=None, min_cm=40.0, tries=300):
        """闲逛用：随便挑一个能走到的空地（离 near 至少 min_cm）。"""
        rows, cols = self.blocked.shape
        for _ in range(tries):
            r, c = rng.randrange(rows), rng.randrange(cols)
            if self.blocked[r, c]:
                continue
            p = self.cell_xy(r, c)
            if near is None or math.dist(p, near) >= min_cm:
                return p
        return None

    def find(self, text: str) -> Obstacle | None:
        for name, ob in self.names.items():
            if name and (name in text or text in name):
                return ob
        return None

    # ---- 显示 ----
    def render(self, car=None, path=None, goal=None):
        """网页上显示的地图：俯视图 + 红色障碍物 + 浅色膨胀区 + 车 + 路线。上方 = 锚点标记的上方。"""
        cv2, np = _cv()
        img = self.image.copy()
        img[self.known == 0] = (60, 60, 60)
        big = cv2.resize(self.blocked.astype(np.uint8), (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        img[(big > 0) & (self.known > 0)] = (img[(big > 0) & (self.known > 0)] * 0.6 + np.array([90, 90, 200]) * 0.4)
        img[self.obstacle > 0] = (40, 40, 220)
        for ob in self.obstacles:
            if ob.name:
                u, v = self.to_px(ob.x, ob.y)
                cv2.putText(img, ob.name.encode("ascii", "ignore").decode() or "#", (int(u), int(v)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
        u, v = self.to_px(0, 0)
        cv2.drawMarker(img, (int(u), int(v)), (0, 200, 255), cv2.MARKER_SQUARE, 10, 2)
        if path:
            pts = ([car[:2]] if car else []) + list(path)
            for a, b in zip(pts, pts[1:]):
                cv2.line(img, tuple(int(t) for t in self.to_px(*a)), tuple(int(t) for t in self.to_px(*b)), (0, 220, 0), 2)
        if goal:
            cv2.circle(img, tuple(int(t) for t in self.to_px(*goal)), 6, (0, 220, 0), 2)
        if car:
            u, v = self.to_px(car[0], car[1])
            cv2.circle(img, (int(u), int(v)), int(CAR_RADIUS_CM / RES_CM), (255, 160, 0), 2)
            cv2.line(img, (int(u), int(v)), (int(u + 15 * math.cos(car[2])), int(v - 15 * math.sin(car[2]))),
                     (255, 160, 0), 2)
        scale = max(1, int(480 / max(img.shape[:2])))
        return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST) if scale > 1 else img


def _footprint(view, w, h):
    """画面里能看到的地面范围（锚点坐标系），离摄像头 MAX_RANGE_CM 以内。"""
    _, np = _cv()
    cam = -view.R.T @ view.t
    pts = []
    for u in np.linspace(0, w - 1, 25):
        for v in np.linspace(0, h - 1, 25):
            p = view.floor_point((u, v))
            if p and math.hypot(p[0] - cam[0], p[1] - cam[1]) <= MAX_RANGE_CM:
                pts.append(p)
    if len(pts) < 4:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys), cam


def build_map(frame, tracker, anchor_id: int, extra_floor=(), boxes=None) -> FloorMap:
    """用一张照片建地图。画面里必须有平放在地上的锚点标记。
    extra_floor：已知是地面的点（例如车走过的地方）。
    boxes：视觉模型列出的障碍物 [(名字, x1, y1, x2, y2)]（照片像素）。给了的话，只有落在这些框里的
    “非地面颜色”才算障碍物，并用框的名字给它命名——地垫、地毯、地板花纹这类大模型知道不是障碍物的就不会误判。
    没给（没开视觉分析）时偏保守：颜色不像地面的都算障碍物（地垫也会被当成障碍物，车开上去过之后重建地图就好了）。"""
    cv2, np = _cv()
    view = tracker.anchor_view(frame, anchor_id)
    if view is None:
        raise RuntimeError(f"照片里看不到地上的 {anchor_id} 号锚点标记：把它平放在地上，让它和车都在画面里")
    h, w = frame.shape[:2]
    fp = _footprint(view, w, h)
    if fp is None:
        raise RuntimeError("看不到地面：摄像头要斜着往下拍")
    xmin, xmax, ymin, ymax, cam = fp
    W, H = int((xmax - xmin) / RES_CM) + 1, int((ymax - ymin) / RES_CM) + 1
    # 俯视图像素 (u, v) → 世界 (x0 + u·res, y1 − v·res, 0) → 画面像素：H_img = K·[r1 r2 t]·A
    A = np.array([[RES_CM, 0, xmin], [0, -RES_CM, ymax], [0, 0, 1]], float)
    Hw = view.K @ np.column_stack([view.R[:, 0], view.R[:, 1], view.t])
    M = Hw @ A
    top = cv2.warpPerspective(frame, M, (W, H), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR)
    known = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_NEAREST)
    # 只要离摄像头 MAX_RANGE_CM 以内的
    yy, xx = np.mgrid[0:H, 0:W]
    wx, wy = xmin + xx * RES_CM, ymax - yy * RES_CM
    known[np.hypot(wx - cam[0], wy - cam[1]) > MAX_RANGE_CM] = 0
    known = cv2.erode(known, np.ones((3, 3), np.uint8))
    fm = FloorMap(xmin, ymax, top, known, np.zeros((H, W), np.uint8))

    # ---- 地面颜色：锚点四周、车四周、已知地面点 ----
    lab = cv2.cvtColor(top, cv2.COLOR_BGR2LAB).astype(np.float32)
    ring = np.zeros((H, W), np.uint8)
    centers = [(0.0, 0.0)] + ([view.car[:2]] if view.car else []) + list(extra_floor)
    for cx, cy in centers:
        u, v = fm.to_px(cx, cy)
        cv2.circle(ring, (int(u), int(v)), int(FLOOR_RING_CM[1] / RES_CM), 255, -1)
        cv2.circle(ring, (int(u), int(v)), int(FLOOR_RING_CM[0] / RES_CM), 0, -1)
    # 标记本身（白纸黑块）和车不是地面也不是障碍物
    ignore = np.zeros((H, W), np.uint8)
    for mid, c in view.corners.items():
        pts = [fm.to_px(*p) for p in (view.floor_point(q) for q in c) if p]
        if len(pts) == 4:
            ctr = np.mean(pts, axis=0)
            poly = np.array([(ctr + (np.array(p) - ctr) * 1.6) for p in pts], np.int32)  # 连白边一起
            cv2.fillConvexPoly(ignore, poly, 255)
    if view.car:
        u, v = fm.to_px(*view.car[:2])
        cv2.circle(ignore, (int(u), int(v)), int(CAR_CLEAR_CM / RES_CM), 255, -1)
    samples = lab[(ring > 0) & (known > 0) & (ignore == 0)]
    if len(samples) < 50:
        raise RuntimeError("锚点四周看不到足够的地面，没法判断地面是什么颜色")
    k = min(4, max(1, len(samples) // 200))
    _, labels, cent = cv2.kmeans(samples, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
                                 3, cv2.KMEANS_PP_CENTERS)
    keep = [cent[i] for i in range(k) if (labels.flatten() == i).mean() > 0.08]  # 零星几个像素的颜色不算地面
    dist = np.min([np.linalg.norm(lab - c, axis=2) for c in keep], axis=0)
    obstacle = ((dist > FLOOR_TOL) & (known > 0) & (ignore == 0)).astype(np.uint8) * 255
    obstacle = cv2.morphologyEx(obstacle, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    named = None
    if boxes is not None:  # 视觉模型的框投到俯视图上（高的东西在俯视图上“倒下”的部分也在框里）
        named = np.zeros((H, W), np.int32)
        for k_, (_, x1, y1, x2, y2) in enumerate(boxes, 1):
            m = np.zeros((h, w), np.uint8)
            pad = 0.1 * max(x2 - x1, y2 - y1)
            cv2.rectangle(m, (int(x1 - pad), int(y1 - pad)), (int(x2 + pad), int(y2 + pad)), 255, -1)
            mw = cv2.warpPerspective(m, M, (W, H), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_NEAREST)
            named[(mw > 0) & (named == 0)] = k_
    # 按颜色把候选区域分开再逐块判断：地垫和挨着它的箱子连成一片时，不能整片一起留或一起扔
    cand = obstacle > 0
    pieces = []  # 每块：(mask, 名字)
    if cand.sum() > 0:
        px = lab[cand]
        kk = int(min(5, max(1, len(px) // 300)))
        _, lbl, _ = cv2.kmeans(px, kk, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
                               2, cv2.KMEANS_PP_CENTERS)
        color_of = np.full((H, W), -1, np.int32)
        color_of[cand] = lbl.flatten()
        for ci in range(kk):
            m = ((color_of == ci).astype(np.uint8)) * 255
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            nn, li = cv2.connectedComponents(m)
            pieces += [li == j for j in range(1, nn)]
    min_px = MIN_OBSTACLE_CM2 / (RES_CM * RES_CM)
    obstacle[:] = 0
    kept = []
    for comp in pieces:
        area = int(comp.sum())
        name = ""
        if area < min_px / 3:
            continue
        if named is not None:
            hit = named[comp]
            hit = hit[hit > 0]
            if len(hit) < 0.3 * area:
                continue  # 大模型没说这里有东西：地垫、地板花纹、光斑
            name = boxes[int(np.bincount(hit).argmax()) - 1][0]
        # 没有视觉模型时不猜“大片均匀颜色 = 地垫”：墙、沙发侧面也是大片均匀的颜色（2026-10-25 仿真里一堵墙被当成了地垫）。
        # 地垫要么由视觉模型排除，要么车开上去过（visited 会当作地面颜色样本）
        kept.append((comp, name))
        obstacle[comp] = 255
    # 同一个东西的几个面（顶面、侧面颜色不同）合回一块，统计成一个障碍物
    obstacle = cv2.morphologyEx(obstacle, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab_img, stats, cents = cv2.connectedComponentsWithStats(obstacle)
    for i in range(1, n):
        comp = lab_img == i
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_px:
            obstacle[comp] = 0
            continue
        names = [nm for m, nm in kept if nm and (m & comp).any()]
        name = max(set(names), key=names.count) if names else ""
        u, v = cents[i]
        ob = Obstacle(xmin + u * RES_CM, ymax - v * RES_CM, area * RES_CM ** 2, name)
        fm.obstacles.append(ob)
        if name:
            fm.names.setdefault(name, ob)
    fm.obstacle = obstacle
    _rebuild_blocked(fm)
    return fm


def _rebuild_blocked(fm: FloorMap):
    """障碍物和看不到的地方膨胀“车身半径 + 余量”，降采样到 GRID_CM 一格。"""
    cv2, np = _cv()
    bad = ((fm.obstacle > 0) | (fm.known == 0)).astype(np.uint8)
    r = int((CAR_RADIUS_CM + MARGIN_CM) / RES_CM)
    bad = cv2.dilate(bad, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    f = int(GRID_CM / RES_CM)
    H, W = bad.shape
    Hc, Wc = H // f, W // f
    fm.blocked = bad[:Hc * f, :Wc * f].reshape(Hc, f, Wc, f).max(axis=(1, 3)) > 0


def parse_obstacle_boxes(answer: str, w: int, h: int) -> list:
    """解析视觉模型的 “纸箱=10,20,30,40；椅子腿=…”（百分比外框，0-1000 也行）→ [(名字, x1, y1, x2, y2)] 像素。
    写了“无”就是空列表。"""
    import re

    out = []
    for m in re.finditer(r"([^\s=；;，,：:。]+)\s*[=:：]\s*(\d+(?:\.\d+)?)[,，\s]+(\d+(?:\.\d+)?)[,，\s]+"
                         r"(\d+(?:\.\d+)?)[,，\s]+(\d+(?:\.\d+)?)", answer):
        v = [float(g) for g in m.groups()[1:]]
        if max(v) > 100:
            v = [x / 10 for x in v]
        x1, x2 = sorted((v[0], v[2]))
        y1, y2 = sorted((v[1], v[3]))
        out.append((m.group(1), x1 / 100 * w, y1 / 100 * h, x2 / 100 * w, y2 / 100 * h))
    return out


OBSTACLE_QUESTION = ("列出画面里地面上会挡住一辆小坦克的东西（箱子、椅子腿、桌腿、瓶子、墙、家具等；"
                     "地垫、地毯、地板花纹、纸片、影子不算），每个给出外框，用百分比坐标（左上角 0,0，右下角 100,100，"
                     "写成 左,上,右,下）。只用这个格式回答一行，不要解释：名字=x1,y1,x2,y2；名字=x1,y1,x2,y2。"
                     "什么都没有就写 无")
