"""标记跟踪的仿真：地面上一辆贴了标记的车 + 目标，从一台（可以晃动的）虚拟相机渲染画面。

坐标：世界 x 向东、y 向北、z 向上，单位厘米；车头朝向 theta（弧度，逆时针 = 左转为正）。
"""

import math
import random
import time

import cv2
import numpy as np

from linx_client.marker import CAR_HEIGHT_CM, DICT_NAME, FOCAL_RATIO, MARKER_CM

W, H = 640, 480
DICTIONARY = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICT_NAME))
V_MAX = 60.0  # 满速前进 cm/s（仿真里调快一点，测试跑得快）
W_MAX = math.radians(225)  # 满速原地转：90° / 400ms


def marker_img(mid, px=120):
    m = cv2.aruco.generateImageMarker(DICTIONARY, mid, 6, borderBits=1)
    m = cv2.resize(m, (px, px), interpolation=cv2.INTER_NEAREST)
    pad = px // 4  # 四周留白
    return cv2.copyMakeBorder(m, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255), pad


def look_at(cam, target):
    """相机位姿：返回 (R, t)，世界点 X → 相机坐标 R @ X + t（OpenCV：x 右、y 下、z 前）。"""
    cam, target = np.asarray(cam, float), np.asarray(target, float)
    z = target - cam
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z])
    return R, -R @ cam


class World:
    def __init__(self, car=(0.0, 0.0, 0.0), target=(60.0, 40.0), target_kind="marker", target_id=1,
                 cam=(-40.0, -60.0, 70.0), shake=0.0, focal_ratio=FOCAL_RATIO, seed=0):
        self.x, self.y, self.theta = car
        self.tx, self.ty = target
        self.target_kind = target_kind  # marker / color
        self.target_id = target_id
        self.fallen = False
        self.cam = cam
        self.shake = shake  # 手持晃动幅度（厘米）
        self.rng = random.Random(seed)
        self.K = np.array([[focal_ratio * W, 0, W / 2], [0, focal_ratio * W, H / 2], [0, 0, 1]])
        self.left = self.right = 0.0
        self.until = 0.0
        self.last = time.monotonic()
        self.follow = True  # 手持：镜头跟着车和目标的中点
        self.marker_reversed = False  # 车顶标记贴反了（箭头朝车尾）
        self.look = (20.0, 20.0)  # 不跟拍时镜头对准的地面点
        self.anchor = None  # 地上的锚点标记 (id, x, y, yaw)
        self.boxes = []  # 障碍物：(x, y, 宽, 深, 高, BGR 颜色)
        self.floor_patches = []  # 地垫等：(四个角的世界坐标, BGR 颜色)
        self.show_target = True
        self.solid_target = False  # True：立着的目标会挡住车（地图测试用；老测试保持原样）
        self.collided = False

    # ---- 运动 ----
    def set_drive(self, left, right, ms):
        self.update()
        self.left, self.right = left, right
        self.until = time.monotonic() + ms / 1000

    def update(self):
        now = time.monotonic()
        t = self.last
        while t < now:
            dt = min(0.01, now - t)
            if t < self.until:
                v = (self.left + self.right) / 2 / 100 * V_MAX
                w = (self.right - self.left) / 2 / 100 * W_MAX
                self.theta += w * dt
                nx, ny = self.x + v * math.cos(self.theta) * dt, self.y + v * math.sin(self.theta) * dt
                if self.solid_target and not self.fallen and self.show_target \
                        and math.hypot(self.tx - nx, self.ty - ny) < 10 <= self.dist():
                    if abs(v) > 0.8 * V_MAX:  # 全速撞上去会把目标撞倒
                        self.fallen = True
                    else:
                        nx, ny = self.x, self.y  # 立着的瓶子挡住车，车穿不过去
                self.x, self.y = nx, ny
                if not self.solid_target and self.dist() < 12 and abs(v) > 0.8 * V_MAX:
                    self.fallen = True
                for bx, by, bw, bd, _, _ in self.boxes:  # 车身（半径约 9cm）碰到箱子
                    if abs(self.x - bx) < bw / 2 + 9 and abs(self.y - by) < bd / 2 + 9:
                        self.collided = True
            t += dt
        self.last = now

    def dist(self):
        return math.hypot(self.tx - self.x, self.ty - self.y)

    def bearing(self):
        """目标相对车头的真实方位（度，左正）。"""
        a = math.atan2(self.ty - self.y, self.tx - self.x) - self.theta
        return math.degrees((a + math.pi) % (2 * math.pi) - math.pi)

    # ---- 渲染 ----
    def _pose(self):
        cx, cy, cz = self.cam
        if self.follow:
            mx, my = (self.x + self.tx) / 2, (self.y + self.ty) / 2
            cx, cy = mx + cx, my + cy
            look = (mx, my, 0)
        else:
            look = (self.look[0], self.look[1], 0)
        j = lambda: self.rng.uniform(-self.shake, self.shake)  # noqa: E731
        return look_at((cx + j(), cy + j(), cz + j()), (look[0] + j(), look[1] + j(), 0))

    def _paste(self, img, src, quad_world, R, t):
        pts, _ = cv2.projectPoints(np.array(quad_world, float), cv2.Rodrigues(R)[0], t, self.K, None)
        h, w = src.shape[:2]
        M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), np.float32(pts.reshape(4, 2)))
        warped = cv2.warpPerspective(src, M, (W, H), borderValue=0)
        mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H))
        img[mask > 0] = warped[mask > 0]

    def _quad(self, center, xaxis, yaxis, half):
        c, xa, ya = (np.asarray(v, float) for v in (center, xaxis, yaxis))
        return [c - half * xa + half * ya, c + half * xa + half * ya, c + half * xa - half * ya, c - half * xa - half * ya]

    def to_anchor(self, x, y):
        """世界坐标 → 锚点坐标系（地图坐标）。"""
        _, ax, ay, yaw = self.anchor
        dx, dy = x - ax, y - ay
        return dx * math.cos(-yaw) - dy * math.sin(-yaw), dx * math.sin(-yaw) + dy * math.cos(-yaw)

    def bbox_pct(self, box):
        """箱子在画面里的外框（百分比），当作“视觉模型的回答”用。"""
        R, t = self._pose()
        bx, by, bw, bd, bh, _ = box
        pts3d = [(bx + sx * bw / 2, by + sy * bd / 2, z) for sx in (-1, 1) for sy in (-1, 1) for z in (0, bh)]
        pts, _ = cv2.projectPoints(np.array(pts3d, float), cv2.Rodrigues(R)[0], t, self.K, None)
        p = pts.reshape(-1, 2)
        return (p[:, 0].min() / W * 100, p[:, 1].min() / H * 100, p[:, 0].max() / W * 100, p[:, 1].max() / H * 100)

    def _poly(self, img, pts3d, color, R, t):
        pts, _ = cv2.projectPoints(np.array(pts3d, float), cv2.Rodrigues(R)[0], t, self.K, None)
        cv2.fillConvexPoly(img, np.int32(pts.reshape(-1, 2)), color, lineType=cv2.LINE_AA)

    def _boxes(self, img, R, t):
        faces = []
        for bx, by, bw, bd, bh, color in self.boxes:
            x0, x1, y0, y1 = bx - bw / 2, bx + bw / 2, by - bd / 2, by + bd / 2
            c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            faces.append(([(x, y, bh) for x, y in c], color))
            for i in range(4):
                (xa, ya), (xb, yb) = c[i], c[(i + 1) % 4]
                shade = tuple(int(v * (0.65 + 0.1 * i)) for v in color)
                faces.append(([(xa, ya, 0), (xb, yb, 0), (xb, yb, bh), (xa, ya, bh)], shade))
        depth = lambda f: float(np.mean([(R @ np.array(p) + t)[2] for p in f[0]]))  # noqa: E731
        for pts, color in sorted(faces, key=depth, reverse=True):  # 远的先画
            self._poly(img, pts, color, R, t)

    def render(self):
        self.update()
        R, t = self._pose()
        img = np.full((H, W, 3), 170, np.uint8)  # 灰色地面
        for quad, color in self.floor_patches:
            self._poly(img, [(x, y, 0) for x, y in quad], color, R, t)
        if self.anchor:
            aid, ax, ay, yaw = self.anchor
            src, pad = marker_img(aid)
            half = MARKER_CM / 2 * src.shape[0] / (src.shape[0] - 2 * pad)
            xa, ya = (math.cos(yaw), math.sin(yaw), 0), (-math.sin(yaw), math.cos(yaw), 0)
            self._paste(img, cv2.cvtColor(src, cv2.COLOR_GRAY2BGR), self._quad((ax, ay, 0.05), xa, ya, half), R, t)
        self._boxes(img, R, t)
        if not self.show_target:
            pass
        elif self.target_kind == "none":
            pass
        else:
            self._render_target(img, R, t)
        self._render_car(img, R, t)
        return img

    def _render_target(self, img, R, t):
        fwd = np.array([self.x - self.tx, self.y - self.ty, 0.0])  # 面朝车起点方向
        fwd /= np.linalg.norm(fwd) or 1
        side = np.cross([0, 0, 1.0], fwd)
        if self.target_kind == "marker":
            src, pad = marker_img(self.target_id)
            half = MARKER_CM / 2 * src.shape[0] / (src.shape[0] - 2 * pad)
            if self.fallen:
                quad = self._quad((self.tx, self.ty, 0.5), -side, fwd, half)  # 倒下：标记平躺
            else:
                quad = self._quad((self.tx, self.ty, 10.0), side, (0, 0, 1), half)  # 立着：竖直面朝车
            self._paste(img, cv2.cvtColor(src, cv2.COLOR_GRAY2BGR), quad, R, t)
        else:
            green = np.zeros((40, 12, 3), np.uint8)
            green[:] = (40, 200, 40)
            cam_dir = np.array([self.tx, self.ty, 0]) - (-R.T @ t)
            cam_dir[2] = 0
            cam_dir /= np.linalg.norm(cam_dir)
            sx = np.cross([0, 0, 1.0], -cam_dir)
            tc = np.array([self.tx, self.ty, 0.0])
            if self.fallen:
                quad = [tc + 3 * sx + 20 * cam_dir, tc - 3 * sx + 20 * cam_dir, tc - 3 * sx, tc + 3 * sx]
            else:  # 立着的瓶子：宽 6、高 20，面朝相机
                quad = [tc - 3 * sx + (0, 0, 20), tc + 3 * sx + (0, 0, 20), tc + 3 * sx, tc - 3 * sx]
            self._paste(img, green, quad, R, t)

    def _render_car(self, img, R, t):
        # 车顶标记：箭头（标记的“上”）朝车头
        f = np.array([math.cos(self.theta), math.sin(self.theta), 0.0])
        r = np.array([math.sin(self.theta), -math.cos(self.theta), 0.0])
        if self.marker_reversed:
            f, r = -f, -r
        src, pad = marker_img(0)
        half = MARKER_CM / 2 * src.shape[0] / (src.shape[0] - 2 * pad)
        self._paste(img, cv2.cvtColor(src, cv2.COLOR_GRAY2BGR), self._quad((self.x, self.y, CAR_HEIGHT_CM), r, f, half), R, t)


class SimCamera:
    def __init__(self, world):
        self.world = world

    def frame(self):
        return self.world.render()

    def snapshot(self):
        return cv2.imencode(".jpg", self.frame())[1].tobytes()


class SimBackend:
    def __init__(self, world):
        self.world = world
        self.calls = []

    async def connect(self):
        pass

    async def drive(self, left, right, failsafe_ms):
        self.calls.append(("drive", left, right, failsafe_ms))
        self.world.set_drive(left, right, failsafe_ms)

    async def turret(self, speed, failsafe_ms):
        pass

    async def fire(self, pulse_ms):
        self.calls.append(("fire", pulse_ms))

    async def halt(self):
        self.world.set_drive(0, 0, 0)

    async def led(self, r, g, b):
        pass

    async def close(self):
        pass
