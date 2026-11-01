"""验证车端独立拒绝迟到、越界、重复指令与发射冷却。"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def car(monkeypatch):
    driver = types.ModuleType("wifi_main")
    driver.calls = []
    driver._arm = lambda ms: driver.calls.append(("arm", ms))
    driver.halt = lambda: driver.calls.append(("halt",))
    driver.handle = lambda command: driver.calls.append(("handle", command)) or "ok"
    transport = types.ModuleType("cloud_probe")
    monkeypatch.setitem(sys.modules, "wifi_main", driver)
    monkeypatch.setitem(sys.modules, "cloud_probe", transport)
    monkeypatch.setitem(sys.modules, "network", types.ModuleType("network"))
    path = Path(__file__).resolve().parents[2] / "firmware/car/cloud_main.py"
    spec = importlib.util.spec_from_file_location("cloud_car_firmware_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, driver, transport


def test_device_checks_freshness_speed_duration_and_firing(car):
    module, driver, _ = car
    motion = {"op": "drive", "l": 30, "r": 30, "ms": 400}
    assert module._apply(motion, 100)
    assert driver.calls == [("arm", 400), ("handle", "drive 30 30 400")]
    driver.calls.clear()
    for command, elapsed in [
        (motion, 801),
        ({**motion, "l": 41}, 100),
        ({**motion, "l": True}, 100),
        ({**motion, "ms": 5301}, 100),
        ({"op": "fire", "ms": 501}, 100),
    ]:
        assert not module._apply(command, elapsed)
    assert driver.calls == []
    assert module._apply({"op": "halt"}, 9000)
    assert driver.calls == [("halt",)]


def test_device_fire_is_bounded_cooled_down_and_never_replayed(car):
    module, driver, _ = car
    clock = [100]
    module.time = types.SimpleNamespace(ticks_ms=lambda: clock[0], ticks_diff=lambda a, b: a - b,
                                        ticks_add=lambda a, b: a + b)
    shot = {"op": "fire", "ms": 500}
    for bad in ({**shot, "ms": True}, {**shot, "ms": 0}, {**shot, "ms": 501}):
        assert not module._apply(bad, 100)
    assert not module._apply(shot, 801)
    assert driver.calls == []
    assert module._apply(shot, 100)
    assert driver.calls == [("arm", 500), ("handle", "fire 500")]
    clock[0] = 2099
    assert not module._apply(shot, 100)
    clock[0] = 2100
    assert module._apply(shot, 100)


def test_device_stops_if_driver_rejects_or_fails(car):
    module, driver, _ = car
    driver.handle = lambda command: "err"
    assert not module._apply({"op": "drive", "l": 20, "r": 20, "ms": 400}, 100)
    assert driver.calls == [("arm", 400), ("halt",)]


def test_device_rejects_repeated_action_id(car):
    module, driver, transport = car
    sent = []
    responses = iter([
        {"type": "cmd", "nonce": 1, "id": 3, "op": "drive", "l": 20, "r": 20, "ms": 400},
        {"type": "cmd", "nonce": 2, "id": 3, "op": "drive", "l": 20, "r": 20, "ms": 400},
    ])
    transport._send_frame = lambda stream, msg: sent.append(msg)
    transport._read_message = lambda stream: next(responses)
    module.time = types.SimpleNamespace(ticks_ms=lambda: 1, ticks_diff=lambda a, b: a - b, sleep_ms=lambda ms: None)
    with pytest.raises(ValueError, match="编号"):
        module._session(object())
    assert driver.calls == [("arm", 400), ("handle", "drive 20 20 400")]
    assert len(sent) == 3  # 第一轮 poll/ack，第二轮 poll；没有执行第二次


@pytest.mark.parametrize(
    "wifi,cloud,button,expected",
    [(True, True, 1, "cloud"), (True, False, 1, "wifi"),
     (True, True, 0, "rc"), (False, True, 1, "rc")],
)
def test_boot_mode_preserves_original_remote_control(monkeypatch, wifi, cloud, button, expected):
    class Selected(BaseException):
        pass

    def select(name):
        def run():
            raise Selected(name)
        return run

    fake_os = types.ModuleType("os")
    def stat(path):
        if (path == "/wifi.json" and wifi) or (path == "/cloud.json" and cloud):
            return ()
        raise OSError("not found")
    fake_os.stat = stat
    fake_machine = types.ModuleType("machine")
    fake_machine.Pin = type("Pin", (), {"IN": 0, "__init__": lambda self, *args: None,
                                        "value": lambda self: button})
    bbl = types.ModuleType("bbl_product")
    bbl.set_app_name = bbl.set_app_version = lambda *args: None
    for name, fake in {"os": fake_os, "machine": fake_machine, "bbl_product": bbl,
                       "cloud_main": types.SimpleNamespace(main=select("cloud")),
                       "wifi_main": types.SimpleNamespace(main=select("wifi")),
                       "rc_main": types.SimpleNamespace(main=select("rc"))}.items():
        monkeypatch.setitem(sys.modules, name, fake)
    monkeypatch.setattr(sys, "path", list(sys.path))
    source = (Path(__file__).resolve().parents[2] / "firmware/car/boot.py").read_text()
    with pytest.raises(Selected, match=expected):
        exec(compile(source, "boot.py", "exec"), {})
