"""打靶挑战：开始 → 命中 → 全中/时间到/放弃，记分规则、指令计数、开火计数、排行榜存盘。"""

import asyncio

import pytest

from linx_client.game import TargetChallenge
from linx_client.mcp import McpServer
from linx_client.tank import MockBackend, TankExecutor
from linx_client.tools import register_game_tools


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _mk(tmp_path):
    tank = TankExecutor(MockBackend(verbose=False), fire_pulse_ms=10, fire_cooldown_s=0)
    clock = Clock()
    changes = []
    g = TargetChallenge(tank, tmp_path / ".leaderboard.json", on_change=changes.append, clock=clock)
    return tank, g, clock, changes


def test_full_round_scores_and_ranks(tmp_path):
    async def main():
        tank, g, clock, changes = _mk(tmp_path)
        await tank.start()
        assert "3 个目标" in g.start(3, 120, "小明")
        for name in ("self.tank.move", "self.tank.turn", "self.tank.fire", "self.get_device_status"):
            g.on_tool(name)  # 查状态不算指令
        tank.fire(2)
        await tank.wait_idle()
        clock.t += 20
        assert "1/3" in g.hit()
        clock.t += 10
        msg = g.hit(2)  # 一次打倒两个 → 全中
        assert "全部打中" in msg and "第 1 名" in msg
        # 命中 3×100 + 剩 90 秒×5 − 没中的子弹 0 − 指令 3×5
        assert g.last_result["score"] == 300 + 450 - 15 and g.last_result["shots"] == 2
        assert not g.running and changes[-1]["last"]["success"]
        await tank.close()
        assert TargetChallenge(tank, tmp_path / ".leaderboard.json").board[0]["player"] == "小明"  # 存盘了

    asyncio.run(main())


def test_misses_cost_points_and_leaderboard_sorted(tmp_path):
    async def main():
        tank, g, clock, _ = _mk(tmp_path)
        await tank.start()
        g.start(1, 60, "甲")
        tank.fire(3)
        await tank.wait_idle()
        clock.t += 30
        g.hit()  # 3 发打中 1 个：浪费 2 发
        assert g.last_result["score"] == 100 + 150 - 40
        g.start(1, 60, "乙")
        clock.t += 5
        g.hit()
        assert [r["player"] for r in g.board] == ["乙", "甲"]
        await tank.close()

    asyncio.run(main())


def test_time_up_is_announced_and_recorded(tmp_path):
    async def main():
        tank, g, clock, _ = _mk(tmp_path)
        said = []

        async def say(t):
            said.append(t)

        g.say = say
        g.start(3, 30)
        g.hit()
        clock.t += 31
        await asyncio.wait_for(g._timer, 3)
        assert not g.running and "时间到" in said[0] and "1/3" in said[0]
        assert g.board[0]["success"] is False and g.board[0]["score"] == 100

    asyncio.run(main())


def test_give_up_not_recorded_and_errors(tmp_path):
    _, g, _, _ = _mk(tmp_path)
    with pytest.raises(ValueError, match="还没开始"):
        g.hit()
    g.start()
    with pytest.raises(ValueError, match="已经在进行"):
        g.start()
    assert "不记分" in g.stop() and not g.board
    assert "没有在进行" in g.stop()


def test_commands_only_counted_during_game(tmp_path):
    _, g, _, _ = _mk(tmp_path)
    g.on_tool("self.tank.move")
    g.start()
    g.on_tool("self.tank.move")
    g.on_tool("self.macro.run")
    assert g.commands == 2


def test_game_tools_via_mcp(tmp_path):
    async def main():
        tank, g, _, _ = _mk(tmp_path)
        mcp = McpServer()
        register_game_tools(mcp, g)

        async def call(tool, **args):
            r = await mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": args}})
            return r["result"]

        r = await call("self.game.start", targets=2, seconds=60, player="小红")
        assert "小红" in r["content"][0]["text"]
        assert "0/2" in (await call("self.game.status"))["content"][0]["text"]
        assert "1/2" in (await call("self.game.hit"))["content"][0]["text"]
        assert "不记分" in (await call("self.game.stop"))["content"][0]["text"]

    asyncio.run(main())
