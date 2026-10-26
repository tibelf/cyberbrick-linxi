"""一张图规划、闭眼走完：视觉模型的回答 → 厘米地图 → 绕障路线 → 转向/前进序列，执行时不再拍照。"""

import asyncio
import math

import pytest

from linx_client.blind import (BLIND_SPEED, CAR_LEN_CM, CAR_WID_CM, EXTRA_MARGIN_CM, BlindMap, BlindNavigator,
                               compile_route, parse_answer)
from linx_client.floormap import CAR_RADIUS_CM, MARGIN_CM
from linx_client.mcp import McpServer
from linx_client.tank import MockBackend, TankExecutor
from linx_client.tools import register_blind_tools

W, H, PX_PER_CM = 1000, 800, 4.0  # 照片 1000×800 像素，地面 250×200 cm


def pct_box(x_cm, y_cm, w_cm, h_cm):
    """地面上以 (x, y) 为中心、宽 w 高 h 的东西 → 百分比外框（图像 y 向下）。"""
    u1, u2 = (x_cm - w_cm / 2) * PX_PER_CM, (x_cm + w_cm / 2) * PX_PER_CM
    v1, v2 = (H / PX_PER_CM - y_cm - h_cm / 2) * PX_PER_CM, (H / PX_PER_CM - y_cm + h_cm / 2) * PX_PER_CM
    return f"{u1 / W * 100:.1f},{v1 / H * 100:.1f},{u2 / W * 100:.1f},{v2 / H * 100:.1f}"


def pct_pt(x_cm, y_cm):
    return f"{x_cm * PX_PER_CM / W * 100:.1f},{(H / PX_PER_CM - y_cm) * PX_PER_CM / H * 100:.1f}"


# 车在 (125, 30) 车头朝上；纸箱挡在正前方 (125, 100)，红瓶子在它后面 (125, 170)
ANSWER = (f"坦克={pct_box(125, 30, CAR_WID_CM, CAR_LEN_CM)}；炮管尖={pct_pt(125, 42)}；"
          f"纸箱={pct_box(125, 100, 40, 30)}；红色瓶子={pct_box(125, 170, 8, 8)}")


def test_parse_answer_formats():
    car, tip, others = parse_answer("坦克 = 400，600，520，750 ; 炮管尖：460,550；纸箱=100,200,300,400")
    assert car == (40, 60, 52, 75) and tip == (46, 55)  # 0-1000 自动换成百分比
    assert others == [("纸箱", (10, 20, 30, 40))]
    assert parse_answer("无")[0] is None


def test_scale_and_pose_from_car_box():
    m = BlindMap(W, H, ANSWER)
    assert m.px_per_cm == pytest.approx(PX_PER_CM, rel=0.02)
    x, y, h = m.pose
    assert (x, y) == pytest.approx((125, 30), abs=1) and h == pytest.approx(math.pi / 2)
    assert not m.tip_disagrees
    assert m.width_cm == pytest.approx(250, rel=0.02)


def test_warns_when_barrel_contradicts_facing_up():
    answer = ANSWER.replace(f"炮管尖={pct_pt(125, 42)}", f"炮管尖={pct_pt(140, 30)}")  # 炮管朝右
    assert BlindMap(W, H, answer, facing="up").tip_disagrees
    m = BlindMap(W, H, answer)  # 默认按炮管尖算朝向
    assert m.pose[2] == pytest.approx(0, abs=0.05) and m.heading_from_tip
    assert m.heading_words() == "画面右边"


def test_route_goes_around_the_box_with_margin():
    m = BlindMap(W, H, ANSWER)
    fm = m.floor_map(EXTRA_MARGIN_CM)
    path = fm.plan(m.pose[:2], (125, 170))
    assert path and len(path) >= 2  # 不能直线穿过纸箱
    clear = CAR_RADIUS_CM + MARGIN_CM  # 至少离纸箱这么远（再加的闭眼余量是额外保险）
    pts = [m.pose[:2]] + path
    for a, b in zip(pts, pts[1:]):
        for i in range(51):
            px, py = a[0] + (b[0] - a[0]) * i / 50, a[1] + (b[1] - a[1]) * i / 50
            dx = max(abs(px - 125) - 20, 0)
            dy = max(abs(py - 100) - 15, 0)
            assert math.hypot(dx, dy) >= clear - 2.5  # 栅格 2cm 的离散误差


def test_compile_route_turn_signs_and_durations():
    steps = compile_route((0, 0, math.pi / 2), [(-30, 30), (-30, 90)], cm_per_s=30)
    assert steps[0] == ("turn", pytest.approx(45))  # 车头朝上，目标在左前方 → 左转（正数）
    assert steps[1] == ("forward", pytest.approx(math.hypot(30, 30) / 30 * 1000))
    assert steps[2] == ("turn", pytest.approx(-45))  # 再朝上 → 右转
    assert steps[3] == ("forward", pytest.approx(2000))
    long = compile_route((0, 0, 0), [(300, 0)], cm_per_s=30)  # 10 秒拆成不超过 5 秒的段
    assert [k for k, _ in long] == ["forward", "forward"]


class FakePhoto:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    async def ask(self, question, frame=None):
        self.calls += 1
        assert "坦克=" in question and "百分比" in question
        return self.answer


def test_blind_goto_runs_without_more_photos():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, max_speed=100, fire_pulse_ms=10)
        await tank.start()
        tank.set_persona("grandpa")
        photo, said = FakePhoto(ANSWER), []

        async def say(text):
            said.append(text)

        nav = BlindNavigator(tank, photo.ask, say=say, cm_per_s=300)  # 很快，测试别等太久
        nav.map = None
        # 尺寸由 frame 给出；这里没有摄像头帧，就按回答对应的照片尺寸构造
        nav.map = BlindMap(W, H, ANSWER)
        mcp = McpServer()
        register_blind_tools(mcp, nav)
        r = await mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "self.blind.goto", "arguments": {"target": "瓶子"}}})
        assert r["result"]["isError"] is False and "不再看摄像头" in r["result"]["content"][0]["text"]
        while tank.status()["mission"]:
            await asyncio.sleep(0.02)
        await tank.close()
        assert photo.calls == 0  # 执行时一张照片都没拍
        drives = [c for c in be.calls if c[0] == "drive"]
        assert any(c[1] == -c[2] for c in drives)  # 有原地转
        assert any(c[1] == c[2] == BLIND_SPEED for c in drives)  # 直行按固定速度（不受老爷爷模式影响）
        assert tank.persona == "grandpa"  # 走完恢复人格
        x, y, _ = nav.map.pose
        assert math.dist((x, y), (125, 170)) < 45 and nav.map.legs == 1  # 位置估计更新到瓶子旁边
        assert said and "估计" in said[0]

    asyncio.run(main())


def test_build_via_tool_and_missing_target():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        photo = FakePhoto(ANSWER.replace("坦克=", "坦克=").replace(";", "；"))
        nav = BlindNavigator(tank, photo.ask)
        mcp = McpServer()
        register_blind_tools(mcp, nav)
        call = lambda name, args: mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",  # noqa: E731
                                              "params": {"name": name, "arguments": args}})
        r = await call("self.blind.goto", {"target": "瓶子"})
        assert r["result"]["isError"] and "拍一张" in r["result"]["content"][0]["text"]
        nav.frame = None
        r = await call("self.blind.map", {})
        text = r["result"]["content"][0]["text"]
        assert r["result"]["isError"] is False and "纸箱" in text and photo.calls == 1
        r = await call("self.blind.goto", {"target": "绿色椅子"})
        assert r["result"]["isError"] and "target" in r["result"]["content"][0]["text"]
        await tank.close()

    asyncio.run(main())


def test_narrow_gap_shrinks_margin_and_says_so():
    # 两堵墙之间只留 50cm 的口子：带上额外余量 15cm（单边 30cm）过不去，收窄到 8cm（单边 23cm）能过
    answer = (f"坦克={pct_box(125, 30, CAR_WID_CM, CAR_LEN_CM)}；"
              f"左墙={pct_box(50, 100, 100, 10)}；右墙={pct_box(200, 100, 100, 10)}；红色瓶子={pct_box(125, 170, 8, 8)}")

    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        nav = BlindNavigator(tank, FakePhoto(answer).ask, cm_per_s=300)
        nav.map = BlindMap(W, H, answer)
        text = nav.goto("红色瓶子")
        assert "只多留了" in text
        await tank.stop()
        await tank.close()

    asyncio.run(main())


def _nav(answer, cm_per_s=30):
    nav = BlindNavigator(None, None, cm_per_s=cm_per_s)
    nav.map = BlindMap(W, H, answer)
    return nav


def test_few_turns_near_side_and_faces_target():
    from linx_client.blind import route_end

    nav = _nav(ANSWER)
    steps, end, path, center, extra, _ = nav.plan("瓶子")
    assert sum(k == "turn" for k, _ in steps) <= 3  # 拐弯少：每拐一次都多一份闭眼误差
    x, y, h = end
    assert y < 170  # 停在瓶子靠车的这一侧，不绕到背后
    assert abs(math.degrees(math.atan2(170 - y, 125 - x) - h)) < 1  # 到了正对目标，可以直接开火
    assert math.dist((x, y), (125, 170)) == pytest.approx(4 + CAR_LEN_CM / 2 + 8, abs=1)  # 开到跟前才停


def test_car_already_facing_target_goes_straight_and_close():
    """用户实测：车头正对左边的垃圾桶，却被当成朝上 → 先左转 90°；而且停在离桶半米外。"""
    answer = (f"坦克={pct_box(170, 100, CAR_LEN_CM, CAR_WID_CM)}；炮管尖={pct_pt(158, 100)}；"
              f"垃圾桶={pct_box(80, 100, 40, 40)}")
    nav = _nav(answer)
    assert nav.map.heading_words() == "画面左边"
    steps, end, *_ = nav.plan("垃圾桶")
    assert [k for k, _ in steps] == ["forward"]  # 直接往前开，不转弯
    gap = 170 - 80 - 20 - CAR_LEN_CM / 2 - 8  # 车中心到“桶边缘前 8cm 处”还要走的距离
    assert steps[0][1] == pytest.approx(gap / 30 * 1000, rel=0.05)
    assert end[0] == pytest.approx(80 + 20 + CAR_LEN_CM / 2 + 8, abs=1.5)
