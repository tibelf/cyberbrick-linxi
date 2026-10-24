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
