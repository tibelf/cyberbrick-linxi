"""闭环寻的：用写死的“视觉模型回答”驱动，检查换算出的动作、结束条件、急停和发射锁定。"""

import asyncio

from linx_client.seek import Seeker, parse_front, parse_positions, turn_needed
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


def test_external_straight_approach():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        ask, asked = _scripted(["坦克=20,60；目标=80,60", "坦克=25,60；目标=80,60", "坦克=72,60；目标=80,60"])
        said = await _run_mission(Seeker(tank, ask, probe_ms=50), tank, "饮料瓶", "approach", False)
        await tank.close()
        drives = _calls(be, "drive")
        assert all(d[1] == d[2] > 0 for d in drives)  # 车头已经朝着目标：只前进，不转向
        assert len(drives) == 2 and len(asked) == 3 and "饮料瓶" in asked[0]
        assert said[0].startswith("已走到饮料瓶") and not _calls(be, "fire")

    asyncio.run(main())


def test_external_turns_by_measured_heading():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        # 目标在画面上方，试探前进后发现车头朝右 → 应左转约 90°
        ask, _ = _scripted(["坦克=50,80；目标=50,20", "坦克=55,80；目标=50,20", "坦克=52,26；目标=50,20"])
        said = await _run_mission(Seeker(tank, ask, probe_ms=50), tank, "饮料瓶", "approach", False)
        await tank.close()
        drives = _calls(be, "drive")
        assert drives[0][1] == drives[0][2] > 0  # 试探前进
        assert drives[1][1] < 0 < drives[1][2]  # 左转
        assert drives[2][1] == drives[2][2] > 0  # 再前进
        assert said[0].startswith("已走到")

    asyncio.run(main())


def test_external_gives_up_when_car_does_not_move():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        ask, _ = _scripted(["坦克=20,60；目标=80,60"] * 4)
        said = await _run_mission(Seeker(tank, ask, probe_ms=50), tank, "饮料瓶", "approach", False)
        await tank.close()
        assert "看不出小坦克往哪个方向走" in said[0]

    asyncio.run(main())


def test_external_reports_missing_car_or_target():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        ask, _ = _scripted(["坦克=无；目标=80,60"])
        said = await _run_mission(Seeker(tank, ask), tank, "饮料瓶", "approach", False)
        ask2, _ = _scripted(["坦克=20,60；目标=无"])
        said += await _run_mission(Seeker(tank, ask2), tank, "饮料瓶", "approach", False)
        await tank.close()
        assert "找不到小坦克" in said[0] and "没有看到饮料瓶" in said[1]

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
