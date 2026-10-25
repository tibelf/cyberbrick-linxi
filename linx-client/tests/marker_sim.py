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
                self.x += v * math.cos(self.theta) * dt
                self.y += v * math.sin(self.theta) * dt
                if self.dist() < 12 and abs(v) > 0.8 * V_MAX:  # 全速撞上去会把目标撞倒
                    self.fallen = True
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
            look = (20, 20, 0)
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

    def render(self):
        self.update()
        R, t = self._pose()
        img = np.full((H, W, 3), 170, np.uint8)  # 灰色地面
        # 目标
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
        # 车顶标记：箭头（标记的“上”）朝车头
        f = np.array([math.cos(self.theta), math.sin(self.theta), 0.0])
        r = np.array([math.sin(self.theta), -math.cos(self.theta), 0.0])
        src, pad = marker_img(0)
        half = MARKER_CM / 2 * src.shape[0] / (src.shape[0] - 2 * pad)
        self._paste(img, cv2.cvtColor(src, cv2.COLOR_GRAY2BGR), self._quad((self.x, self.y, CAR_HEIGHT_CM), r, f, half), R, t)
        return img


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
