"""实车云端连接探测：只认证并收发心跳，不导入电机驱动。"""

import gc
import hashlib
import json
import os
import socket
import ssl
import time
import ubinascii

SERVER_IP = '110.40.227.30'
SERVER_NAME = 'cyberbrick-device.invalid'
GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


def _write_all(stream, data):
    pos = 0
    while pos < len(data):
        n = stream.write(data[pos:])
        if n is None or n <= 0:
            raise OSError('发送中断')
        pos += n


def _read_exact(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        data = stream.read(size - len(chunks))
        if not data:
            raise OSError('连接中断')
        chunks.extend(data)
    return bytes(chunks)


def _send_frame(stream, text, opcode=1):
    data = text.encode() if isinstance(text, str) else text
    if len(data) > 125:
        raise ValueError('检测消息过长')
    mask = os.urandom(4)
    frame = bytearray((0x80 | opcode, 0x80 | len(data)))
    frame.extend(mask)
    frame.extend(bytes(v ^ mask[i % 4] for i, v in enumerate(data)))
    _write_all(stream, frame)


def _read_frame(stream):
    head = _read_exact(stream, 2)
    if not head[0] & 0x80 or head[0] & 0x70 or head[1] & 0x80:
        raise ValueError('无效的服务端数据帧')
    opcode = head[0] & 0x0f
    length = head[1] & 0x7f
    if length == 126:
        extra = _read_exact(stream, 2)
        length = extra[0] * 256 + extra[1]
    elif length == 127:
        raise ValueError('服务端数据帧过长')
    if length > 512:
        raise ValueError('服务端数据帧过长')
    return opcode, _read_exact(stream, length)


def _read_message(stream):
    while True:
        opcode, data = _read_frame(stream)
        if opcode == 1:
            return json.loads(data)
        if opcode == 9:  # WebSocket ping
            _send_frame(stream, data, opcode=10)
        elif opcode == 8:
            raise OSError('服务端关闭连接')
        else:
            raise ValueError('不支持的服务端数据帧')


def _websocket_upgrade(stream, server_name=SERVER_NAME):
    key = ubinascii.b2a_base64(os.urandom(16)).strip()
    request = (b'GET /device HTTP/1.1\r\nHost: ' + server_name.encode()
               + b'\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: '
               + key + b'\r\nSec-WebSocket-Version: 13\r\n\r\n')
    _write_all(stream, request)
    status = stream.readline()
    if not status.startswith(b'HTTP/1.1 101 '):
        raise OSError('WebSocket 升级失败')
    expected = ubinascii.b2a_base64(hashlib.sha1(key + GUID).digest()).strip()
    accepted = None
    for _ in range(32):
        line = stream.readline()
        if not line or len(line) > 512:
            raise OSError('WebSocket 响应头无效')
        if line == b'\r\n':
            break
        name, sep, value = line.partition(b':')
        if sep and name.strip().lower() == b'sec-websocket-accept':
            accepted = value.strip()
    else:
        raise OSError('WebSocket 响应头过长')
    if accepted != expected:
        raise ValueError('WebSocket 响应校验失败')


def open_link(ca, token, server_ip=SERVER_IP, server_name=SERVER_NAME, capabilities=None):
    """校验证书、升级 WebSocket 并认证；调用者负责关闭返回的连接。"""
    gc.collect()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=ca)
    raw = socket.socket()
    stream = None
    try:
        raw.settimeout(3)
        raw.connect((server_ip, 443))
        stream = ctx.wrap_socket(raw, server_hostname=server_name)
        _websocket_upgrade(stream, server_name)
        auth = {'type': 'auth', 'token': token}
        if capabilities:
            auth['capabilities'] = capabilities
        _send_frame(stream, json.dumps(auth))
        ready = _read_message(stream)
        if ready.get('type') != 'ready':
            raise ValueError('设备认证失败')
        return stream
    except Exception:
        if stream:
            stream.close()
        else:
            raw.close()
        raise


def run(ca, token):
    """调用前连接 Wi-Fi；结束后关闭套接字。不会改变车端配置。"""
    import network
    import ntptime

    if not network.WLAN(network.STA_IF).isconnected():
        raise OSError('小车尚未连接 Wi-Fi')
    ntptime.settime()
    print('网络时间同步成功')
    stream = open_link(ca, token)
    try:
        print('服务器证书验证通过，小车已通过云端认证')
        for i in range(5):
            _send_frame(stream, json.dumps({'type': 'ping', 'id': i}))
            pong = _read_message(stream)
            if pong.get('type') != 'pong' or pong.get('id') != i:
                raise ValueError('心跳应答不匹配')
            print('云端心跳 %d/5 成功' % (i + 1))
            time.sleep_ms(500)
    finally:
        stream.close()
        gc.collect()
