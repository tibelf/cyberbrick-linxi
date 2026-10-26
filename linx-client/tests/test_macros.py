"""动作宏（语音编程）：解析步骤、一句话定义、边做边学、执行（能急停、受发射锁定约束）、存盘。"""

import asyncio
import json

import pytest

from linx_client.macros import BUILTIN, Macros, parse_step, split_steps
from linx_client.mcp import McpServer
from linx_client.tank import MockBackend, TankExecutor
from linx_client.tools import register_macro_tools, register_tank_tools


def test_parse_step():
    assert parse_step("前进 2000") == ("move", "forward", 2000, 80)
    assert parse_step("前进2秒 速度 100") == ("move", "forward", 2000, 100)
    assert parse_step("后退 1.5s") == ("move", "backward", 1500, 80)
    assert parse_step("左转 90度") == ("turn", "left", 90)
    assert parse_step("右转") == ("turn", "right", 90)
    assert parse_step("炮管抬 300") == ("barrel", "up", 300)
    assert parse_step("压炮管") == ("barrel", "down", 300)
    assert parse_step("开火 2发") == ("fire", 2)
    assert parse_step("跳舞") == ("dance",)
    assert parse_step("等待 500毫秒") == ("wait", 500)
    assert parse_step("情绪 开心") == ("emote", "happy")
    assert parse_step("执行「巡逻」") == ("macro", "巡逻")
    assert parse_step("飞起来") is None
    assert split_steps("前进 2000；左转 90，然后开火 1") == ["前进 2000", "左转 90", "开火 1"]


def _drives(be):
    return [c for c in be.calls if c[0] == "drive"]


def _mk(tmp_path, **kw):
    tank = TankExecutor(MockBackend(verbose=False), fire_pulse_ms=10, fire_cooldown_s=0, **kw)
    return tank, Macros(tank, tmp_path / ".macros.json")


async def _wait_mission(tank):
    while tank.status()["mission"]:
        await asyncio.sleep(0.02)


def test_builtin_macros_until_user_saves(tmp_path):
    _, m = _mk(tmp_path)
    assert set(m.macros) == set(BUILTIN)


def test_save_run_and_persist(tmp_path):
    async def main():
        tank, m = _mk(tmp_path)
        await tank.start()
        msg = m.save("绕场", "前进 300；左转 90", repeat=3)
        assert "记住了「绕场」" in msg and "重复 3 次" in msg
        assert "开始：动作「绕场」" in m.run("绕场")
        await _wait_mission(tank)
        drives = _drives(tank.backend)
        assert len(drives) == 6  # 前进 + 左转，3 遍
        assert drives[0][1] == drives[0][2] > 0 and drives[1][1] < 0 < drives[1][2]
        await tank.close()
        saved = json.loads((tmp_path / ".macros.json").read_text(encoding="utf-8"))
        assert saved["绕场"] == {"steps": ["前进 300", "左转 90"], "repeat": 3}
        assert Macros(tank, tmp_path / ".macros.json").find("那个绕场") == "绕场"  # 重启后还在，说法模糊也找得到

    asyncio.run(main())


def test_bad_steps_are_rejected_with_hint(tmp_path):
    _, m = _mk(tmp_path)
    with pytest.raises(ValueError, match="飞起来"):
        m.save("瞎搞", "前进 300；飞起来")
    with pytest.raises(ValueError, match="没有叫“不存在”"):
        m.save("套娃", "执行 不存在")
    m.save("甲", "前进 100")
    m.save("乙", "执行 甲")
    with pytest.raises(ValueError, match="太多层"):
        m.save("甲", "执行 乙")  # 甲 → 乙 → 甲 → …
    assert m.macros["甲"]["steps"] == ["前进 100"]  # 出错时不改坏原来的


def test_nested_macro_and_repeat_override(tmp_path):
    async def main():
        tank, m = _mk(tmp_path)
        await tank.start()
        m.save("扭", "左转 30；右转 30")
        m.save("表演", "执行 扭；前进 200")
        m.run("表演", repeat=2)
        await _wait_mission(tank)
        assert len(_drives(tank.backend)) == 6
        await tank.close()

    asyncio.run(main())


def test_learn_by_doing(tmp_path):
    async def main():
        tank, m = _mk(tmp_path)
        await tank.start()
        m.record_start()
        tank.move("forward", 500, 80)
        tank.turn("left", 45)
        tank.fire(1)
        tank.dance()
        tank.emote("happy")  # 情绪小动作是自动的，不算用户教的
        await tank.wait_idle()
        msg = m.record_stop("胜利舞")
        assert "记住了「胜利舞」" in msg and "开火" in msg
        assert m.macros["胜利舞"]["steps"] == ["前进 500", "左转 45", "开火 1", "跳舞"]
        assert tank.recorder is None
        await tank.close()

    asyncio.run(main())


def test_learning_does_not_record_the_macro_playing(tmp_path):
    async def main():
        tank, m = _mk(tmp_path)
        await tank.start()
        m.record_start()
        m.run("巡逻", repeat=1)  # 学动作时执行别的宏：宏自己下发的动作不该被记进来
        await _wait_mission(tank)
        assert tank.recorder == []
        assert "没存" in m.record_stop("")
        await tank.close()

    asyncio.run(main())


def test_stop_cancels_macro(tmp_path):
    async def main():
        tank, m = _mk(tmp_path)
        await tank.start()
        m.save("长", "前进 2000", repeat=5)
        m.run("长")
        await asyncio.sleep(0.2)
        await tank.stop()
        n = len(_drives(tank.backend))
        await asyncio.sleep(0.3)
        assert tank.status()["mission"] is None and len(_drives(tank.backend)) == n
        await tank.close()

    asyncio.run(main())


def test_fire_lock_still_applies(tmp_path):
    async def main():
        tank, m = _mk(tmp_path, fire_enabled=False)
        said = []

        async def say(t):
            said.append(t)

        m.say = say
        await tank.start()
        m.save("开炮", "前进 100；开火 1；后退 100")
        m.run("开炮")
        await _wait_mission(tank)
        assert not [c for c in tank.backend.calls if c[0] == "fire"]
        assert len(_drives(tank.backend)) == 1 and "锁定" in said[0]  # 开火被拒，后面的步骤不做
        await tank.close()

    asyncio.run(main())


def test_tools_via_mcp(tmp_path):
    async def main():
        tank, m = _mk(tmp_path)
        await tank.start()
        mcp = McpServer()
        register_tank_tools(mcp, tank)
        register_macro_tools(mcp, m)

        async def call(tool, **args):
            r = await mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": args}})
            return r["result"]

        r = await call("self.macro.save", name="转圈", steps="左转 360")
        assert not r.get("isError") and "转圈" in r["content"][0]["text"]
        assert "转圈" in (await call("self.macro.list"))["content"][0]["text"]
        assert (await call("self.macro.run", name="不会的动作")).get("isError")
        await call("self.macro.delete", name="转圈")
        assert m.find("转圈") is None
        await tank.close()

    asyncio.run(main())
