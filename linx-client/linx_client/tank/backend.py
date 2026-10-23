"""小车底层驱动接口。

backend 只负责"设定状态"，时长由 executor 控制；每次 drive/fire 都带一个
failsafe_ms，设备端到点自动停（防止上位机崩溃后小车一直跑）。
"""

from typing import Protocol


class TankBackend(Protocol):
    async def connect(self) -> None: ...

    async def drive(self, left: int, right: int, failsafe_ms: int) -> None:
        """left/right: -100..100，正数前进。"""

    async def aim(self, angle: int) -> None:
        """炮塔俯仰 0..180，90 为水平。"""

    async def fire(self, pulse_ms: int) -> None:
        """发射电机通电 pulse_ms 毫秒（一发）。"""

    async def halt(self) -> None:
        """立刻关闭所有输出。"""

    async def close(self) -> None: ...


class MockBackend:
    """不接硬件，只打印并记录调用，用于联调灵矽和单元测试。"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.calls: list[tuple] = []

    def _log(self, *call):
        self.calls.append(call)
        if self.verbose:
            print(f"[mock-tank] {call[0]}{call[1:]}")

    async def connect(self):
        self._log("connect")

    async def drive(self, left, right, failsafe_ms):
        self._log("drive", left, right, failsafe_ms)

    async def aim(self, angle):
        self._log("aim", angle)

    async def fire(self, pulse_ms):
        self._log("fire", pulse_ms)

    async def halt(self):
        self._log("halt")

    async def close(self):
        self._log("close")
