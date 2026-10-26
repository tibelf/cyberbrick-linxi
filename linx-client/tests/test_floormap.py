"""俯视地图：锚点定位、障碍物识别（地垫不算）、A* 绕行、沿路线开车不碰箱子、寻的/闲逛走地图。全部在仿真里跑。"""

import asyncio
import math
import random

import pytest

pytest.importorskip("cv2")

import marker_sim  # noqa: E402
from marker_sim import SimBackend, SimCamera, World  # noqa: E402

from linx_client.floormap import build_map, parse_obstacle_boxes  # noqa: E402
from linx_client.marker import MarkerTracker, marker_svg  # noqa: E402
from linx_client.marker_seek import MarkerSeeker  # noqa: E402
from linx_client.navigate import MapBuilder, Navigator  # noqa: E402
from linx_client.tank import TankExecutor  # noqa: E402
from linx_client.wander import Wanderer  # noqa: E402

BOX = (55, 5, 24, 24, 18, (40, 90, 160))  # 挡在车和目标中间的纸箱
MAT = ([(-200, 40), (250, 40), (250, 250), (-200, 250)], (120, 120, 125))  # 一大块灰色地垫（不是障碍物）


@pytest.fixture(autouse=True)
def hd(monkeypatch):
    monkeypatch.setattr(marker_sim, "W", 1280)
    monkeypatch.setattr(marker_sim, "H", 960)


def scene(**kw):
    kw.setdefault("cam", (-40, -100, 140))
    w = World(car=(0, 0, 0.0), target=(110, 10), **kw)
    w.follow, w.look = False, (50, 10)
    w.solid_target = True
    w.anchor = (3, -30, -30, 0.4)
    w.boxes = [BOX]
    w.floor_patches = [MAT]
    return w


def _tracker():
    t = MarkerTracker()
    t.anchor_id = 3
    return t


@pytest.mark.parametrize("cam", [(-60, -110, 110), (-20, -150, 120), (-120, -80, 120)])
def test_anchor_pose_same_from_any_camera(cam):
    """不管从哪拍，车在锚点坐标系里的位置和朝向都一样（所以手持也行）。"""
    w = scene(cam=cam)
    v = _tracker().anchor_view(w.render(), 3)
    tx, ty = w.to_anchor(w.x, w.y)
    assert v and v.car
    assert math.dist(v.car[:2], (tx, ty)) < 3
    assert abs(math.degrees(v.car[2] - (w.theta - 0.4))) < 5


def test_map_marks_box_and_is_conservative_about_mat_without_vision():
    w = scene()
    tr = _tracker()
    f = w.render()
    fm = build_map(f, tr, 3)
    box, mat = w.to_anchor(BOX[0], BOX[1]), w.to_anchor(20, 90)
    assert not fm.free(*box)  # 箱子走不通
    assert fm.free(*w.to_anchor(0, 0))  # 车所在的地方能走
    assert not fm.free(*mat)  # 没有视觉模型：分不清地垫和障碍物，先当障碍物（宁可绕远）
    # 车开到地垫上去过（visited）→ 重建地图时地垫的颜色也算地面
    fm2 = build_map(f, tr, 3, extra_floor=[w.to_anchor(20, 70)])
    assert fm2.free(*mat) and not fm2.free(*box)


def test_vision_says_mat_is_not_an_obstacle():
    w = scene()
    tr = _tracker()
    f = w.render()
    answer = "纸箱=%.0f,%.0f,%.0f,%.0f" % w.bbox_pct(BOX)
    fm = build_map(f, tr, 3, boxes=parse_obstacle_boxes(answer, marker_sim.W, marker_sim.H))
    assert fm.free(*w.to_anchor(20, 90)) and not fm.free(*w.to_anchor(BOX[0], BOX[1]))


def test_vision_names_obstacles_and_ignores_unlisted_things():
    w = scene()
    w.floor_patches.append(([(0, -45), (25, -45), (25, -25), (0, -25)], (40, 40, 200)))  # 一块红色小地毯
    tr = _tracker()
    f = w.render()
    assert not build_map(f, tr, 3).free(*w.to_anchor(12, -35))  # 只按颜色：小地毯也被当成障碍物（偏保守）
    answer = "纸箱=%.0f,%.0f,%.0f,%.0f" % w.bbox_pct(BOX)
    fm = build_map(f, tr, 3, boxes=parse_obstacle_boxes(answer, marker_sim.W, marker_sim.H))
    assert [o.name for o in fm.obstacles] == ["纸箱"]  # 大模型没提小地毯 → 不算障碍物
    assert fm.find("那个纸箱") is fm.obstacles[0]
    assert parse_obstacle_boxes("无", 100, 100) == []


def test_plan_goes_around_box():
    w = scene()
    tr = _tracker()
    f = w.render()
    fm = build_map(f, tr, 3)
    v = tr.anchor_view(f, 3)
    path = fm.plan(v.car[:2], v.markers[1])
    assert path and len(path) >= 2  # 不是一条直线
    pts = [v.car[:2]] + path
    for a, b in zip(pts, pts[1:]):
        assert fm.line_free(a, b) or a == pts[0]
    assert not fm.line_free(v.car[:2], v.markers[1])  # 直线会穿过箱子


def test_no_path_when_a_wall_cuts_the_floor_in_two():
    w = scene()
    w.boxes = [(75, 30, 8, 300, 25, (40, 90, 160))]  # 一堵横穿画面的墙（仿真里太长会伸到相机背后，画不出来）
    w.show_target = False
    tr = _tracker()
    f = w.render()
    fm = build_map(f, tr, 3)
    assert fm.plan(tr.anchor_view(f, 3).car[:2], w.to_anchor(110, 10)) is None


async def _drive(w, run, map_ok=True, **nav_kw):
    tr = _tracker()
    tank = TankExecutor(SimBackend(w), max_speed=100)
    await tank.start()
    nav = Navigator(tank, SimCamera(w), tr, 3, **nav_kw)
    nav.map = build_map(w.render(), tr, 3)
    if not map_ok:
        nav.map.blocked[:] = False  # 假装没有障碍物：直接冲
    out = {}

    async def mission():
        out["r"] = await run(nav, tr)

    tank.start_mission("测试", mission)
    while tank.status()["mission"]:
        await asyncio.sleep(0.05)
    await tank.close()
    return out.get("r"), nav


def _goto_target(nav, tr):
    async def go():
        v = await nav.look()
        return await nav.goto(v.markers[1], stop_cm=18)
    return go()


@pytest.mark.parametrize("shake", [0.0, 2.0])
def test_drives_around_box_without_touching_it(shake):
    w = scene(shake=shake)
    (ok, msg), nav = asyncio.run(_drive(w, _goto_target))
    assert ok, msg
    assert not w.collided and w.dist() < 25
    assert len(nav.visited) >= 3  # 走过的地方记下来了


def test_straight_line_would_hit_the_box():
    """对照组：不用地图直接朝目标开，会撞上箱子——说明上面没碰到是地图的功劳。"""
    w = scene()
    asyncio.run(_drive(w, _goto_target, map_ok=False))
    assert w.collided


def test_stops_when_anchor_not_visible():
    w = scene()

    async def run(nav, tr):
        w.anchor = None  # 建完地图后锚点被挡住了
        return await nav.goto(w.to_anchor_saved, stop_cm=10)

    w.to_anchor_saved = w.to_anchor(110, 10)
    (ok, msg), _ = asyncio.run(_drive(w, run, lost_s=0.5))
    assert not ok and "3 号锚点" in msg
    assert math.hypot(w.x, w.y) < 1  # 没有闭着眼开


async def _seek(w, target, goal, labels=None, ask=None):
    tr = _tracker()
    tank = TankExecutor(SimBackend(w), max_speed=100)
    await tank.start()
    nav = Navigator(tank, SimCamera(w), tr, 3)
    said = []

    async def say(t):
        said.append(t)

    builder = MapBuilder(nav, ask=ask)
    summary = await builder()
    seeker = MarkerSeeker(tank, SimCamera(w), tr, labels=labels or {}, ask=ask, say=say, navigator=nav)
    seeker(target, goal, False)
    while tank.status()["mission"]:
        await asyncio.sleep(0.05)
    await tank.close()
    return said, summary


def test_seek_hits_target_behind_box_via_map():
    w = scene()
    said, summary = asyncio.run(_seek(w, "绿色瓶子", "hit", labels={1: "绿色瓶子"}))
    assert "地图建好了" in summary
    assert said[0] in ("已把绿色瓶子撞倒了", "已经撞上绿色瓶子了（没开视觉分析，看不出倒没倒）"), said
    assert w.fallen and not w.collided


def test_seek_goes_to_obstacle_named_by_vision():
    w = scene()
    w.show_target = False

    async def ask(q, frame=None):
        return "纸箱=%.0f,%.0f,%.0f,%.0f" % w.bbox_pct(BOX)

    said, summary = asyncio.run(_seek(w, "纸箱", "approach", ask=ask))
    assert "纸箱" in summary
    assert said[0] == "已走到纸箱跟前", said
    bx, by = BOX[:2]
    assert math.hypot(w.x - bx, w.y - by) < 50 and not w.collided


def test_wander_on_map_never_touches_box():
    async def main():
        w = scene()
        w.show_target = False
        tr = _tracker()
        tank = TankExecutor(SimBackend(w), max_speed=100)
        await tank.start()
        nav = Navigator(tank, SimCamera(w), tr, 3)
        nav.map = build_map(w.render(), tr, 3)

        asked = []

        async def ask(q):
            asked.append(q)
            return "坦克=无；空地=无"

        wd = Wanderer(tank, ask, rng=random.Random(3))
        wd.navigator = nav
        wd()
        await asyncio.sleep(8)
        assert tank.status()["mission"] == "自由闲逛"
        await tank.stop()
        await tank.close()
        assert len(nav.visited) >= 4 and not w.collided
        assert not asked  # 有地图时按地图逛，不用视觉模型看路

    asyncio.run(main())


def test_marker_sheet_labels_anchor():
    assert "3 号 · 地图锚点" in marker_svg(6)
