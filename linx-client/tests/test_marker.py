"""ArUco 标记跟踪 + 闭环开到目标：用仿真画面（可以手持晃动）跑通，检查方位、距离、急停和撞倒。"""

import asyncio

import pytest

pytest.importorskip("cv2")

from marker_sim import SimBackend, SimCamera, World  # noqa: E402

from linx_client.marker import MarkerTracker, Target, marker_svg, parse_labels, resolve_target  # noqa: E402
from linx_client.marker_seek import ARRIVE_CM, MarkerSeeker  # noqa: E402
from linx_client.tank import MockBackend, TankExecutor  # noqa: E402

CAMS = [(-40, -60, 70), (30, -80, 60), (-70, -20, 60), (-30, -90, 45), (0, -60, 100)]


@pytest.mark.parametrize("cam", CAMS)
@pytest.mark.parametrize("kind", ["marker", "color"])
def test_bearing_and_distance_independent_of_camera(cam, kind):
    """同一个摆放，从不同位置、高度、角度拍，算出的方位/距离都接近真实值（手持换角度也行）。"""
    w = World(cam=cam, car=(0, 0, 0.3), target_kind=kind)
    target = Target("marker", marker_id=1) if kind == "marker" else Target("color", color="绿")
    s = MarkerTracker().locate(w.render(), target)
    assert s.car and s.target
    assert abs(s.bearing - w.bearing()) < 5
    assert abs(s.distance - w.dist()) < 0.1 * w.dist()


def test_wrong_focal_guess_keeps_direction():
    """焦距估错 15%：距离比例会偏，方向仍然基本对（闭环会不断修正）。"""
    w = World(cam=(-40, -60, 70), car=(0, 0, 0.3), focal_ratio=0.95)
    s = MarkerTracker().locate(w.render(), Target("marker", marker_id=1))
    assert abs(s.bearing - w.bearing()) < 8


def test_no_car_marker():
    w = World(cam=(-40, -60, 70))
    w.x = 500  # 车开出画面
    s = MarkerTracker().locate(w.render(), Target("marker", marker_id=1))
    assert not s.car and not s.target


def test_resolve_target():
    labels = parse_labels("1=绿色瓶子，2=红色盒子")
    assert labels == {1: "绿色瓶子", 2: "红色盒子"}
    assert resolve_target("绿色瓶子", labels).marker_id == 1
    assert resolve_target("瓶子", labels).marker_id == 1
    assert resolve_target("3号标记", labels).marker_id == 3
    assert resolve_target("三号", {}).marker_id == 3
    t = resolve_target("蓝色杯子", labels)
    assert t.kind == "color" and t.color == "蓝"
    assert resolve_target("那个东西", {}).kind == "any_marker"


def test_marker_svg_fits_a4_so_browser_does_not_shrink_it():
    import re

    w, h = (float(v) for v in re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', marker_svg(6)).groups())
    assert w <= 185 and h <= 270  # 超出 A4 可打印区域时浏览器会自动缩小，打出来就不是 6 cm（2026-10-25 用户打印预览只有约 4.7 cm）
    with pytest.raises(ValueError):
        marker_svg(20)


def test_marker_svg_has_exact_size():
    svg = marker_svg(6)
    assert 'font-size' in svg and "黑框边长 6 cm" in svg and "0 号 · 贴车顶" in svg and svg.count("<rect") > 4 * 10


async def _run(world, goal="approach", target="绿色瓶子", labels=None, **kw):
    tank = TankExecutor(SimBackend(world), max_speed=100)
    await tank.start()
    said = []

    async def say(t):
        said.append(t)

    seeker = MarkerSeeker(tank, SimCamera(world), labels=labels if labels is not None else {1: "绿色瓶子"},
                          say=say, **kw)
    seeker(target, goal, False)
    while tank.status()["mission"]:
        await asyncio.sleep(0.05)
    await tank.close()
    return said


@pytest.mark.parametrize("shake", [0.0, 3.0])
def test_drives_to_target_with_handheld_camera(shake):
    """车头不朝目标（偏 70° 多），摄像头跟着拍还在抖：边开边修正，最后停在目标跟前。"""
    world = World(car=(0, 0, -0.6), target=(60, 40), cam=(-40, -60, 70), shake=shake)
    said = asyncio.run(_run(world, time_limit_s=20))
    assert said and said[0].startswith("已走到绿色瓶子"), said
    assert world.dist() < ARRIVE_CM + 6


def test_drives_to_color_target():
    world = World(car=(0, 0, 0.3), target=(70, 20), target_kind="color", cam=(-40, -60, 70))
    said = asyncio.run(_run(world, target="绿色瓶子", labels={}, time_limit_s=20))
    assert said[0].startswith("已走到"), said
    assert world.dist() < ARRIVE_CM + 8


def test_color_target_right_next_to_car_still_seen():
    """瓶子紧挨着车时也要认得出（之前把车周围一圈像素抹掉，连瓶子一起抹了，负载高时会报“看不到”）。"""
    w = World(car=(0, 0, 0.0), target=(13, 0), target_kind="color", cam=(-40, -60, 70))
    s = MarkerTracker().locate(w.render(), Target("color", color="绿"))
    assert s.target and abs(s.distance - 13) < 3


def test_aim_turns_in_place():
    world = World(car=(0, 0, -1.0), target=(60, 40), cam=(-40, -60, 70))
    said = asyncio.run(_run(world, goal="aim", time_limit_s=10))
    assert said[0].startswith("已对准") and abs(world.bearing()) < 8
    assert abs(world.x) < 3 and abs(world.y) < 3  # 原地转，没有开走


def test_hit_knocks_target_over():
    world = World(car=(0, 0, 0.3), target=(60, 30), cam=(-40, -60, 70))
    said = asyncio.run(_run(world, goal="hit", time_limit_s=20))
    assert said[0] == "已把绿色瓶子撞倒了" and world.fallen


def test_stops_when_target_lost():
    world = World(car=(0, 0, 0.3), target=(60, 30), cam=(-40, -60, 70))
    world.target_id = 5  # 画面里是 5 号，要找的是 1 号
    said = asyncio.run(_run(world, lost_s=0.5, time_limit_s=10))
    assert "看不到绿色瓶子" in said[0]
    assert abs(world.x) < 1 and abs(world.y) < 1  # 看不到就不动


def test_falls_back_to_vision_seeker_without_markers():
    class Fallback:
        async def run(self, target, goal, fire):
            self.called = (target, goal)
            return "fallback"

    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()

        class Blank:
            def frame(self):
                import numpy as np

                return np.full((480, 640, 3), 128, np.uint8)  # 画面里没有任何标记

        fb = Fallback()
        MarkerSeeker(tank, Blank(), fallback=fb)("杯子", "approach", False)
        while tank.status()["mission"]:
            await asyncio.sleep(0.05)
        await tank.close()
        assert fb.called == ("杯子", "approach")

    asyncio.run(main())


def test_stop_cancels_and_car_halts():
    async def main():
        world = World(car=(0, 0, 0.3), target=(200, 30), cam=(-40, -60, 70))
        world.follow = True
        tank = TankExecutor(SimBackend(world), max_speed=100)
        await tank.start()
        MarkerSeeker(tank, SimCamera(world), labels={1: "瓶子"})("瓶子", "approach", False)
        await asyncio.sleep(0.6)
        await tank.stop()
        await asyncio.sleep(0.05)
        x = world.x
        await asyncio.sleep(0.5)
        world.update()
        assert tank.status()["mission"] is None and abs(world.x - x) < 0.5
        await tank.close()

    asyncio.run(main())


def test_steer_only_from_mission():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        with pytest.raises(RuntimeError):
            await tank.steer(50, 50)
        await tank.close()

    asyncio.run(main())
