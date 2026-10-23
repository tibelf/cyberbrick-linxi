"""灵矽 OTA 上报：拿激活码（未绑定）或 WebSocket 地址 + token（已绑定）。

协议见 Xrobot-docs/docs/xrobot/platform/OTA.md。
"""

import json
import time
import urllib.request

from . import __version__
from .identity import Identity

OTA_URL = "https://xrobo.qiniuapi.com/v1/ota/"
APP_NAME = "cyberbrick-voice"


def report(ident: Identity, url: str = OTA_URL, timeout: float = 10) -> dict:
    body = {
        "version": 0,
        "uuid": ident.client_id,
        "application": {
            "name": APP_NAME,
            "version": __version__,
            "elf_sha256": "0" * 64,
        },
        "ota": {"label": APP_NAME},
        "board": {"type": APP_NAME, "ssid": "", "rssi": 0, "mac": ident.mac},
        "flash_size": 0,
        "mac_address": ident.mac,
        "chip_model_name": "",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Activation-Version": "1",
            "Device-Id": ident.mac,
            "Client-Id": ident.client_id,
            "User-Agent": f"{APP_NAME}/{__version__}",
            "Accept-Language": "zh-CN",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"OTA 上报失败: {data['error']}")
    return data


def wait_for_binding(ident: Identity, poll_s: float = 5, url: str = OTA_URL) -> dict:
    """阻塞直到设备在智控台绑定完成，返回 {"url": ..., "token": ...}。"""
    shown = None
    while True:
        data = report(ident, url)
        ws = data.get("websocket") or {}
        if ws.get("token"):
            return ws
        code = (data.get("activation") or {}).get("code")
        if code and code != shown:
            print("=" * 48)
            print(f"  设备未绑定。请登录 https://xrobo.qiniu.com/")
            print(f"  在智能体 → 设备管理 → 添加设备，输入激活码：{code}")
            print(f"  （Device-Id: {ident.mac}）")
            print("=" * 48)
            shown = code
        time.sleep(poll_s)
