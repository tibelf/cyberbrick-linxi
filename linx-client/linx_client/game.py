"""打靶挑战：地上摆几个目标（纸杯、瓶子），玩家只能用语音指挥小车，限时内全部打倒。

- 开始：语音“开始打靶挑战，3 个目标，限时两分钟”（self.game.start）或网页按钮
- 打中：语音“打中了 / 又倒了一个”（self.game.hit）或网页上点「命中 +1」——倒没倒由人判断，程序不猜
- 结束：全部打中、时间到、或说“不玩了”（self.game.stop）
- 记分：命中 ×100，全部打中再加剩余秒数 ×5，没打中的子弹每发 −20，每条指令 −5（鼓励用最少的话指挥），最低 0 分
- 排行榜存在本地 .leaderboard.json（不进仓库），网页上显示前 10 名
开火仍然只在玩家明确说“开火”时才发生，游戏本身从不开火；时间到只播报，不动车。
"""

import asyncio
import json
import time
from pathlib import Path

from .tank import TankExecutor

DEFAULT_TARGETS = 3
DEFAULT_SECONDS = 120
MAX_TARGETS = 10
COUNTED_TOOLS = {  # 算作“一条指令”的工具：玩家指挥小车动的那些
    "self.tank.move", "self.tank.turn", "self.tank.barrel", "self.tank.fire", "self.tank.dance",
    "self.tank.seek", "self.macro.run",
}


class TargetChallenge:
    def __init__(self, tank: TankExecutor, path: str | Path, say=None, on_change=None, clock=time.time):
        self.tank = tank
        self.path = Path(path)
        self.say = say
        self.on_change = on_change  # 状态一变就通知网页：on_change(snapshot)
        self.clock = clock
        self.running = False
        self.player = ""
        self.targets = DEFAULT_TARGETS
        self.seconds = DEFAULT_SECONDS
        self.hits = 0
        self.commands = 0
        self.t0 = 0.0
        self.shots0 = 0
        self.last_result: dict | None = None
        self._timer: asyncio.Task | None = None
        self.board = self._load()

    # ---- 排行榜 ----
    def _load(self) -> list:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self):
        try:
            self.path.write_text(json.dumps(self.board, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            print(f"[game] 存不了排行榜：{e}")

    # ---- 状态 ----
    @property
    def shots(self) -> int:
        return self.tank.shots_fired - self.shots0 if self.running else 0

    def remaining(self) -> float:
        return max(0.0, self.seconds - (self.clock() - self.t0)) if self.running else 0.0

    def score(self, finished_all: bool) -> int:
        bonus = int(self.remaining() * 5) if finished_all else 0
        misses = max(0, self.shots - self.hits)
        return max(0, self.hits * 100 + bonus - misses * 20 - self.commands * 5)

    def snapshot(self) -> dict:
        return {
            "running": self.running, "player": self.player, "targets": self.targets, "hits": self.hits,
            "seconds": self.seconds, "ends_at": (self.t0 + self.seconds) * 1000 if self.running else None,
            "shots": self.shots, "commands": self.commands, "score": self.score(False) if self.running else None,
            "last": self.last_result, "board": self.board[:10],
        }

    def _changed(self):
        if self.on_change:
            self.on_change(self.snapshot())

    # ---- MCP 工具 / 网页按钮 ----
    def start(self, targets: int = DEFAULT_TARGETS, seconds: int = DEFAULT_SECONDS, player: str = "") -> str:
        if self.running:
            raise ValueError("挑战已经在进行了：说“不玩了”可以结束")
        self.running = True
        self.player = (player or "").strip()[:12] or "无名英雄"
        self.targets = max(1, min(int(targets), MAX_TARGETS))
        self.seconds = max(20, min(int(seconds), 600))
        self.hits = self.commands = 0
        self.t0 = self.clock()
        self.shots0 = self.tank.shots_fired
        self.last_result = None
        try:
            self._timer = asyncio.get_running_loop().create_task(self._countdown())
        except RuntimeError:  # 没有事件循环（单元测试直接调用）
            self._timer = None
        self._changed()
        return (f"打靶挑战开始！{self.player}，{self.targets} 个目标，限时 {self.seconds} 秒。"
                f"每打倒一个就说“打中了”，指令越少分越高")

    def hit(self, count: int = 1) -> str:
        if not self.running:
            raise ValueError("挑战还没开始：说“开始打靶挑战”")
        self.hits = min(self.targets, self.hits + max(1, int(count)))
        if self.hits >= self.targets:
            return self._finish(True)
        self._changed()
        return f"命中！{self.hits}/{self.targets}，还剩 {self.remaining():.0f} 秒"

    def stop(self) -> str:
        if not self.running:
            return "现在没有在进行的挑战"
        return self._finish(False, gave_up=True)

    def status(self) -> str:
        if not self.running:
            best = self.board[0] if self.board else None
            return "现在没在挑战" + (f"，最高分是 {best['player']} 的 {best['score']} 分" if best else "")
        return (f"{self.player} 挑战中：命中 {self.hits}/{self.targets}，还剩 {self.remaining():.0f} 秒，"
                f"开火 {self.shots} 次，指令 {self.commands} 条，当前 {self.score(False)} 分")

    def on_tool(self, name: str | None):
        """会话里每次工具调用都会通知到这里，挑战中玩家指挥小车的指令计数。"""
        if self.running and name in COUNTED_TOOLS:
            self.commands += 1
            self._changed()

    # ---- 结束 ----
    async def _countdown(self):
        try:
            while self.running:
                left = self.remaining()
                if left <= 0:
                    msg = self._finish(False)
                    if self.say:
                        await self.say(msg)
                    return
                await asyncio.sleep(min(1.0, left))
        except asyncio.CancelledError:
            pass

    def _finish(self, all_hit: bool, gave_up: bool = False) -> str:
        used = min(self.seconds, self.clock() - self.t0)
        score = self.score(all_hit)
        result = {"player": self.player, "score": score, "hits": self.hits, "targets": self.targets,
                  "seconds": round(used, 1), "shots": self.shots, "commands": self.commands,
                  "success": all_hit, "at": time.strftime("%m-%d %H:%M")}
        self.running = False
        if self._timer and self._timer is not asyncio.current_task():
            self._timer.cancel()
        self._timer = None
        rank = None
        if not gave_up:
            self.board.append(result)
            self.board.sort(key=lambda r: (-r["score"], r["seconds"]))
            self.board = self.board[:50]
            self._save()
            rank = self.board.index(result) + 1
        result["rank"] = rank
        self.last_result = result
        self._changed()
        if all_hit:
            try:
                self.tank.dance()  # 庆祝一下（车正忙就算了）
            except Exception:  # noqa: BLE001
                pass
            head = f"全部打中！用时 {used:.0f} 秒"
        elif gave_up:
            return f"挑战结束：命中 {self.hits}/{self.targets}，这局不记分"
        else:
            head = f"时间到！命中 {self.hits}/{self.targets}"
        return (f"{head}，开火 {result['shots']} 次，指令 {result['commands']} 条，得分 {score}，"
                f"排行榜第 {rank} 名")
