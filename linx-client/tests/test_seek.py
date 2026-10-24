"""闭环寻的：用写死的“视觉模型回答”驱动，检查换算出的动作、结束条件、急停和发射锁定。"""

import asyncio

from linx_client.seek import Seeker, clock_to_turn, parse_front, parse_overhead
from linx_client.tank import MockBackend, TankExecutor


def test_parse_overhead():
    assert parse_overhead("找到=是；方向=3；距离=2") == {"found": True, "clock": 3, "dist": 2.0}
    assert parse_overhead("找到：是，方向：12，距离：0.5 个车身") == {"found": True, "clock": 12, "dist": 0.5}
    assert parse_overhead("找到=否") == {"found": False}
    assert parse_overhead("杯子在右边") is None
    assert parse_overhead("找到=是；方向=13；距离=1") is None


def test_parse_front():
    assert parse_front("找到=是；偏角=-20；近=否") == {"found": True, "offset": -20, "near": False}
    assert parse_front("找到=是；偏角=+5；近=是") == {"found": True, "offset": 5, "near": True}
    assert parse_front("找到=否") == {"found": False}


def test_clock_to_turn():
    assert clock_to_turn(12) is None
    assert clock_to_turn(3) == ("right", 90)
    assert clock_to_turn(6) == ("right", 180)
    assert clock_to_turn(9) == ("left", 90)
    assert clock_to_turn(11) == ("left", 30)


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


def test_overhead_turn_then_approach():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        ask, asked = _scripted(["找到=是；方向=3；距离=2", "找到=是；方向=12；距离=2", "找到=是；方向=12；距离=1"])
        seeker = Seeker(tank, ask, view="overhead", car_len_ms=50)
        said = await _run_mission(seeker, tank, "红色杯子", "approach", False)
        await tank.close()
        drives = _calls(be, "drive")
        assert drives[0][1] > 0 > drives[0][2]  # 3 点钟 → 原地右转
        assert drives[1][1] == drives[1][2] > 0  # 12 点钟 → 直走
        assert len(asked) == 3 and "红色杯子" in asked[0]
        assert said and said[0].startswith("已走到红色杯子") and not _calls(be, "fire")

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
            return "找到=是；方向=12；距离=5"  # 永远在前面很远

        seeker = Seeker(tank, slow_ask, view="overhead", car_len_ms=2000)
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


def test_second_mission_rejected_and_bad_answers_abort():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        ask, _ = _scripted(["我也不知道", "看不清"])
        seeker = Seeker(tank, ask, view="overhead")
        said = []
        seeker.say = lambda t: _append(said, t)
        seeker("杯子", "aim", False)
        try:
            seeker("杯子", "aim", False)
        except ValueError as e:
            assert "正在执行" in str(e)
        else:
            raise AssertionError("同时只能有一个任务")
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
