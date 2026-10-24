# -*-coding:utf-8-*-
"""Wi-Fi 控车模式（运行在车上的 MicroPython）。

依次尝试 /wifi.json 里的 2.4G 网络；都连不上就自己开热点 CyberBrick-Tank（IP 192.168.4.1）。
监听 UDP 4210，只执行白名单指令，不执行任意代码；同时开 WebREPL（端口 8266，带密码）供无线维护。
/wifi.json 格式：{"networks": [{"ssid": ..., "password": ...}, ...], "admin_password": "8-9 位"}
协议：每个 UDP 包一条 ASCII 指令，回一条应答。
  ping                  -> pong
  drive <l> <r> <ms>    l/r: -100..100，正数前进
  turret <sp> <ms>      sp: -100..100，0 为停
  fire <ms>             扣扳机，ms 后看门狗回位
  halt                  -> ok
成功回 ok，失败回 err <原因>。
时长语义与 USB 版 BOOTSTRAP 相同：指令只设状态并启动看门狗，到点自动 halt；
主机掉线或 Wi-Fi 断开时，车会在最后一条指令的 ms 到期后自己停下。
"""

import sys
import time
import json
import socket
import network
from machine import Timer, Pin

if '/bbl' not in sys.path:
    sys.path.append('/bbl')
from motors import MotorsController
from servos import ServosController

PORT = 4210
AP_SSID = 'CyberBrick-Tank'
MAX_MS = 6000
TRIG_REST = 86
TRIG_FIRE = 150

_m = MotorsController()
_s = ServosController()
_wd = Timer(0)


def halt(*_):
    _m.stop(1)
    _m.stop(2)
    _s.stop(1)
    _s.set_angle(2, TRIG_REST)


def _arm(ms):
    _wd.init(mode=Timer.ONE_SHOT, period=ms, callback=halt)


def _clip(v, lo, hi):
    return max(lo, min(hi, int(v)))


def handle(line):
    parts = line.split()
    if not parts:
        return 'err 空指令'
    cmd, args = parts[0], parts[1:]
    try:
        if cmd == 'ping':
            return 'pong'
        if cmd == 'halt':
            halt()
            return 'ok'
        if cmd == 'drive' and len(args) == 3:
            l, r = _clip(args[0], -100, 100), _clip(args[1], -100, 100)
            # 接线见 docs/HANDOFF.md：MOTOR1 = 右履带，MOTOR2 = 左履带（反装）
            _m.set_speed(1, int(r * 20.47))
            _m.set_speed(2, int(-l * 20.47))
            _arm(_clip(args[2], 1, MAX_MS))
            return 'ok'
        if cmd == 'turret' and len(args) == 2:
            _s.set_speed(1, _clip(args[0], -100, 100))
            _arm(_clip(args[1], 1, MAX_MS))
            return 'ok'
        if cmd == 'fire' and len(args) == 1:
            _s.set_angle(2, TRIG_FIRE)
            _arm(_clip(args[0], 1, MAX_MS))
            return 'ok'
    except ValueError:
        return 'err 参数不是整数'
    return 'err 未知指令'


def _led(r, g, b):
    try:
        import neopixel
        np = neopixel.NeoPixel(Pin(8, Pin.OUT), 1)
        np[0] = (r, g, b)
        np.write()
    except Exception:
        pass


def _connect(cfg):
    """返回 (模式, IP)。依次连 networks，每个最多 15s；都失败就开热点。"""
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    for net in cfg.get('networks', []):
        print('[wifi] 连接', net['ssid'])
        sta.connect(net['ssid'], net['password'])
        t0 = time.ticks_ms()
        while not sta.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < 15000:
            time.sleep_ms(200)
        if sta.isconnected():
            return net['ssid'], sta.ifconfig()[0]
        sta.disconnect()
    sta.active(False)
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, password=cfg['admin_password'], authmode=network.AUTH_WPA2_PSK)
    return AP_SSID + '（热点）', ap.ifconfig()[0]


def main():
    halt()
    _led(20, 10, 0)  # 黄：正在连 Wi-Fi
    with open('/wifi.json') as f:
        cfg = json.load(f)
    name, ip = _connect(cfg)
    try:
        import webrepl
        webrepl.start(password=cfg['admin_password'])
    except Exception as e:
        print('[wifi] WebREPL 启动失败', e)
    print('[wifi] 已连接', name, 'IP', ip, 'UDP', PORT)
    if name.startswith(AP_SSID):
        _led(20, 0, 20)  # 紫：自己开了热点
    else:
        _led(0, 0, 20)  # 蓝：已连上 Wi-Fi

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))
    while True:
        data, addr = sock.recvfrom(128)
        try:
            reply = handle(data.decode())
        except Exception as e:  # 指令出错也不能让主循环退出
            halt()
            reply = 'err ' + str(e)
        sock.sendto(reply.encode(), addr)
