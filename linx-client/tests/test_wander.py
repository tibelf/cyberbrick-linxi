"""自由闲逛：一直逛到 stop()；撞到/堵死会后退掉头；不出画面；绝不开火；断线暂停；看不清就停下。"""

import asyncio
import random

import pytest

from linx_client import wander
from linx_client.tank import MockBackend, TankExecutor
from linx_client.wander import Wanderer, parse_front_path, parse_surroundings, rotate


@pytest.fixture(autouse=True)
def no_flourish(monkeypatch):
    monkeypatch.setattr(wander, "FLOURISH", 0)  # 小动作是随机的，测试里关掉，单独测


def test_parse_surroundings():
    assert parse_surroundings("坦克=40,60；空地=上,左上") == {"car": (40.0, 60.0), "free": {"上", "左上"}}
    assert parse_surroundings("坦克=40,60；空地=无") == {"car": (40.0, 60.0), "free": set()}
    assert parse_surroundings("坦克=无；空地=无")["car"] is None
    assert parse_surroundings("前面是空的") is None


def test_parse_front_path():
    assert parse_front_path("前方=通；左=堵；右=通") == {"前方": True, "左": False, "右": True}
    assert parse_front_path("前方=堵") is None


def test_rotate_left_90():
    x, y = rotate((1, 0), 90)  # 车头朝右，左转 90° → 朝画面上方
    assert abs(x) < 1e-9 and abs(y + 1) < 1e-9


def _drives(be):
    return [c for c in be.calls if c[0] == "drive"]


async def _start(answers, view="overhead", paused=None, fire_enabled=True):
    be = MockBackend(verbose=False)
    tank = TankExecutor(be, fire_enabled=fire_enabled)
    await tank.start()
    said = []

    async def say(t):
        said.append(t)

    async def ask(q):
        return answers(q) if callable(answers) else (answers.pop(0) if answers else "看不清")

    w = Wanderer(tank, ask, say=say, view=view, paused=paused, rng=random.Random(1), seg_ms=(50, 60))
    w()
    return be, tank, said


def test_keeps_going_until_stop_and_never_fires():
    async def main():
        x = [10.0]

        def moving(q):  # 每次看都往右挪一点，四周都是空地
            x[0] = 10 + (x[0] + 7 - 10) % 70
            return f"坦克={x[0]:.0f},50；空地=上,下,左,右"

        be, tank, said = await _start(moving)
        await asyncio.sleep(2.5)
        assert tank.status()["mission"] == "自由闲逛"  # 没有步数上限，还在逛
        n = len(_drives(be))
        assert n >= 4
        await tank.stop()
        await asyncio.sleep(0.2)
        assert tank.status()["mission"] is None
        assert len(_drives(be)) == n  # 停了就不再动
        assert not [c for c in be.calls if c[0] == "fire"] and not said
        await tank.close()

    asyncio.run(main())


def test_backs_off_when_stuck():
    async def main():
        # 第一次看到后试探前进，第二次位置没变 → 撞到了：应该后退（两条履带都为负）
        be, tank, _ = await _start(["坦克=50,50；空地=上,右", "坦克=50,50；空地=上,右"])
        await asyncio.sleep(1.0)
        await tank.stop()
        await tank.close()
        drives = _drives(be)
        assert drives[0][1] == drives[0][2] > 0
        assert any(d[1] == d[2] < 0 for d in drives[1:])

    asyncio.run(main())


def test_does_not_drive_off_screen_edge():
    async def main():
        # 坦克贴着画面左边，唯一的空地又是“左” → 过滤掉后无路可走，后退掉头，而不是往外开
        be, tank, _ = await _start(["坦克=5,50；空地=左"])
        await asyncio.sleep(0.8)
        await tank.stop()
        await tank.close()
        assert _drives(be)[0][1] == _drives(be)[0][2] < 0

    asyncio.run(main())


def test_front_view_turns_to_open_side():
    async def main():
        be, tank, _ = await _start(["前方=堵；左=通；右=堵"], view="front")
        await asyncio.sleep(0.5)
        await tank.stop()
        await tank.close()
        d = _drives(be)[0]
        assert d[1] < 0 < d[2]  # 左转

    asyncio.run(main())


def test_stops_and_says_when_blind():
    async def main():
        be, tank, said = await _start([])  # 一直“看不清”
        for _ in range(60):
            if not tank.status()["mission"]:
                break
            await asyncio.sleep(0.1)
        await tank.close()
        assert tank.status()["mission"] is None and "看不清画面" in said[0]
        assert not _drives(be)

    asyncio.run(main())


def test_pauses_while_disconnected():
    async def main():
        down = [True]
        be, tank, _ = await _start(lambda q: "坦克=50,50；空地=上", paused=lambda: down[0])
        await asyncio.sleep(0.4)
        assert not _drives(be)  # 断线期间不动
        down[0] = False
        await asyncio.sleep(0.8)
        assert _drives(be)  # 重连后继续
        await tank.stop()
        await tank.close()

    asyncio.run(main())


def test_flourish_uses_emote_during_mission(monkeypatch):
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        await tank.start()
        w = Wanderer(tank, None, rng=random.Random(0))
        w.rng.choice = lambda seq: "happy"
        tank.start_mission("自由闲逛", lambda: w._flourish())
        await asyncio.sleep(0.05)
        assert "情绪" in (tank.status()["current"] or "")
        await tank.stop()
        await tank.close()

    asyncio.run(main())


def test_unexpected_error_does_not_end_wander():
    async def main():
        calls = [0]

        def flaky(q):
            calls[0] += 1
            if calls[0] == 1:
                raise OSError("网络抖了一下")  # 视觉接口偶发出错
            return f"坦克={10 + calls[0] * 5},50；空地=右"

        be, tank, said = await _start(flaky)
        await asyncio.sleep(1.5)
        assert tank.status()["mission"] == "自由闲逛" and _drives(be) and not said
        await tank.stop()
        await tank.close()

    asyncio.run(main())
