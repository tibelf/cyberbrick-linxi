"""闭环寻的：用写死的“视觉模型回答”驱动，检查换算出的动作、结束条件、急停和发射锁定。"""

import asyncio

from linx_client.seek import (NEAR_CARS, RAM_MS, Box, Seeker, gap_in_cars, parse_boxes, parse_front, parse_positions,
                              parse_yes_no, turn_needed)
from linx_client.tank import MockBackend, TankExecutor


def test_parse_positions():
    assert parse_positions("坦克=20,60；目标=75,55") == {"car": (20.0, 60.0), "target": (75.0, 55.0)}
    assert parse_positions("坦克=(20, 60)，目标=（75，55）") == {"car": (20.0, 60.0), "target": (75.0, 55.0)}
    assert parse_positions("坦克=20,60；目标=无") == {"car": (20.0, 60.0), "target": None}
    assert parse_positions("找到=是；方向=4；距离=2") is None
    assert parse_positions("坦克在左边") is None


def test_parse_front():
    assert parse_front("找到=是；偏角=-20；近=否") == {"found": True, "offset": -20, "near": False}
    assert parse_front("找到=是；偏角=+5；近=是") == {"found": True, "offset": 5, "near": True}
    assert parse_front("找到=否") == {"found": False}


def test_turn_needed():
    # 画面坐标 y 向下：车头朝右，目标在画面上方 → 左转 90°；在下方 → 右转 90°
    assert round(turn_needed((1, 0), (0, -1))) == 90
    assert round(turn_needed((1, 0), (0, 1))) == -90
    assert abs(round(turn_needed((1, 0), (-1, 0)))) == 180
    assert round(turn_needed((1, 0), (5, 0))) == 0


def _scripted(answers):
    asked = []

    async def ask(q):
        asked.append(q)
        return answers.pop(0) if answers else "找到=否"

    return ask, asked


async def _run_mission(seeker, tank, *args):
    said = []
    seeker.say = lambda text: _append(said, text)
    seeker(*args)
    while tank.status()["mission"]:
        await asyncio.sleep(0.02)
    return said


async def _append(lst, x):
    lst.append(x)


def _calls(be, kind):
    return [c for c in be.calls if c[0] == kind]


def test_parse_boxes_and_gap():
    r = parse_boxes("坦克=45,62,53,72；目标=60,55,65,80")
    assert r["car"] == Box(45, 62, 53, 72) and r["target"].foot == (62.5, 80)
    assert parse_boxes("坦克=450,620,530,720；目标=无")["car"] == Box(45, 62, 53, 72)  # 0-1000 坐标
    assert parse_boxes("坦克=49,68；目标=57,72")["car"].center == (49, 68)  # 只给中心点也能用
    assert parse_boxes("看不清") is None
    # 2026-10-25 截图里的情形：中心只差 9%，但隔着一个多车身 → 不算到
    assert gap_in_cars(Box(45, 64, 53, 72), Box(55, 62, 60, 80)) > NEAR_CARS
    assert gap_in_cars(Box(45, 64, 53, 72), Box(53, 62, 58, 74)) < NEAR_CARS


def test_parse_yes_no():
    assert parse_yes_no("是") and parse_yes_no("是的，已经碰到了")
    assert parse_yes_no("否") is False and parse_yes_no("还没有") is False
    assert parse_yes_no("可能吧") is None


def _box(cx, cy, w=8, h=8):
    return f"{cx - w / 2},{cy - h / 2},{cx + w / 2},{cy + h / 2}"


def test_external_straight_approach_confirmed_by_vision():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        ask, asked = _scripted([
            f"坦克={_box(20, 60)}；目标={_box(80, 60)}", f"坦克={_box(25, 60)}；目标={_box(80, 60)}",
            f"坦克={_box(72, 60)}；目标={_box(80, 60)}", "是"])
        said = await _run_mission(Seeker(tank, ask, probe_ms=50), tank, "饮料瓶", "approach", False)
        await tank.close()
        drives = _calls(be, "drive")
        assert all(d[1] == d[2] > 0 for d in drives)  # 车头已经朝着目标：只前进，不转向
        assert len(drives) == 2 and len(asked) == 4 and "饮料瓶" in asked[0] and "紧挨着" in asked[3]
        assert said[0].startswith("已走到饮料瓶") and not _calls(be, "fire")

    asyncio.run(main())


def test_does_not_stop_when_vision_says_not_there_yet():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        ask, _ = _scripted([
            f"坦克={_box(20, 60)}；目标={_box(30, 60)}", "否",  # 看着挨着，模型说还没碰到 → 继续走
            f"坦克={_box(23, 60)}；目标={_box(30, 60)}", "否",
            f"坦克={_box(26, 60)}；目标={_box(30, 60)}", "是"])
        said = await _run_mission(Seeker(tank, ask, probe_ms=50), tank, "瓶子", "approach", False)
        await tank.close()
        assert len(_calls(be, "drive")) >= 2 and said[0].startswith("已走到瓶子")

    asyncio.run(main())


def test_hit_rams_until_knocked_over():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        ask, _ = _scripted([f"坦克={_box(50, 60)}；目标={_box(57, 60)}", "是", "否",  # 撞了一次没倒
                            f"坦克={_box(50, 60)}；目标={_box(57, 60)}", "是", "是"])
        said = await _run_mission(Seeker(tank, ask, probe_ms=50), tank, "瓶子", "hit", False)
        await tank.close()
        rams = [d for d in _calls(be, "drive") if d[1] == d[2] > 0 and d[3] == RAM_MS + 300]  # 速度会被限速裁剪，按时长认
        assert len(rams) == 2 and said[0] == "已把瓶子撞倒了"

    asyncio.run(main())


def test_external_turns_by_measured_heading():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        # 目标在画面上方，试探前进后发现车头朝右 → 应左转约 90°
        ask, _ = _scripted([f"坦克={_box(50, 80)}；目标={_box(50, 20)}", f"坦克={_box(55, 80)}；目标={_box(50, 20)}",
                            f"坦克={_box(52, 26)}；目标={_box(50, 20)}", "是"])
        said = await _run_mission(Seeker(tank, ask, probe_ms=50), tank, "饮料瓶", "approach", False)
        await tank.close()
        drives = _calls(be, "drive")
        assert drives[0][1] == drives[0][2] > 0  # 试探前进
        assert drives[1][1] < 0 < drives[1][2]  # 左转（超过 90° 会拆成几段）
        after_turn = next(d for d in drives[1:] if not d[1] < 0 < d[2])
        assert after_turn[1] == after_turn[2] > 0  # 转完再前进
        assert said[0].startswith("已走到")

    asyncio.run(main())


def test_external_unsticks_then_gives_up_when_car_does_not_move():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        ask, _ = _scripted([f"坦克={_box(20, 60)}；目标={_box(80, 60)}"] * 20)
        said = await _run_mission(Seeker(tank, ask, probe_ms=50), tank, "饮料瓶", "approach", False)
        await tank.close()
        assert any(d[1] == d[2] < 0 for d in _calls(be, "drive"))  # 先试过后退脱困
        assert "卡住" in said[0]

    asyncio.run(main())


def test_external_retries_when_target_missing_then_reports():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        ask, _ = _scripted(["坦克=无；目标=80,60"] * 3)
        said = await _run_mission(Seeker(tank, ask), tank, "饮料瓶", "approach", False)
        ask2, asked2 = _scripted(["坦克=20,60；目标=无", "坦克=20,60；目标=无", "坦克=20,60；目标=无"])
        said += await _run_mission(Seeker(tank, ask2), tank, "饮料瓶", "approach", False)
        await tank.close()
        assert "找不到小坦克" in said[0] and "没有看到饮料瓶" in said[1] and len(asked2) == 3

    asyncio.run(main())


def test_time_limit():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        x = [10.0]

        async def ask(q):
            x[0] += 3  # 一直在动，但目标永远在很远的地方
            return f"坦克={_box(x[0], 60)}；目标={_box(x[0] + 60, 60)}"

        seeker = Seeker(tank, ask, probe_ms=20, time_limit_s=0.5)
        seeker.say = lambda t: _append(said, t)
        said = []
        seeker("门", "approach", False)
        while tank.status()["mission"]:
            await asyncio.sleep(0.02)
        await tank.close()
        assert "还没走到门" in said[0]

    asyncio.run(main())


def test_front_scans_when_not_found_then_aims_and_fires():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, fire_pulse_ms=10, fire_cooldown_s=0)
        await tank.start()
        ask, _ = _scripted(["找到=否", "找到=是；偏角=-20；近=否", "找到=是；偏角=3；近=否"])
        seeker = Seeker(tank, ask, view="front")
        said = await _run_mission(seeker, tank, "盒子", "aim", True)
        await tank.close()
        drives = _calls(be, "drive")
        assert drives[0][1] > 0 > drives[0][2]  # 没看到 → 右转扫描
        assert drives[1][1] < 0 < drives[1][2]  # 偏左 20° → 左转
        assert len(_calls(be, "fire")) == 1
        assert "已对准盒子" in said[0] and "已开火" in said[0]

    asyncio.run(main())


def test_fire_lock_is_reported_not_bypassed():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, fire_enabled=False)
        await tank.start()
        ask, _ = _scripted(["找到=是；偏角=0；近=是"])
        said = await _run_mission(Seeker(tank, ask, view="front"), tank, "盒子", "aim", True)
        await tank.close()
        assert not _calls(be, "fire") and "没有开火" in said[0]

    asyncio.run(main())


def test_stop_cancels_mission():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()

        async def slow_ask(q):
            await asyncio.sleep(0.05)
            return "坦克=10,50；目标=90,50"  # 永远离得很远

        seeker = Seeker(tank, slow_ask, view="overhead", probe_ms=2000)
        seeker("门", "approach", False)
        await asyncio.sleep(0.3)
        assert tank.status()["mission"]
        await tank.stop()
        await asyncio.sleep(0.05)
        assert tank.status()["mission"] is None and not tank.status()["current"]
        n = len(_calls(be, "drive"))
        await asyncio.sleep(0.3)
        assert len(_calls(be, "drive")) == n  # 停了以后不再下发动作
        await tank.close()

    asyncio.run(main())


def test_new_mission_replaces_old_and_bad_answers_abort():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()

        async def never(q):
            await asyncio.sleep(10)
            return ""

        Seeker(tank, never)("杯子", "aim", False)
        ask, _ = _scripted(["我也不知道", "看不清"])
        seeker = Seeker(tank, ask, view="overhead")
        said = []
        seeker.say = lambda t: _append(said, t)
        assert "已停止之前的「对准杯子」" in seeker("瓶子", "aim", False)
        while tank.status()["mission"]:
            await asyncio.sleep(0.02)
        await tank.close()
        assert "看不懂" in said[0]

    asyncio.run(main())


def test_no_emote_during_mission():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()

        async def slow_ask(q):
            await asyncio.sleep(0.2)
            return "找到=否"

        Seeker(tank, slow_ask, view="overhead")("杯子", "aim", False)
        await asyncio.sleep(0.05)
        assert tank.emote("happy") is None  # 任务两步之间执行器是空的，也不能插情绪小动作
        await tank.stop()
        await tank.close()

    asyncio.run(main())


def test_mission_error_is_reported_not_silent():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        errors = []
        tank.on_error = errors.append
        await tank.start()

        async def broken(q):
            raise RuntimeError("平台没有下发视觉接口")

        Seeker(tank, broken)("绿色瓶子", "approach", False)
        while tank.status()["mission"]:
            await asyncio.sleep(0.02)
        await tank.close()
        assert errors and "走到绿色瓶子" in errors[0] and "视觉接口" in errors[0]
        assert tank.status()["last_error"]

    asyncio.run(main())
