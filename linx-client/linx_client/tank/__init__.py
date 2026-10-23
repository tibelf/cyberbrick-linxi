from .backend import MockBackend, TankBackend
from .executor import TankExecutor


def make_backend(kind: str, port: str | None = None) -> TankBackend:
    if kind == "mock":
        return MockBackend()
    if kind == "serial":
        from .serial_repl import SerialBackend

        return SerialBackend(port)
    raise ValueError(f"未知 backend: {kind}")


__all__ = ["MockBackend", "TankBackend", "TankExecutor", "make_backend"]
