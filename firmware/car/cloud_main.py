"""车主动连接云端；断线停车，拒绝过期指令，发射始终锁定。"""

import gc
import json
import network
import time

import cloud_probe
import wifi_main

MAX_RTT_MS = 800
MAX_FAILSAFE_MS = 5300
MAX_TRACK_SPEED = 40  # 首次云端实车验证先限速；两端都限制
MAX_TURRET_SPEED = 60


def _is_int(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _apply(message, elapsed_ms, driver=wifi_main):
    """仅接受白名单动作；设备端再次校验参数并先启动硬件看门狗。"""
    op = message.get('op')
    if op == 'halt':  # 停车命令即便迟到仍然安全
        driver.halt()
        return True
    if elapsed_ms < 0 or elapsed_ms > MAX_RTT_MS:
        return False
    if op == 'drive':
        l, r, ms = message.get('l'), message.get('r'), message.get('ms')
        if not (_is_int(l, -MAX_TRACK_SPEED, MAX_TRACK_SPEED)
                and _is_int(r, -MAX_TRACK_SPEED, MAX_TRACK_SPEED)
                and _is_int(ms, 1, MAX_FAILSAFE_MS)):
            return False
        command = 'drive %d %d %d' % (l, r, ms)
    elif op == 'turret':
        speed, ms = message.get('speed'), message.get('ms')
        if not (_is_int(speed, -MAX_TURRET_SPEED, MAX_TURRET_SPEED)
                and _is_int(ms, 1, MAX_FAILSAFE_MS)):
            return False
        command = 'turret %d %d' % (speed, ms)
    elif op == 'led':
        r, g, b = message.get('r'), message.get('g'), message.get('b')
        if not all(_is_int(v, 0, 255) for v in (r, g, b)):
            return False
        try:
            return driver.handle('led %d %d %d' % (r, g, b)) == 'ok'
        except Exception:
            driver.halt()
            return False
    else:
        return False  # 包括 fire；设备端也不允许发射
    try:
        driver._arm(ms)
        if driver.handle(command) != 'ok':
            driver.halt()
            return False
        return True
    except Exception:
        driver.halt()
        return False


def _connect_wifi():
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        return True
    ap = network.WLAN(network.AP_IF)
    if ap.active():
        ap.active(False)
    sta.active(True)
    with open('/wifi.json') as f:
        cfg = json.load(f)
    for item in cfg.get('networks', []):
        try:
            sta.connect(item['ssid'], item['password'])
            start = time.ticks_ms()
            while not sta.isconnected() and time.ticks_diff(time.ticks_ms(), start) < 15000:
                time.sleep_ms(200)
            if sta.isconnected():
                try:
                    sta.config(pm=sta.PM_NONE)
                except Exception:
                    pass
                return True
            sta.disconnect()
        except Exception:
            pass
    return False


def _session(stream):
    nonce = 0
    last_id = 0
    while nonce < 2**31 - 1:
        nonce += 1
        started = time.ticks_ms()
        cloud_probe._send_frame(stream, json.dumps({'type': 'poll', 'nonce': nonce}))
        msg = cloud_probe._read_message(stream)
        elapsed = time.ticks_diff(time.ticks_ms(), started)
        if not isinstance(msg, dict) or msg.get('nonce') != nonce:
            raise ValueError('云端应答编号不匹配')
        if msg.get('type') == 'cmd':
            number = msg.get('id')
            if not _is_int(number, last_id + 1, 2**31 - 1):
                raise ValueError('云端动作编号无效')
            last_id = number
            ok = _apply(msg, elapsed)
            cloud_probe._send_frame(stream, json.dumps({'type': 'ack', 'id': number, 'ok': ok}))
        elif msg.get('type') != 'noop':
            raise ValueError('云端应答类型无效')
        time.sleep_ms(150)


def main():
    """读取车端私有配置并持续重连；每次失联先停车。"""
    import ntptime

    wifi_main.halt()
    with open('/cloud.json') as f:
        cfg = json.load(f)
    with open('/cloud-ca.pem', 'rb') as f:
        ca = f.read()
    token = cfg['token']
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('云端设备密码无效')
    while True:
        stream = None
        try:
            if not _connect_wifi():
                raise OSError('Wi-Fi 未连接')
            ntptime.settime()
            stream = cloud_probe.open_link(ca, token, cfg['ip'], cfg['server_name'])
            print('[云端] 已连接')
            _session(stream)
        except Exception as e:
            print('[云端] 连接中断:', type(e).__name__)
        finally:
            wifi_main.halt()
            if stream:
                stream.close()
            gc.collect()
        time.sleep_ms(2000)
