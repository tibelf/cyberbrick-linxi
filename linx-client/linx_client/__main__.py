"""命令行入口：python -m linx_client {activate,run,tank-test,camera-test,marker-print,marker-test,list-audio,wifi-setup,car-exec}"""

import argparse
import asyncio
import os
import sys
import threading
import time

from . import ota
from .identity import load_or_create
from .mcp import McpServer
from .session import LinxSession
from .tank import TankExecutor, make_backend
from .tools import register_camera_tools, register_tank_tools


def _add_tank_args(p: argparse.ArgumentParser):
    p.add_argument("--backend", choices=["mock", "serial", "udp"], default="mock",
                   help="mock=不接硬件只打印，serial=USB 线控，udp=Wi-Fi 控车（先执行 wifi-setup）")
    p.add_argument("--port", help="CyberBrick 串口，如 /dev/tty.usbmodem1101 或 COM3")
    p.add_argument("--host", help="Wi-Fi 模式下车的 IP，不填则局域网广播自动查找")
    p.add_argument("--max-speed", type=int, default=100, help="速度上限 0-100（车架空首测时可设 40）")
    p.add_argument("--no-fire", action="store_true", help="锁定发射功能")


def _make_tank(args) -> TankExecutor:
    return TankExecutor(make_backend(args.backend, args.port, args.host), max_speed=args.max_speed, fire_enabled=not args.no_fire)


def _ask_wifi_config(default_ssid: str) -> dict:
    import getpass

    networks = []
    print("依次输入车要连的 2.4G Wi-Fi（可以多个，比如家里 + 手机热点），名称留空结束")
    while True:
        default = "" if networks else default_ssid
        ssid = input(f"Wi-Fi 名称 [{default}]：").strip() or default
        if not ssid:
            break
        networks.append({"ssid": ssid, "password": getpass.getpass(f"{ssid} 的密码（不回显）：")})
    while True:
        admin = getpass.getpass("维护密码（8-9 位，用于车的热点和无线调试）：")
        if 8 <= len(admin) <= 9:
            break
        print("长度要 8-9 位（WebREPL 最多 9 位，热点至少 8 位）")
    return {"networks": networks, "admin_password": admin}


ADMIN_PASSWORD_FILE = ".car_admin_password"  # 已 gitignore，用户自己创建


def _admin_password() -> str | None:
    """维护密码：环境变量 CYBERBRICK_ADMIN_PASSWORD，或 linx-client/.car_admin_password；都没有就交互输入。"""
    from pathlib import Path

    if os.environ.get("CYBERBRICK_ADMIN_PASSWORD"):
        return os.environ["CYBERBRICK_ADMIN_PASSWORD"]
    f = Path(__file__).resolve().parents[1] / ADMIN_PASSWORD_FILE
    return f.read_text().strip() if f.exists() else None


def _check_car_code(*files) -> None:
    """上传前用 CPython 做语法检查，避免把语法错误写进车里导致连不上网（MicroPython 语法是其子集）。"""
    for f in files:
        compile(f.read_bytes(), str(f), "exec")


def _open_car(args):
    """连上车：--host 走 Wi-Fi 维护通道，否则走 USB raw REPL。返回 (repl, reset)，reset 会重启车并关闭连接。"""
    import getpass

    if args.host:
        from .tank.netadmin import NetRepl

        repl = NetRepl(args.host, _admin_password() or getpass.getpass("维护密码："))

        def reset():
            repl.reset()
            repl.close()

        return repl, reset

    import serial

    from .tank.serial_repl import RawRepl, find_port

    name = args.port or find_port()
    if not name:
        raise SystemExit("找不到车的串口：请用 USB 连上车，或者装车后用 --host <车的IP> 走无线")
    port = serial.Serial(name, 115200, timeout=0.1)
    repl = RawRepl(port, timeout=10)
    repl.enter()

    def reset():
        repl.exit()
        port.write(b"import machine; machine.reset()\r\n")
        time.sleep(0.3)
        port.close()

    return repl, reset


def cmd_car_exec(args):
    """在车上执行一段 MicroPython 代码并打印输出（调试用）。不重启车，执行完车保持停止状态。"""
    repl, reset = _open_car(args)
    code = args.code if args.code != "-" else sys.stdin.read()
    try:
        print(repl.exec(code))
    finally:
        if args.reset:
            reset()


def cmd_wifi_setup(args):
    """把 Wi-Fi 控车程序写到车上。默认走 USB；--host 走 Wi-Fi 维护通道。密码只写到车上，不进仓库。"""
    import json
    from pathlib import Path

    from .tank.serial_repl import put_file, remove_file

    fw = Path(__file__).resolve().parents[2] / "firmware"
    repl, reset = _open_car(args)
    try:
        if args.uninstall:
            put_file(repl, "/boot.py", (fw / "backup-car" / "boot.py").read_bytes())
            remove_file(repl, "/wifi.json")
            remove_file(repl, "/app/wifi_main.py")
            print("✅ 已恢复原厂程序")
        else:
            if not args.code_only:
                cfg = _ask_wifi_config(args.ssid)
                put_file(repl, "/wifi.json", json.dumps(cfg).encode())
            _check_car_code(fw / "car" / "wifi_main.py", fw / "car" / "boot.py")
            put_file(repl, "/app/wifi_main.py", (fw / "car" / "wifi_main.py").read_bytes())
            put_file(repl, "/boot.py", (fw / "car" / "boot.py").read_bytes())
            print("✅ 已写入，车正在重启。灯：黄=连接中，蓝=已连上 Wi-Fi，紫=没连上、自己开了热点 CyberBrick-Tank")
            print("   开机时按住板载按键 = 原厂遥控")
    finally:
        reset()  # 重启后按新的 boot.py 选择模式（boot.py 最后写，中途失败也不会半新半旧）


def cmd_activate(args):
    ident = load_or_create(args.state)
    data = ota.report(ident, args.ota_url)
    ws = data.get("websocket") or {}
    if ws.get("token"):
        print(f"✅ 设备 {ident.mac} 已绑定，WebSocket: {ws.get('url')}")
    else:
        ota.wait_for_binding(ident, url=args.ota_url)
        print("✅ 绑定完成")


ERROR_SAY_GAP_S = 20  # 车出问题时语音提醒的最小间隔，车关着时不至于一直念


def _report_tank_errors(tank: TankExecutor, session: LinxSession, publish=None):
    """动作在后台失败时，工具早就回了“已安排”，大模型并不知道。这里主动告诉用户：网页上显示，语音念一句。"""
    last_say = [0.0]

    def on_error(message: str):
        if publish:
            publish({"type": "error", "text": message})
        now = time.monotonic()
        if session.ws is not None and now - last_say[0] >= ERROR_SAY_GAP_S:
            last_say[0] = now
            asyncio.create_task(session.say(message))

    tank.on_error = on_error


def _start_console(loop: asyncio.AbstractEventLoop, tank: TankExecutor, session: LinxSession, done: asyncio.Event):
    """键盘：回车/s = 急停（同时打断说话）；a = 打断说话；t <文字> = 发文本调试；q = 退出。"""

    def worker():
        print("⌨️  回车或 s=急停 · a=打断说话 · t 文字=发文本 · q=退出")
        for line in sys.stdin:
            line = line.strip()
            if line in ("", "s", "stop", "停"):
                asyncio.run_coroutine_threadsafe(tank.stop(), loop)
                asyncio.run_coroutine_threadsafe(session.abort(), loop)
                print("🛑 急停")
            elif line == "a":
                asyncio.run_coroutine_threadsafe(session.abort(), loop)
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
    photo = None
    if args.camera:
        from .camera import PhotoTool, make_camera

        from .seek import Seeker

        photo = PhotoTool(make_camera(args.camera), mcp, ident)
        # say 要用到后面才创建的 session，所以包一层，调用时再取
        seeker = Seeker(tank, photo.ask, say=lambda text: session.say(text), view=args.camera_view)
        from .wander import Wanderer

        # 和平台断开时听不到“停”，闲逛先暂停；重连后自动继续
        wanderer = Wanderer(tank, photo.ask, say=lambda text: session.say(text), view=args.camera_view,
                            paused=lambda: session.ws is None)
        if args.marker:
            from .marker_seek import MarkerSeeker

            # 有标记就看标记边开边调；没贴标记时 MarkerSeeker 会退回上面的视觉模型版 seeker
            seeker = MarkerSeeker(tank, photo.camera, _make_tracker(args), labels=_marker_labels(args),
                                  ask=photo.ask, say=lambda text: session.say(text), fallback=seeker,
                                  on_photo=lambda *a: photo.on_photo and photo.on_photo(*a))
            print("🔳 标记跟踪：车顶 0 号标记，目标 " + (args.marker_targets or "1–9 号标记或颜色"))
        register_camera_tools(mcp, photo, view=args.camera_view, seeker=seeker, wanderer=wanderer)
        print(f"📷 摄像头：{args.camera}（需在智控台开启视觉分析）")
    else:
        from .tools import register_wander_tool
        from .wander import Wanderer

        # 没开摄像头也能闲逛：闭着眼慢慢逛，说“停”才停
        register_wander_tool(mcp, Wanderer(tank, None, say=lambda text: session.say(text),
                                           paused=lambda: session.ws is None), camera=False)
    session = LinxSession(
        ident,
        mcp,
        get_endpoint,
        audio=not args.no_audio,
        listen_mode=args.mode,
        duplex=args.duplex,
        mic_device=args.mic,
        speaker_device=args.speaker,
        barge_in_level=args.barge_in_level,
    )
    listeners = []
    if not args.no_mood:
        from .tank.mood import Mood

        mood = Mood(tank)
        listeners.append(lambda e: e.get("type") == "emotion" and mood.on_emotion(e.get("emotion")))
    if args.web:
        import webbrowser

        from .web import WebConsole

        console = WebConsole(tank, session, port=args.web_port)
        listeners.append(console.publish)
        if photo:
            import base64

            photo.on_photo = lambda jpeg, q, a: console.publish({
                "type": "photo", "question": q, "answer": a,
                "image": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
            })
        await console.start()
        print(f"🌐 网页控制台：{console.url}")
        webbrowser.open(console.url)
    session.on_event = lambda e: [listen(e) for listen in listeners]
    _report_tank_errors(tank, session, console.publish if args.web else None)
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
        ("左转 90°", lambda: tank.turn("left", 90, args.max_speed)),
        ("右转 90°", lambda: tank.turn("right", 90, args.max_speed)),
        ("炮管抬高 300ms", lambda: tank.barrel("up", 300)),
        ("炮管压低 300ms", lambda: tank.barrel("down", 300)),
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


def _add_marker_args(p):
    p.add_argument("--marker-size-cm", type=float, default=6.0, help="打印出来的标记黑框边长（厘米，打印后量一下）")
    p.add_argument("--marker-targets", help="目标标记叫什么，例如 “1=绿色瓶子,2=红色盒子”；没登记的目标按颜色认")
    p.add_argument("--car-height-cm", type=float, default=7.0, help="车顶标记离地高度（厘米）")
    p.add_argument("--focal-ratio", type=float, default=0.8, help="摄像头焦距 ÷ 画面宽度（不标定时的估计值）")


def _make_tracker(args):
    from .marker import MarkerTracker

    return MarkerTracker(args.marker_size_cm, args.car_height_cm, args.focal_ratio)


def _marker_labels(args):
    from .marker import parse_labels

    return parse_labels(args.marker_targets)


def cmd_marker_test(args):
    """实时打印“目标在车头左/右几度、多远”，每秒存一张标注图，用来检查标记贴得对不对。"""
    from .camera import _encode, make_camera
    from .marker import CAR_ID, detection_report, resolve_target

    cam, tracker = make_camera(args.camera), _make_tracker(args)
    target = resolve_target(args.target, _marker_labels(args))
    print(f"🔳 目标：{args.target}（{target.kind} {target.marker_id or target.color or ''}），Ctrl+C 退出；标注图存到 {args.out}")
    last = 0.0
    try:
        while True:
            frame = cam.frame()
            s = tracker.locate(frame, target)
            if s.car and s.target:
                info = f"目标在车头{'左' if s.bearing > 0 else '右'} {abs(s.bearing):5.1f}°  距离 {s.distance:5.1f} cm"
                if s.target_tilt is not None:
                    info += f"  目标倾角 {s.target_tilt:4.0f}°"
                if abs(s.bearing) > 150:
                    info += "  ←炮管正对着目标却显示 180° 左右？0 号标记贴反了"
            else:
                info = "看不到车顶 0 号标记" if not s.car else "看到车了，看不到目标"
            want = (CAR_ID,) + ((target.marker_id,) if target.marker_id else ())
            info += f"  [{frame.shape[1]}×{frame.shape[0]} · {detection_report(s.corners or {}, want)}]"
            print("\r" + info.ljust(90), end="", flush=True)
            if time.monotonic() - last > 1:
                last = time.monotonic()
                with open(args.out, "wb") as f:
                    f.write(_encode(tracker.annotate(frame, s)))
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()


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
    p_run.add_argument("--duplex", action="store_true", help="全双工：小车说话时也能说话打断它（外放也可以：会自动扣掉自己的回声，戴耳机最稳）")
    p_run.add_argument("--barge-in-level", type=int, default=2000,
                       help="全双工下插话的最低音量（16 位采样 RMS，越小越灵敏）；0 = 不做回声判断、麦克风原样上传（只适合戴耳机）")
    p_run.add_argument("--mic", help="麦克风设备名或序号（见 list-audio）")
    p_run.add_argument("--speaker", help="扬声器设备名或序号")
    p_run.add_argument("--ws-url", help="覆盖 WebSocket 地址")
    p_run.add_argument("--token", help="直接指定 token，跳过 OTA")
    p_run.add_argument("--no-mood", action="store_true", help="关闭情绪肢体语言（回复时的小动作和车灯）")
    p_run.add_argument("--web", action="store_true", help="同时打开网页控制台（对话记录、急停、按住说话、手动控车）")
    p_run.add_argument("--web-port", type=int, default=8080, help="网页控制台端口")
    p_run.add_argument("--camera", help="开启拍照工具：0=电脑摄像头序号（0:1920x1440 = 指定分辨率），http://…=JPEG 快照地址，其他=图片文件")
    p_run.add_argument("--camera-view", choices=["overhead", "front"], default="overhead",
                       help="overhead=摄像头固定在场地旁边（俯拍或斜拍，越高越好），front=摄像头装在车上朝前")

    p_run.add_argument("--marker", action="store_true",
                       help="用 ArUco 标记边开边调（车顶贴 0 号标记，见 marker-print）：摄像头可以固定，也可以手拿着跟拍")
    _add_marker_args(p_run)

    p_mp = sub.add_parser("marker-print", help="生成可打印的 ArUco 标记（SVG，按实际大小打印）")
    p_mp.add_argument("--marker-size-cm", type=float, default=6.0, help="黑框边长（厘米）")
    p_mp.add_argument("--out", default="markers.svg")

    p_mt = sub.add_parser("marker-test", help="实时显示标记算出的方位和距离（不连平台、不动车）")
    p_mt.add_argument("--camera", default="0", help="同 run --camera")
    p_mt.add_argument("--target", default="1号", help="要找的目标，如 “1号”“绿色瓶子”")
    p_mt.add_argument("--out", default="marker-test.jpg", help="每秒存一张标注图")
    _add_marker_args(p_mt)

    p_cam = sub.add_parser("camera-test", help="拍一张照片存成文件，检查摄像头（不连平台）")
    p_cam.add_argument("--camera", default="0", help="同 run --camera")
    p_cam.add_argument("--out", default="camera-test.jpg", help="保存路径")

    p_test = sub.add_parser("tank-test", help="逐步测试小车动作（先把车架空！）")
    _add_tank_args(p_test)

    sub.add_parser("list-audio", help="列出音频设备")

    p_wifi = sub.add_parser("wifi-setup", help="给车装 Wi-Fi 控车程序（默认 USB，--host 走无线；--uninstall 恢复原厂）")
    p_wifi.add_argument("--port", help="车的串口")
    p_wifi.add_argument("--host", help="车的 IP，走 Wi-Fi 维护通道而不是 USB；热点模式下是 192.168.4.1")
    p_wifi.add_argument("--code-only", action="store_true", help="只更新车上的程序，不改 Wi-Fi 配置")
    p_wifi.add_argument("--ssid", default="CMCC-7GE2", help="2.4G Wi-Fi 名称")
    p_wifi.add_argument("--uninstall", action="store_true", help="恢复原厂 boot.py，删除 Wi-Fi 配置")

    p_exec = sub.add_parser("car-exec", help="在车上执行一段代码并打印输出（调试用）")
    p_exec.add_argument("code", help="MicroPython 代码，- 表示从标准输入读")
    p_exec.add_argument("--port", help="车的串口")
    p_exec.add_argument("--host", help="车的 IP，走 Wi-Fi 维护通道")
    p_exec.add_argument("--reset", action="store_true", help="执行完重启车（Wi-Fi 模式下维护会话结束后车本来就会继续接收指令）")

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
    elif args.cmd == "camera-test":
        from .camera import make_camera

        jpeg = make_camera(args.camera).snapshot()
        with open(args.out, "wb") as f:
            f.write(jpeg)
        print(f"📷 已保存 {args.out}（{len(jpeg) // 1024} KB）")
    elif args.cmd == "marker-print":
        from .marker import marker_svg

        with open(args.out, "w", encoding="utf-8") as f:
            f.write(marker_svg(args.marker_size_cm))
        print(f"🔳 已生成 {args.out}：用浏览器打开，打印时选“实际大小/100%”。0 号贴车顶、箭头朝车头；1–3 号贴目标")
    elif args.cmd == "marker-test":
        cmd_marker_test(args)
    elif args.cmd == "car-exec":
        cmd_car_exec(args)
    elif args.cmd == "wifi-setup":
        cmd_wifi_setup(args)
    elif args.cmd == "list-audio":
        import sounddevice as sd

        print(sd.query_devices())


if __name__ == "__main__":
    main()
