"""动作宏（语音编程）：把一串动作起个名字记下来，以后一句话就执行。

两种教法：
- 一句话定义：“记住，巡逻就是前进 2 秒、左转 90 度，重复 4 次” → self.macro.save(name, steps, repeat)
- 边做边学：“开始学动作” → 用户照常下令、车照常做，executor 把每个动作记下来 → “记住这套叫胜利舞”

步骤写法（大模型照这个格式写 steps，一行或用分号隔开；录制时 executor 也按这个格式记）：
  前进 2000 [速度 80] / 后退 1000      时长毫秒，也可以写“2秒”
  左转 90 / 右转 45                    角度
  炮管抬 300 / 炮管压 300              毫秒
  开火 1                               发数（仍受 --no-fire、冷却、每次最多 3 发约束）
  跳舞
  等待 500                             毫秒
  情绪 开心|生气|难过|惊讶|思考
  执行 巡逻                            调用另一个宏（最多套 3 层）
执行时作为一个任务跑（start_mission）：说“停”随时取消，每一步仍走 executor 的安全约束；一次最多跑 MAX_RUN_S 秒。
宏存在本地 .macros.json（用户自己的数据，不进仓库）。
"""

import asyncio
import json
import re
import time
from pathlib import Path

from .tank import TankExecutor
from .tank.executor import EMOTES

MAX_STEPS = 40
MAX_REPEAT = 10
MAX_DEPTH = 3
MAX_RUN_S = 60.0
EMOTE_NAMES = {v: k for k, v in EMOTES.items()}  # 开心 → happy

BUILTIN = {  # 没有存过任何宏时先给两个，演示用；可以被覆盖或删除
    "巡逻": {"steps": ["前进 1500", "左转 90"], "repeat": 4},
    "庆祝": {"steps": ["情绪 开心", "跳舞", "炮管抬 300", "炮管压 300"], "repeat": 1},
}

_NUM = r"(\d+(?:\.\d+)?)"


def _ms(text: str) -> int | None:
    """“2000”“2秒”“1.5s”“500毫秒”→ 毫秒。"""
    m = re.fullmatch(_NUM + r"\s*(毫秒|ms|秒|s)?", text.strip())
    if not m:
        return None
    v, unit = float(m.group(1)), m.group(2)
    return int(v * 1000) if unit in ("秒", "s") else int(v)


def parse_step(step: str) -> tuple | None:
    """一步 → (动作, 参数...)；看不懂返回 None。"""
    s = re.sub(r"(\d)\s*度", r"\1", step.strip()).replace("：", " ").replace(":", " ")  # “90度”，别动“速度”
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None
    m = re.fullmatch(r"(前进|后退)\s*(.+?)(?:\s*速度\s*(\d+))?", s)
    if m and _ms(m.group(2)):
        return ("move", "forward" if m.group(1) == "前进" else "backward", _ms(m.group(2)), int(m.group(3) or 80))
    m = re.fullmatch(r"(左转|右转)\s*(\d+)?", s)
    if m:
        return ("turn", "left" if m.group(1) == "左转" else "right", int(m.group(2) or 90))
    m = re.fullmatch(r"(?:炮管)?(抬|压|抬高|压低)(?:炮管)?\s*(.+)?", s)
    if m:
        return ("barrel", "up" if m.group(1).startswith("抬") else "down", _ms(m.group(2) or "300") or 300)
    m = re.fullmatch(r"(?:开火|发射|射击)\s*(\d+)?\s*发?", s)
    if m:
        return ("fire", int(m.group(1) or 1))
    if s in ("跳舞", "跳个舞", "跳一段舞"):
        return ("dance",)
    m = re.fullmatch(r"(?:等待|等|暂停|停顿)\s*(.+)", s)
    if m and _ms(m.group(1)):
        return ("wait", _ms(m.group(1)))
    m = re.fullmatch(r"情绪\s*(\S+)", s)
    if m and m.group(1) in EMOTE_NAMES:
        return ("emote", EMOTE_NAMES[m.group(1)])
    m = re.fullmatch(r"(?:执行|做|跑)\s*(\S+)", s)
    if m:
        return ("macro", m.group(1).strip("「」“”\"'"))
    return None


def split_steps(steps: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;；,，、\n]|然后|接着|再", steps) if p.strip()]


def _step_ms(step: tuple, tank: TankExecutor) -> int:
    kind = step[0]
    if kind == "move":
        return step[2]
    if kind == "turn":
        return int(step[2] / 90 * 400)
    if kind in ("barrel", "wait"):
        return step[-1]
    if kind == "fire":
        return int(step[1] * (tank.fire_pulse_ms + tank.fire_cooldown_s * 1000))
    if kind == "dance":
        return 2400
    if kind == "emote":
        return 600
    return 0


class Macros:
    def __init__(self, tank: TankExecutor, path: str | Path, say=None):
        self.tank = tank
        self.path = Path(path)
        self.say = say
        self.macros: dict[str, dict] = {}
        self._load()

    # ---- 存取 ----
    def _load(self):
        try:
            self.macros = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.macros = {k: dict(v) for k, v in BUILTIN.items()}
        except (OSError, ValueError) as e:
            print(f"[macro] 读不了 {self.path}：{e}，先用内置的")
            self.macros = {k: dict(v) for k, v in BUILTIN.items()}

    def _save_file(self):
        try:
            self.path.write_text(json.dumps(self.macros, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            print(f"[macro] 存不了 {self.path}：{e}")

    def find(self, name: str) -> str | None:
        name = name.strip().strip("「」“”\"'")
        if name in self.macros:
            return name
        hits = [k for k in self.macros if k in name or name in k]
        return hits[0] if len(hits) == 1 else None

    def _check(self, steps: list[str], depth: int = 0) -> list[tuple]:
        """解析并校验：看不懂的步骤、不存在的子宏、套太多层都报错（大模型会照着改了再调）。"""
        out, bad = [], []
        for st in steps:
            p = parse_step(st)
            if p is None:
                bad.append(st)
            elif p[0] == "macro" and not self.find(p[1]):
                bad.append(f"{st}（没有叫“{p[1]}”的动作）")
            else:
                out.append(p)
        if bad:
            raise ValueError("这几步看不懂：" + "；".join(bad) +
                             "。每步写成“前进 2000”“左转 90”“炮管抬 300”“开火 1”“跳舞”“等待 500”“情绪 开心”“执行 巡逻”这样")
        if not out:
            raise ValueError("一个动作都没有")
        if len(out) > MAX_STEPS:
            raise ValueError(f"步骤太多了（最多 {MAX_STEPS} 步）")
        return out

    def _duration_ms(self, name: str, depth: int = 0) -> int:
        if depth > MAX_DEPTH:
            raise ValueError(f"动作套动作太多层了（最多 {MAX_DEPTH} 层），可能是自己调用了自己")
        m = self.macros[name]
        total = 0
        for p in (parse_step(s) for s in m["steps"]):
            total += self._duration_ms(self.find(p[1]), depth + 1) if p[0] == "macro" else _step_ms(p, self.tank)
        return total * m.get("repeat", 1)

    # ---- MCP 工具 ----
    def save(self, name: str, steps: str, repeat: int = 1) -> str:
        name = name.strip().strip("「」“”\"'")
        if not name:
            raise ValueError("要给这套动作起个名字")
        parts = split_steps(steps)
        parsed = self._check(parts)
        old = self.macros.get(name)
        self.macros[name] = {"steps": parts, "repeat": max(1, min(int(repeat), MAX_REPEAT))}
        try:
            secs = self._duration_ms(name) / 1000
        except ValueError:
            if old is None:
                del self.macros[name]
            else:
                self.macros[name] = old
            raise
        self._save_file()
        fire = "，里面有开火" if any(p[0] == "fire" for p in parsed) else ""
        rep = f"，重复 {self.macros[name]['repeat']} 次" if self.macros[name]["repeat"] > 1 else ""
        return f"{'改好' if old else '记住'}了「{name}」：{len(parsed)} 步{rep}，大约 {secs:.0f} 秒{fire}。说“{name}”就能执行"

    def run(self, name: str, repeat: int = 0) -> str:
        key = self.find(name)
        if key is None:
            raise ValueError(f"没有叫“{name}”的动作。现在有：{'、'.join(self.macros) or '（还没有）'}")
        times = max(1, min(int(repeat), MAX_REPEAT)) if repeat else self.macros[key].get("repeat", 1)
        secs = self._duration_ms(key) / max(1, self.macros[key].get("repeat", 1)) * times / 1000
        return self.tank.start_mission(f"动作「{key}」", lambda: self._play(key, times)) + \
            f"（大约 {min(secs, MAX_RUN_S):.0f} 秒，说“停”随时停）"

    def list(self) -> str:
        if not self.macros:
            return "还没有记住任何动作"
        return "；".join(f"「{k}」：{'、'.join(v['steps'])}" + (f"（重复 {v['repeat']} 次）" if v.get("repeat", 1) > 1 else "")
                        for k, v in self.macros.items())

    def delete(self, name: str) -> str:
        key = self.find(name)
        if key is None:
            raise ValueError(f"没有叫“{name}”的动作")
        del self.macros[key]
        self._save_file()
        return f"忘掉「{key}」了"

    def record_start(self) -> str:
        self.tank.recorder = []
        return "开始学了：接下来你说的动作我边做边记，说“记住这套叫什么”就存下来，说“不学了”就放弃"

    def record_stop(self, name: str = "", repeat: int = 1) -> str:
        steps, self.tank.recorder = self.tank.recorder, None
        if steps is None:
            raise ValueError("现在没在学动作：先说“开始学动作”")
        if not name:
            return f"不学了，刚才的 {len(steps)} 个动作没存"
        if not steps:
            raise ValueError("刚才一个动作都没做，没东西可记")
        return self.save(name, "；".join(steps), repeat)

    # ---- 执行 ----
    async def _play(self, name: str, times: int):
        t0 = time.monotonic()
        try:
            for _ in range(times):
                await self._play_once(name, t0, 0)
        except TimeoutError:
            msg = f"「{name}」做了 {MAX_RUN_S:.0f} 秒还没完，先停下"
            print(f"⏱ {msg}")
            if self.say:
                await self.say(msg)
        except PermissionError as e:  # 例如 --no-fire
            msg = f"「{name}」里有开火，但{e}，后面的动作没做"
            print(f"🚫 {msg}")
            if self.say:
                await self.say(msg)

    async def _play_once(self, name: str, t0: float, depth: int):
        tank = self.tank
        for p in (parse_step(s) for s in self.macros[name]["steps"]):
            if time.monotonic() - t0 > MAX_RUN_S:
                raise TimeoutError
            kind = p[0]
            if kind == "macro":
                sub = self.find(p[1])
                for _ in range(self.macros[sub].get("repeat", 1)):
                    await self._play_once(sub, t0, depth + 1)
                continue
            if kind == "wait":
                await asyncio.sleep(p[1] / 1000)
                continue
            if kind == "move":
                tank.move(p[1], p[2], p[3])
            elif kind == "turn":
                tank.turn(p[1], p[2])
            elif kind == "barrel":
                tank.barrel(p[1], p[2])
            elif kind == "fire":
                tank.fire(p[1])
            elif kind == "dance":
                tank.dance()
            elif kind == "emote":
                tank.emote(p[1], from_mission=True)
            await tank.wait_idle()
