"""ArUco 标记跟踪：每一帧算出“目标在车头左/右多少度、离车多少厘米”。

车顶贴 0 号标记（打印件上箭头朝车头），目标可以贴 1–9 号标记，或者按颜色认。
**结果只跟车和目标的相对位置有关，跟摄像头在哪无关**，所以摄像头可以固定，也可以手拿着手机跟拍：
1. 车顶标记的真实边长已知 → solvePnP 算出这一帧里标记（也就是车）相对摄像头的位置和朝向，
   标记平面就是和地面平行的“车身坐标系”：x 向车的右边，y 向车头，z 向上。
2. 目标是标记：同样算出它的三维位置，换算到车身坐标系。
   目标按颜色认：取色块的底边中点（贴地的点），从摄像头射出一条光线，和地面（车顶标记下方
   car_height_cm 处的平面）求交点，再换算到车身坐标系。
3. 方位角 = atan2(-x, y)（左正右负），距离 = 水平面内的长度。

摄像头内参不标定，焦距按 FOCAL_RATIO × 画面宽度估算（手机主摄/网络摄像头水平视角约 60–70°）。
估不准主要影响距离的比例，方向基本不受影响；闭环控制每一帧都重新测，误差会被不断修正。
"""

import math
from dataclasses import dataclass

DICT_NAME = "DICT_4X4_50"
CAR_ID = 0
TARGET_IDS = range(1, 10)
MARKER_CM = 6.0  # 打印出来的黑色方块边长
CAR_HEIGHT_CM = 7.0  # 车顶标记离地高度（Mini T 车顶大约这么高）
FOCAL_RATIO = 0.8

# OpenCV 的 HSV：H 0–180。只认饱和度够高的颜色，免得把灰白地面当成目标
COLORS = {
    "红": [((0, 110, 70), (8, 255, 255)), ((170, 110, 70), (180, 255, 255))],
    "橙": [((9, 110, 80), (19, 255, 255))],
    "黄": [((20, 100, 80), (34, 255, 255))],
    "绿": [((35, 80, 50), (85, 255, 255))],
    "蓝": [((95, 110, 50), (128, 255, 255))],
    "紫": [((129, 80, 50), (165, 255, 255))],
}
MIN_BLOB = 0.0004  # 色块至少占画面这么大比例


def _cv():
    import cv2
    import numpy as np

    return cv2, np


@dataclass
class Target:
    kind: str  # marker / color / any_marker
    marker_id: int | None = None
    color: str | None = None
    label: str = ""


def parse_labels(spec: str | None) -> dict[int, str]:
    """“1=绿色瓶子,2=红色盒子” → {1: "绿色瓶子", 2: "红色盒子"}"""
    out = {}
    for part in (spec or "").replace("，", ",").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().isdigit() and int(k) in TARGET_IDS and v.strip():
                out[int(k)] = v.strip()
    return out


def resolve_target(text: str, labels: dict[int, str]) -> Target:
    """把大模型给的目标说法（“绿色瓶子”）对应到一个标记或一种颜色。

    优先级：登记过名字的标记 → “3号标记”这种说法 → 颜色词 → 画面里唯一的目标标记（运行时再看）。"""
    for mid, label in labels.items():
        if label in text or text in label:
            return Target("marker", marker_id=mid, label=label)
    for ch, n in (("一", 1), ("二", 2), ("三", 3), ("四", 4), ("五", 5), ("六", 6), ("七", 7), ("八", 8), ("九", 9)):
        text_n = text.replace(ch + "号", f"{n}号")
        for mid in TARGET_IDS:
            if f"{mid}号" in text_n:
                return Target("marker", marker_id=mid, label=text)
    for color in COLORS:
        if color in text:
            return Target("color", color=color, label=text)
    return Target("any_marker", label=text)


@dataclass
class Sighting:
    car: bool = False
    target: bool = False
    bearing: float | None = None  # 目标在车头的哪边：度，左正右负
    distance: float | None = None  # 车中心到目标，厘米（水平面内）
    target_tilt: float | None = None  # 目标标记和地面的夹角（度），用来判断撞倒了没有；颜色目标没有
    target_id: int | None = None
    corners: dict | None = None  # 画调试图用：{id: 4x2 角点}
    target_px: tuple | None = None  # 颜色目标的贴地点（像素）


class MarkerTracker:
    def __init__(self, marker_cm: float = MARKER_CM, car_height_cm: float = CAR_HEIGHT_CM,
                 focal_ratio: float = FOCAL_RATIO):
        cv2, np = _cv()
        self.cv2, self.np = cv2, np
        self.marker_cm = marker_cm
        self.car_height_cm = car_height_cm
        self.focal_ratio = focal_ratio
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICT_NAME))
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self.detector = cv2.aruco.ArucoDetector(dictionary, params)
        h = marker_cm / 2  # 角点顺序与检测结果一致：左上、右上、右下、左下（标记的“上”= 车头）
        self.obj = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], np.float32)

    def camera_matrix(self, w: int, h: int):
        f = self.focal_ratio * w
        return self.np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], float)

    def _pose(self, corners, K):
        cv2, np = self.cv2, self.np
        ok, rvec, tvec = cv2.solvePnP(self.obj, corners.astype(np.float32), K, None, flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if not ok:
            return None
        R, _ = cv2.Rodrigues(rvec)
        return R, tvec.reshape(3)

    def detect(self, frame) -> dict:
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return {}
        return {int(i): c.reshape(4, 2) for i, c in zip(ids.flatten(), corners)}

    def _floor_point(self, px, K, R, t):
        """像素点（地面上的点）→ 车身坐标系 (x, y)。光线和 z = -car_height 的平面求交。"""
        np = self.np
        d = np.linalg.inv(K) @ np.array([px[0], px[1], 1.0])
        dm, tm = R.T @ d, R.T @ t  # 光线方向、摄像头原点在车身坐标系里：p = s·dm − tm
        if abs(dm[2]) < 1e-6:
            return None
        s = (tm[2] - self.car_height_cm) / dm[2]
        if s <= 0:
            return None  # 交点在摄像头背后：这一点不在地面上（比如在地平线以上）
        p = s * dm - tm
        return float(p[0]), float(p[1])

    def _color_blob(self, frame, color: str, exclude: list):
        """最大的那块指定颜色的贴地点（色块外框的底边中点）。exclude：车顶标记附近不算（车身可能同色）。"""
        cv2, np = self.cv2, self.np
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = None
        for lo, hi in COLORS[color]:
            m = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
            mask = m if mask is None else mask | m
        for cx, cy, r in exclude:
            cv2.circle(mask, (int(cx), int(cy)), int(r), 0, -1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) < MIN_BLOB * frame.shape[0] * frame.shape[1]:
            return None
        x, y, w, h = cv2.boundingRect(c)
        return x + w / 2, y + h

    def locate(self, frame, target: Target) -> Sighting:
        np = self.np
        found = self.detect(frame)
        s = Sighting(corners=found)
        if CAR_ID not in found:
            return s
        K = self.camera_matrix(frame.shape[1], frame.shape[0])
        pose = self._pose(found[CAR_ID], K)
        if pose is None:
            return s
        s.car = True
        R, t = pose
        xy = None
        if target.kind == "color":
            c = found[CAR_ID].mean(axis=0)
            side = np.linalg.norm(found[CAR_ID][0] - found[CAR_ID][2])
            px = self._color_blob(frame, target.color, [(c[0], c[1], side * 1.6)])
            if px is not None:
                s.target_px = px
                xy = self._floor_point(px, K, R, t)
        else:
            mid = target.marker_id
            if mid is None:  # any_marker：画面里正好一个目标标记才用它，有好几个时不瞎猜
                ids = [i for i in found if i in TARGET_IDS]
                mid = ids[0] if len(ids) == 1 else None
            if mid is not None and mid in found:
                tp = self._pose(found[mid], K)
                if tp is not None:
                    Rt, tt = tp
                    p = R.T @ (tt - t)
                    xy = float(p[0]), float(p[1])
                    n = R.T @ Rt[:, 2]  # 目标标记的法线，在车身坐标系里
                    s.target_tilt = math.degrees(math.asin(min(1.0, abs(float(n[2])))))
                    s.target_id = mid
        if xy is not None:
            s.target = True
            s.bearing = math.degrees(math.atan2(-xy[0], xy[1]))
            s.distance = math.hypot(*xy)
        return s

    def annotate(self, frame, s: Sighting):
        """调试图：框出标记，画车到目标的连线，写上方位和距离。"""
        cv2, np = self.cv2, self.np
        img = frame.copy()
        for mid, c in (s.corners or {}).items():
            pts = c.astype(np.int32)
            color = (0, 200, 0) if mid == CAR_ID else (0, 140, 255)
            cv2.polylines(img, [pts], True, color, 2)
            cv2.putText(img, "CAR" if mid == CAR_ID else str(mid), tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        if s.target_px:
            cv2.circle(img, (int(s.target_px[0]), int(s.target_px[1])), 6, (0, 140, 255), -1)
        if s.car and s.target:
            text = f"{'L' if s.bearing > 0 else 'R'} {abs(s.bearing):.0f}deg  {s.distance:.0f}cm"
        else:
            text = "no car marker" if not s.car else "no target"
        cv2.putText(img, text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        return img


def marker_svg(size_cm: float = MARKER_CM, target_ids=(1, 2, 3)) -> str:
    """生成可打印的标记（SVG 按毫米标尺寸，打印时选“实际大小 / 100%”就是准确尺寸）。"""
    cv2, _ = _cv()
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICT_NAME))
    mm = size_cm * 10
    cell = mm / 6
    gap = 25  # 标记之间留白，四周的白边是识别必需的
    items = [(CAR_ID, "0 号 · 贴车顶", "箭头朝车头")] + [(i, f"{i} 号 · 贴目标", "") for i in target_ids]
    cols = 2
    rows = (len(items) + cols - 1) // cols
    pitch_x, pitch_y = mm + gap * 2, mm + gap + 22
    width = gap + cols * pitch_x
    height = gap + rows * pitch_y + 14
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" '
           f'viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>']
    for k, (mid, label, note) in enumerate(items):
        x0, y0 = gap + (k % cols) * pitch_x, gap + (k // cols) * pitch_y
        bits = cv2.aruco.generateImageMarker(dictionary, mid, 6, borderBits=1)
        for r in range(6):
            for c in range(6):
                if bits[r, c] == 0:
                    out.append(f'<rect x="{x0 + c * cell:.3f}" y="{y0 + r * cell:.3f}" '
                               f'width="{cell + 0.02:.3f}" height="{cell + 0.02:.3f}" fill="black"/>')
        if mid == CAR_ID:  # 箭头：标记的“上”就是车头
            cx = x0 + mm / 2
            out.append(f'<path d="M{cx - 5},{y0 - 4} L{cx},{y0 - 12} L{cx + 5},{y0 - 4} Z" fill="black"/>')
        out.append(f'<text x="{x0}" y="{y0 + mm + 9}" font-size="5">{label}</text>')
        if note:
            out.append(f'<text x="{x0}" y="{y0 + mm + 16}" font-size="4">{note}</text>')
    out.append(f'<text x="{gap}" y="{height - 12}" font-size="3.5">黑框边长 {size_cm:g} cm。打印时选“实际大小/100%”，'
               f'打印后量一下，不是 {size_cm:g} cm 就用 --marker-size-cm 改成实际值。</text>')
    out.append(f'<text x="{gap}" y="{height - 6}" font-size="3.5">剪的时候黑框外面至少留 1 cm 白边，贴平、别反光。</text>')
    out.append("</svg>")
    return "\n".join(out)
