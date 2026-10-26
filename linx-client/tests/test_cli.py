"""命令行入口：不同参数组合下 run 注册了哪些工具、各子命令的流程。不连灵矽、不接车。"""

import asyncio
import socket
import sys

import pytest

import linx_client.__main__ as cli
from linx_client.tank import MockBackend, TankExecutor


class FakeSession:
    """代替 LinxSession：run_forever 只取一次连接地址就返回，让 _run 走完收尾流程。"""

    last = None

    def __init__(self, ident, mcp, get_endpoint, **kw):
        self.ident, self.mcp, self.get_endpoint, self.kw = ident, mcp, get_endpoint, kw
        self.ws = None
        self.on_event = None
        self.mic_enabled = True
        self.closed = False
        FakeSession.last = self

    async def run_forever(self):
        self.endpoint = self.get_endpoint()

    async def close(self):
        self.closed = True

    async def say(self, text):
        pass

    async def abort(self):
        pass

    async def send_text(self, text):
        pass


@pytest.fixture
def run_cli(monkeypatch, tmp_path):
    """用给定参数跑一次 `python -m linx_client run ...`，返回假会话（带着注册好工具的 mcp）。"""
    monkeypatch.setattr(cli, "LinxSession", FakeSession)
    monkeypatch.setattr(cli, "_start_console", lambda *a: None)
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    def run(*args):
        argv = ["linx_client", "--state", str(tmp_path / "id.json"), "run", "--backend", "mock", "--no-audio", *args]
        monkeypatch.setattr(sys, "argv", argv)
        cli.main()
        return FakeSession.last

    return run


@pytest.fixture
def photo_file(tmp_path):
    f = tmp_path / "shot.jpg"
    f.write_bytes(b"\xff\xd8fake\xff\xd9")
    return str(f)


BASE_TOOLS = {"self.tank.move", "self.tank.turn", "self.tank.fire", "self.tank.stop", "self.tank.dance",
              "self.tank.set_persona", "self.macro.save", "self.macro.run", "self.game.start"}


def tools(session) -> set[str]:
    return set(session.mcp.tools)


def test_run_without_camera_registers_blind_wander_only(run_cli):
    s = run_cli("--token", "tok")
    names = tools(s)
    assert BASE_TOOLS <= names
    assert "self.tank.wander" in names  # 没摄像头也能闭眼慢逛
    assert not names & {"self.camera.take_photo", "self.tank.seek", "self.blind.goto"}
    assert s.endpoint == ("wss://xrobo-io.qiniuapi.com/v1/ws/", "tok")  # 给了 token 就跳过 OTA
    assert s.closed


def test_run_with_camera_registers_photo_seek_and_wander(run_cli, photo_file):
    names = tools(run_cli("--token", "tok", "--camera", photo_file))
    assert {"self.camera.take_photo", "self.tank.seek", "self.tank.wander"} <= names
    assert "self.blind.goto" not in names


def test_run_blind_replaces_seek(run_cli, photo_file):
    names = tools(run_cli("--token", "tok", "--camera", photo_file, "--blind"))
    assert {"self.blind.map", "self.blind.goto", "self.camera.take_photo"} <= names
    assert "self.tank.seek" not in names  # 闭眼模式不注册一直拍照的 seek


def test_run_blind_without_camera_warns(run_cli, capsys):
    names = tools(run_cli("--token", "tok", "--blind", "--blind-view-cm", "120"))
    out = capsys.readouterr().out
    assert "--blind 要配合 --camera" in out
    assert "self.blind.goto" not in names


def test_run_blind_view_without_blind_warns(run_cli, capsys, photo_file):
    run_cli("--token", "tok", "--camera", photo_file, "--blind-view-cm", "120")
    assert "--blind-view-cm 只在加了 --blind" in capsys.readouterr().out


def test_run_marker_registers_map_tool(run_cli, photo_file):
    names = tools(run_cli("--token", "tok", "--camera", photo_file, "--marker", "--marker-targets", "1=绿色瓶子"))
    assert {"self.map.build", "self.tank.seek"} <= names


def test_run_gets_endpoint_from_ota_and_parses_device_numbers(run_cli, monkeypatch):
    monkeypatch.setattr(cli.ota, "wait_for_binding", lambda ident, url: {"url": "ws://ota/", "token": "t2"})
    s = run_cli("--mic", "2", "--speaker", "USB 耳机", "--duplex")
    assert s.endpoint == ("ws://ota/", "t2")
    assert s.kw["mic_device"] == 2 and s.kw["speaker_device"] == "USB 耳机"  # 纯数字按设备序号
    assert s.kw["duplex"] is True and s.kw["audio"] is False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_run_with_web_publishes_events(run_cli, capsys):
    s = run_cli("--token", "tok", "--web", "--web-port", str(_free_port()))
    assert "网页控制台" in capsys.readouterr().out
    s.on_event({"type": "tool", "name": "self.tank.move"})  # 网页、计分、情绪各监听都能收到，不报错


def test_report_tank_errors_publishes_and_rate_limits_speech(monkeypatch):
    async def main():
        tank = TankExecutor(MockBackend())
        said, published = [], []

        class S:
            ws = object()

            async def say(self, text):
                said.append(text)

        cli._report_tank_errors(tank, S(), published.append)
        tank.on_error("车没有应答")
        tank.on_error("车没有应答")
        await asyncio.sleep(0)
        return said, published

    said, published = asyncio.run(main())
    assert [p["text"] for p in published] == ["车没有应答", "车没有应答"]  # 网页每次都显示
    assert said == ["车没有应答"]  # 语音 20 秒内只念一次


def test_activate_already_bound(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.ota, "report", lambda ident, url: {"websocket": {"url": "ws://x/", "token": "t"}})
    monkeypatch.setattr(sys, "argv", ["linx_client", "--state", str(tmp_path / "id.json"), "activate"])
    cli.main()
    assert "已绑定" in capsys.readouterr().out


def test_activate_waits_for_binding(monkeypatch, tmp_path, capsys):
    waited = []
    monkeypatch.setattr(cli.ota, "report", lambda ident, url: {"activation": {"code": "654321"}})
    monkeypatch.setattr(cli.ota, "wait_for_binding", lambda ident, url: waited.append(url))
    monkeypatch.setattr(sys, "argv", ["linx_client", "--state", str(tmp_path / "id.json"), "activate"])
    cli.main()
    assert waited and "绑定完成" in capsys.readouterr().out


class FakeRepl:
    """代替 NetRepl：记下执行的代码。"""

    instances = []

    def __init__(self, host, password):
        self.host, self.password = host, password
        self.codes = []
        self.reset_called = self.closed = False
        FakeRepl.instances.append(self)

    def exec(self, code):
        self.codes.append(code)
        return "hello\n"

    def reset(self):
        self.reset_called = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_car(monkeypatch):
    FakeRepl.instances = []
    monkeypatch.setattr("linx_client.tank.netadmin.NetRepl", FakeRepl)
    monkeypatch.setenv("CYBERBRICK_ADMIN_PASSWORD", "pw123456")

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["linx_client", *args])
        cli.main()
        return FakeRepl.instances[-1]

    return run


def test_car_exec_over_wifi(fake_car, capsys):
    repl = fake_car("car-exec", "--host", "192.168.4.1", "print('hello')")
    assert repl.host == "192.168.4.1" and repl.password == "pw123456"
    assert repl.codes == ["print('hello')"]
    assert "hello" in capsys.readouterr().out
    assert not repl.reset_called  # 不加 --reset 不重启车


def test_wifi_setup_code_only_writes_boot_last(fake_car):
    repl = fake_car("wifi-setup", "--host", "10.0.0.8", "--code-only")
    opened = [c for c in repl.codes if "open(" in c]
    assert "/app/wifi_main.py" in opened[0] and "/boot.py" in opened[-1]  # boot.py 最后写，中途失败不会半新半旧
    assert not any("/wifi.json" in c for c in repl.codes)  # 只更新程序，不动 Wi-Fi 配置
    assert repl.reset_called and repl.closed


def test_wifi_setup_writes_config(fake_car, monkeypatch):
    monkeypatch.setattr(cli, "_ask_wifi_config", lambda ssid: {"networks": [{"ssid": ssid, "password": "x"}],
                                                               "admin_password": "pw123456"})
    repl = fake_car("wifi-setup", "--host", "10.0.0.8", "--ssid", "Home")
    assert "/wifi.json" in [c for c in repl.codes if "open(" in c][0]


def test_wifi_setup_uninstall_restores_factory_boot(fake_car):
    repl = fake_car("wifi-setup", "--host", "10.0.0.8", "--uninstall")
    assert any("open('/boot.py'" in c for c in repl.codes)
    assert any("os.remove('/wifi.json')" in c for c in repl.codes)
    assert any("os.remove('/app/wifi_main.py')" in c for c in repl.codes)


def test_check_car_code_rejects_syntax_error(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n")
    with pytest.raises(SyntaxError):
        cli._check_car_code(bad)


def test_admin_password_from_env(monkeypatch):
    monkeypatch.setenv("CYBERBRICK_ADMIN_PASSWORD", "abc12345")
    assert cli._admin_password() == "abc12345"


def test_tank_test_steps_through_actions(monkeypatch, capsys):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "")
    monkeypatch.setattr(sys, "argv", ["linx_client", "tank-test", "--backend", "mock", "--no-fire",
                                      "--turn-ms-per-90", "100"])
    cli.main()
    assert len(prompts) == 6  # 前后左右 + 炮管上下；--no-fire 时不测发射
    assert not any("发射" in p for p in prompts)


def test_marker_print_and_camera_test(monkeypatch, tmp_path, photo_file, capsys):
    svg = tmp_path / "m.svg"
    monkeypatch.setattr(sys, "argv", ["linx_client", "marker-print", "--out", str(svg)])
    cli.main()
    assert "<svg" in svg.read_text()

    out = tmp_path / "copy.jpg"
    monkeypatch.setattr(sys, "argv", ["linx_client", "camera-test", "--camera", photo_file, "--out", str(out)])
    cli.main()
    assert out.read_bytes() == open(photo_file, "rb").read()
