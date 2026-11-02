# ESP32-CAM 摄像头

为车载视觉准备的独立摄像头程序。使用 AI-Thinker ESP32-CAM 常见引脚，不操作小车电机或发射器。

已在用户的 ESP32-CAM 实机验证：识别到传感器 PID `0x3660`（OV3660）、检测到 PSRAM，并通过 USB 读回 640×480 JPEG。画面方向、色彩、局域网和云端视频仍需进一步验证，不能仅凭 USB 拍照成功视为整车方案完成。

## 文件

- `esp32-camera.ino`：USB 状态与拍照、串口配网、局域网 JPEG 快照和 MJPEG 画面。
- `camera_client.py`：通过明确指定的串口进行测试和配网，支持 macOS 本机输入窗口。
- `test_camera_client.py`：不依赖硬件的串口协议测试。

运行测试：

```bash
python -m unittest discover -s esp32-camera -p 'test_*.py' -v
```

## 构建和写入

已使用 Arduino ESP32 3.3.12，开发板 `esp32:esp32:esp32cam:PartitionScheme=huge_app` 编译。真实硬件的分区表、启动器、芯片安全状态和可恢复备份必须先核对；不能将本程序刷入 CyberBrick Core 或 AI Passport。当前设备此前的应用分区位于 `0x10000`、大小 `0x300000`，本次只写入此应用区并由设备验证，保留了原 bootloader、分区表和 NVS。不同设备不可直接沿用这一偏移。

原始闪存备份可能包含敏感信息，放在用户私有目录中，不得提交仓库或公开。备份不包含 eFuse，恢复流程也还没有实测。

## USB 测试和配网

安装 `pyserial` 后，明确指定摄像头串口，不能自动扫描并误操作其他设备：

```bash
python esp32-camera/camera_client.py --port <摄像头串口> status
python esp32-camera/camera_client.py --port <摄像头串口> capture --out <新照片路径>
python esp32-camera/camera_client.py --port <摄像头串口> wifi --dialog
```

`wifi --dialog` 仅在 macOS 使用本机输入框，密码经 USB 发送，不放在命令行参数或仓库里；不加 `--dialog` 可在终端输入并隐藏密码。板子仅支持 2.4 GHz Wi-Fi。连接成功后保存名称和密码以便下次自动连接；此设备目前没有启用闪存加密，能物理访问设备的人可能读取这些配置。

配网后，局域网首页为 `http://<摄像头IP>/`，快照为 `http://<摄像头IP>/capture`，实时画面为 `http://<摄像头IP>:81/stream`。新版首页可调白平衡、亮度、饱和度和水平/垂直翻转；设置写入摄像头并在启动时恢复。**当前 HTTP 接口没有鉴权和 HTTPS，只能用于可信局域网，不要将端口直接暴露到公网。** 需要手机和摄像头网络互通；将局域网 IP 填到云服务器并不能自动打通互联网视频，也不能直接用于 HTTPS 页面。
