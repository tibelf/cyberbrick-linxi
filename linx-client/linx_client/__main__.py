"""命令行入口：python -m linx_client {activate,run,tank-test,list-audio}"""

import argparse
import asyncio
import sys
import threading

from . import ota
from .identity import load_or_create
from .mcp import McpServer
from .session import LinxSession
from .tank import TankExecutor, make_backend
from .tools import register_tank_tools


def _add_tank_args(p: argparse.ArgumentParser):
    p.add_argument("--backend", choices=["mock", "serial"], default="mock", help="mock=不接硬件只打印")
    p.add_argument("--port", help="CyberBrick 串口，如 /dev/tty.usbmodem1101 或 COM3")
    p.add_argument("--max-speed", type=int, default=40, help="速度上限 0-100（首测建议 40）")
    p.add_argument("--no-fire", action="store_true", help="锁定发射功能")


def _make_tank(args) -> TankExecutor:
    return TankExecutor(make_backend(args.backend, args.port), max_speed=args.max_speed, fire_enabled=not args.no_fire)


def cmd_activate(args):
    ident = load_or_create(args.state)
    data = ota.report(ident, args.ota_url)
    ws = data.get("websocket") or {}
    if ws.get("token"):
        print(f"✅ 设备 {ident.mac} 已绑定，WebSocket: {ws.get('url')}")
    else:
        ota.wait_for_binding(ident, url=args.ota_url)
        print("✅ 绑定完成")


def _start_console(loop: asyncio.AbstractEventLoop, tank: TankExecutor, session: LinxSession, done: asyncio.Event):
    """键盘：回车/s = 急停；t <文字> = 发文本调试；q = 退出。"""

    def worker():
        print("⌨️  回车或 s=急停 · t 文字=发文本 · q=退出")
        for line in sys.stdin:
            line = line.strip()
            if line in ("", "s", "stop", "停"):
                asyncio.run_coroutine_threadsafe(tank.stop(), loop)
                print("🛑 急停")
            elif line.startswith("t "):
                asyncio.run_coroutine_threadsafe(session.send_text(line[2:].strip()), loop)
            elif line == "q":
                loop.call_soon_threadsafe(done.set)
                return
        loop.call_soon_threadsafe(done.set)

    threading.Thread(target=worker, daemon=True).start()


async def _run(args):
    ident = load_or_create(args.state)
    print(f"[linx] 设备 Device-Id={ident.mac}")

    def get_endpoint():
        if args.token:
            return args.ws_url or "wss://xrobo-io.qiniuapi.com/v1/ws/", args.token
        ws = ota.wait_for_binding(ident, url=args.ota_url)
        return args.ws_url or ws["url"], ws["token"]

    tank = _make_tank(args)
    await tank.start()
    mcp = McpServer()
    register_tank_tools(mcp, tank)
    session = LinxSession(
        ident,
        mcp,
        get_endpoint,
        audio=not args.no_audio,
        listen_mode=args.mode,
        duplex=args.duplex,
        mic_device=args.mic,
        speaker_device=args.speaker,
    )
    done = asyncio.Event()
    _start_console(asyncio.get_running_loop(), tank, session, done)
    runner = asyncio.create_task(session.run_forever())
    await asyncio.wait([runner, asyncio.create_task(done.wait())], return_when=asyncio.FIRST_COMPLETED)
    await session.close()
    runner.cancel()
    await tank.close()


async def _tank_test(args):
    tank = _make_tank(args)
    await tank.start()
    steps = [
        ("前进 1 秒", lambda: tank.move("forward", 1000, args.max_speed)),
        ("后退 1 秒", lambda: tank.move("backward", 1000, args.max_speed)),
        ("左转 0.6 秒", lambda: tank.turn("left", 600, args.max_speed)),
        ("右转 0.6 秒", lambda: tank.turn("right", 600, args.max_speed)),
        ("炮塔抬到 120", lambda: tank.aim(120)),
        ("炮塔回到 90", lambda: tank.aim(90)),
    ]
    if not args.no_fire:
        steps.append(("发射 1 发（确认前方安全！）", lambda: tank.fire(1)))
    try:
        for name, fn in steps:
            input(f"▶ 回车执行：{name}（Ctrl-C 退出）")
            fn()
            while tank.status()["current"] or tank.status()["queued"]:
                await asyncio.sleep(0.1)
    finally:
        await tank.close()


def main():
    ap = argparse.ArgumentParser(prog="linx_client", description="把电脑模拟成灵矽设备，语音控制 CyberBrick 小车")
    ap.add_argument("--state", default=".linx_device.json", help="虚拟设备身份文件")
    ap.add_argument("--ota-url", default=ota.OTA_URL)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("activate", help="上报 OTA，显示激活码并等待在智控台绑定")

    p_run = sub.add_parser("run", help="连接灵矽，开始语音控车")
    _add_tank_args(p_run)
    p_run.add_argument("--no-audio", action="store_true", help="不开麦克风/扬声器（只联调工具调用）")
    p_run.add_argument("--mode", default="realtime", choices=["realtime", "auto", "manual"])
    p_run.add_argument("--duplex", action="store_true", help="全双工（需要耳机或回声消除，否则会自我打断）")
    p_run.add_argument("--mic", help="麦克风设备名或序号（见 list-audio）")
    p_run.add_argument("--speaker", help="扬声器设备名或序号")
    p_run.add_argument("--ws-url", help="覆盖 WebSocket 地址")
    p_run.add_argument("--token", help="直接指定 token，跳过 OTA")

    p_test = sub.add_parser("tank-test", help="逐步测试小车动作（先把车架空！）")
    _add_tank_args(p_test)

    sub.add_parser("list-audio", help="列出音频设备")

    args = ap.parse_args()
    if args.cmd == "activate":
        cmd_activate(args)
    elif args.cmd == "run":
        for attr in ("mic", "speaker"):
            v = getattr(args, attr)
            if v is not None and v.isdigit():
                setattr(args, attr, int(v))
        asyncio.run(_run(args))
    elif args.cmd == "tank-test":
        asyncio.run(_tank_test(args))
    elif args.cmd == "list-audio":
        import sounddevice as sd

        print(sd.query_devices())


if __name__ == "__main__":
    main()
