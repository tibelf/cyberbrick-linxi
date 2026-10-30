"""云端模拟小车使用的灵矽 OTA 接入；身份文件由调用方保存在私有目录。"""

from urllib.parse import urlsplit, urlunsplit

from . import ota


def make_cloud_endpoint(ident, on_activation):
    """每次连接前重新取地址；不打印或返回设备身份。"""
    def endpoint():
        data = ota.report(ident)
        info = data.get("websocket") or {}
        url, token = info.get("url"), info.get("token")
        if not url or not token:
            code = str((data.get("activation") or {}).get("code") or "")
            if len(code) == 6 and code.isdigit():
                on_activation(code)
            raise RuntimeError("云端设备尚未绑定，请在网页查看激活码")
        parts = urlsplit(url)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise RuntimeError("灵矽返回的连接地址格式不受支持")
        if parts.scheme == "ws" and parts.hostname == "xrobo-io.qiniuapi.com":
            url = urlunsplit(("wss", parts.netloc, parts.path, "", ""))
        elif parts.scheme != "wss":
            raise RuntimeError("拒绝使用未加密的灵矽连接地址")
        return url, str(token)
    return endpoint
