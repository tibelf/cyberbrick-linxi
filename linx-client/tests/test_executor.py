import asyncio

import pytest

from linx_client.tank import MockBackend, TankExecutor


async def drain(tank):
    for _ in range(500):
        s = tank.status()
        if not s["current"] and not s["queued"]:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("executor 没有执行完")


def run(coro):
    return asyncio.run(coro)


def test_move_clamps_speed_and_halts():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, max_speed=40)
        await tank.start()
        tank.move("forward", 50, 100)
        await drain(tank)
        await tank.close()
        drive = [c for c in be.calls if c[0] == "drive"][0]
        assert drive[1:3] == (40, 40)
        assert drive[3] > 50  # 设备端 failsafe 比动作时长略长
        idx = be.calls.index(drive)
        assert be.calls[idx + 1][0] == "halt"

    run(main())


def test_stop_cancels_running_and_queued():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be)
        await tank.start()
        tank.move("forward", 3000, 50)
        tank.move("backward", 3000, 50)
        await asyncio.sleep(0.05)
        t0 = asyncio.get_running_loop().time()
        await tank.stop()
        await drain(tank)
        assert asyncio.get_running_loop().time() - t0 < 0.5
        assert len([c for c in be.calls if c[0] == "drive"]) == 1
        await tank.close()

    run(main())


def test_fire_locked():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False), fire_enabled=False)
        with pytest.raises(PermissionError):
            tank.fire(1)

    run(main())


def test_fire_cooldown_and_max_shots():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, fire_pulse_ms=10, fire_cooldown_s=0.05, max_shots=2)
        await tank.start()
        tank.fire(5)
        await drain(tank)
        await tank.close()
        assert len([c for c in be.calls if c[0] == "fire"]) == 2
        assert tank.shots_fired == 2

    run(main())


def test_queue_limit():
    async def main():
        tank = TankExecutor(MockBackend(verbose=False))
        for _ in range(3):
            tank.move("forward", 5000, 50)
        with pytest.raises(ValueError):
            tank.move("forward", 1000, 50)

    run(main())


def test_turn_pivots_and_scales_by_angle_and_speed():
    from linx_client.tank.executor import MS_PER_90_AT_FULL

    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, max_speed=100)
        await tank.start()
        assert f"{MS_PER_90_AT_FULL}ms" in tank.turn("left", 90, 100)
        assert f"{MS_PER_90_AT_FULL}ms" in tank.turn("right", 45, 50)  # 角度减半、速度减半，时长不变
        await drain(tank)
        await tank.close()
        drives = [c for c in be.calls if c[0] == "drive"]
        assert drives[0][1:3] == (-100, 100)  # 左转：左履带后退、右履带前进
        assert drives[1][1:3] == (50, -50)

    run(main())


def test_personas_change_move_style():
    from linx_client.tank.executor import GRANDPA_SPEED

    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, max_speed=100)
        await tank.start()
        tank.set_persona("soldier")
        tank.move("forward", 100, 30)
        tank.set_persona("grandpa")
        tank.move("forward", 700, 100)
        tank.set_persona("drunk")
        tank.move("forward", 500, 80)
        await drain(tank)
        await tank.close()
        drives = [c[1:3] for c in be.calls if c[0] == "drive"]
        assert drives[0] == (100, 100)  # 特种兵无视“慢一点”，全速
        assert drives[1:4] == [(GRANDPA_SPEED, GRANDPA_SPEED)] * 3  # 老爷爷 700ms 拆成 300+300+100 走走停停
        assert drives[4:6] == [(80, 32), (32, 80)]  # 醉汉左右交替一快一慢
        assert tank.status()["persona"] == "醉汉"
        with pytest.raises(ValueError):
            tank.set_persona("pirate")

    run(main())


def test_emote_skips_when_busy_and_cancels_out():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, max_speed=100)
        await tank.start()
        tank.move("forward", 200, 80)
        assert tank.emote("happy") is None  # 正在执行指令：不插队
        await drain(tank)
        be.calls.clear()
        for kind in ("happy", "angry", "sad", "surprised", "thinking"):
            assert tank.emote(kind)
            await drain(tank)
        await tank.close()
        # 每种情绪动作都成对抵消：履带净位移为 0，炮管净转动为 0
        net_drive = sum((c[1] + c[2]) * (c[3] - 300) for c in be.calls if c[0] == "drive")
        net_barrel = sum(c[1] * (c[2] - 300) for c in be.calls if c[0] == "turret")
        assert net_drive == 0 and net_barrel == 0

    run(main())


def test_mood_rate_limits(monkeypatch):
    from linx_client.tank import mood as mood_mod

    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, max_speed=100)
        await tank.start()
        clock = [100.0]
        monkeypatch.setattr(mood_mod, "_now", lambda: clock[0])
        m = mood_mod.Mood(tank)
        assert m.on_emotion("surprised") == "surprised"
        clock[0] += 1
        assert m.on_emotion("happy") is None  # 冷却中
        clock[0] += 5
        assert m.on_emotion("shocked") is None  # 同一组情绪 20s 内不重复
        assert m.on_emotion("laughing") == "happy"
        assert m.on_emotion("unknown-emotion") is None
        await drain(tank)
        await asyncio.sleep(0)
        await tank.close()
        assert ("led", 40, 30, 0) in be.calls and ("led", 0, 40, 0) in be.calls

    run(main())


def test_manual_takes_over_and_blocks_emotes():
    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, max_speed=70)
        await tank.start()
        tank.move("forward", 3000, 80)
        await asyncio.sleep(0.05)
        await tank.manual(100, 20, -1)  # 手动优先：语音排的前进被打断
        assert not tank.busy and tank.manual_active
        assert ("drive", 70, 20, 400) in be.calls and ("turret", -45, 400) in be.calls
        assert tank.emote("happy") is None  # 手动期间不插情绪动作
        be.calls.clear()
        await tank.manual(0, 0, 0)
        assert be.calls == [("halt",)]
        await tank.close()

    run(main())


def test_mood_skips_wiggle_when_command_follows(monkeypatch):
    """平台先发情绪、紧接着发“前进”：不能先原地扭一下再走。"""
    from linx_client.tank import mood as mood_mod

    monkeypatch.setattr(mood_mod, "EMOTE_DELAY_S", 0.1)

    async def main():
        be = MockBackend(verbose=False)
        tank = TankExecutor(be, max_speed=100)
        await tank.start()
        m = mood_mod.Mood(tank)
        assert m.on_emotion("happy") == "happy"
        tank.move("forward", 200, 80)  # 指令紧跟着到
        await asyncio.sleep(0.3)
        await drain(tank)
        drives = [c[1:3] for c in be.calls if c[0] == "drive"]
        assert drives == [(80, 80)]  # 只有直行，没有原地扭
        assert ("led", 0, 40, 0) in be.calls  # 车灯照样变色

        m2 = mood_mod.Mood(tank)  # 没有指令跟着：照常扭一扭
        be.calls.clear()
        m2.on_emotion("happy")
        await asyncio.sleep(0.2)
        await drain(tank)
        await tank.close()
        assert any(c[0] == "drive" and c[1] == -c[2] for c in be.calls)

    run(main())
