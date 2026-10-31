"""通过指定串口测试 ESP32-CAM；配网信息只在本机输入，不写入仓库。"""

import argparse
import getpass
import ipaddress
import re
import subprocess
import time
from pathlib import Path

import serial

MAX_JPEG_BYTES = 512 * 1024


class CameraClient:
    def __init__(self, connection):
        self.connection = connection

    def _await_line(self, prefix, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.connection.readline(256).strip()
            if line.startswith(b"ERROR "):
                raise RuntimeError(line.decode("ascii", "replace"))
            if line.startswith(prefix):
                return line
        raise TimeoutError("摄像头没有及时响应")

    def status(self):
        self.connection.reset_input_buffer()
        self.connection.write(b"STATUS\n")
        return self._await_line(b"STATUS ", 10).decode("ascii", "replace")

    def capture(self):
        self.connection.reset_input_buffer()
        self.connection.write(b"CAPTURE\n")
        line = self._await_line(b"FRAME ", 10)
        match = re.fullmatch(rb"FRAME ([0-9]+)", line)
        size = int(match.group(1)) if match else 0
        if size < 4 or size > MAX_JPEG_BYTES:
            raise ValueError("摄像头返回的图片长度不合理")
        deadline = time.monotonic() + 90
        image = bytearray()
        while len(image) < size and time.monotonic() < deadline:
            image.extend(self.connection.read(min(4096, size - len(image))))
        if len(image) != size:
            raise TimeoutError("摄像头图片传输不完整")
        if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
            raise ValueError("摄像头返回的图片不是完整 JPEG")
        return bytes(image)

    def configure_wifi(self, ssid, password):
        network = ssid.encode("utf-8")
        secret = password.encode("utf-8")
        if not 1 <= len(network) <= 32 or len(secret) > 63:
            raise ValueError("Wi-Fi 名称需为 1–32 字节，密码不超过 63 字节")
        self.connection.reset_input_buffer()
        self.connection.write(f"WIFI {len(network)} {len(secret)}\n".encode("ascii"))
        self._await_line(b"WIFI_READY", 10)
        self.connection.write(network + secret)
        line = self._await_line(b"WIFI_OK ", 35)
        ip = line.decode("ascii", "strict").split(" ", 1)[1]
        return str(ipaddress.IPv4Address(ip))


def ask_mac_dialog(prompt, hidden=False):
    """显示本机输入窗口；凭据仅通过进程管道返回，不传命令行。"""
    script = (
        'set resultText to text returned of (display dialog '
        + repr(prompt).replace("'", '"')
        + ' default answer ""'
        + (" with hidden answer" if hidden else "")
        + ')\nreturn resultText'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("已取消输入，或无法打开本机输入窗口")
    return result.stdout.rstrip("\r\n")


def main():
    parser = argparse.ArgumentParser(description="通过 USB 测试或配置 ESP32-CAM")
    parser.add_argument("--port", required=True, help="摄像头串口，需先确认不是小车主控")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="查看摄像头状态")
    capture = sub.add_parser("capture", help="通过 USB 拍一张照片")
    capture.add_argument("--out", required=True, help="保存 JPEG 的本机路径")
    wifi = sub.add_parser("wifi", help="在本机输入 Wi-Fi 名称和密码")
    wifi.add_argument("--dialog", action="store_true", help="使用 macOS 输入窗口")
    args = parser.parse_args()

    if args.action == "wifi":
        if args.dialog:
            ssid = ask_mac_dialog("请输入 2.4 GHz Wi-Fi 名称")
            password = ask_mac_dialog("请输入 Wi-Fi 密码，密码不会显示", hidden=True)
        else:
            ssid = input("2.4 GHz Wi-Fi 名称：")
            password = getpass.getpass("Wi-Fi 密码（输入时不显示）：")

    with serial.Serial(args.port, 115200, timeout=1, write_timeout=10) as connection:
        time.sleep(2)
        camera = CameraClient(connection)
        if args.action == "status":
            print(camera.status())
        elif args.action == "capture":
            jpeg = camera.capture()
            path = Path(args.out)
            with path.open("xb") as output:
                output.write(jpeg)
            print(f"照片已保存：{path}（{len(jpeg)} 字节）")
        else:
            ip = camera.configure_wifi(ssid, password)
            print(f"摄像头已连接 Wi-Fi： http://{ip}/")


if __name__ == "__main__":
    main()
