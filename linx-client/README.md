# linx-client

CyberBrick 的 Python 控制服务：承载**灵矽（Linx）设备会话**，把语音工具调用和手动操作交给同一个动作执行器，再控制履带、炮台和扳机。

| 模式 | 入口 | 音频与控车链路 |
|---|---|---|
| **云端（当前主线）** | `python -m linx_client.cloud_demo` | 手机 App / 网页收放音；小车经 Wi-Fi / 热点主动 WSS 连云，现场无需电脑 |
| 电脑 / 局域网 | `python -m linx_client run` | 电脑收放音；通过 UDP 或 USB 控车，可启用视觉、宏、闲逛等扩展 |

`cloud_demo` 沿用早期模块名，现已支持显式开启实车模式；不传 `--real-car` 时仍只操作模拟车。日常手机操作见 [项目首页](../README.md#快速开始) 和 [小车直连云端](../docs/云端直连实车.md)。

## 安装

需要 Python ≥ 3.11。以下命令在运行服务的机器上执行；云端模式不需要服务器麦克风或声卡。

```bash
cd linx-client
python -m venv .venv
source .venv/bin/activate  # Windows：.venv\Scripts\activate
pip install -r requirements.txt
```

## 云端模式（当前主线）

手机原生 App 和 HTTPS 网页共用 `/control`，上传音频、接收回复及发送手动控制。`cloud_audio.py` 把手机音频接入灵矽会话；`cloud_car.py` 接收小车主动建立的 `/device` 连接。两个服务仅监听服务器回环地址，由 HTTPS/WSS 代理提供公网入口。

### 配置与启动

1. 在[灵矽智控台](https://xrobo.qiniu.com/)创建智能体，意图识别选择 **Function Call**，角色提示词可参考 [`prompts/agent-prompt.md`](prompts/agent-prompt.md)。云端目前注册基础小车工具，包含移动、转向、停车、炮管、发射、跳舞及人格；宏、闲逛和视觉工具尚未注册到云端入口。
2. 在服务器私有环境文件中设置 `CAR_DEMO_TOKEN`（手机控制密码）和独立的 `CAR_DEVICE_TOKEN`（小车认证口令），两者均须为至少 32 个字符的随机值。身份文件和口令不进入仓库、APK 或公开日志。
3. 配置 HTTPS 代理的手机入口和独立车端 TLS/WSS 入口。手机入口包含网页认证及 `/control` 转发，车端入口将 `/device` 转发到 `127.0.0.1:18767`。参考 [部署模板](../deploy/cloud-demo/) 和 [现有实车部署说明](../docs/云端直连实车.md)；模板仍以早期模拟服务为基础，不包含完整实车设备入口。
4. 由私有环境文件加载上述口令，并准备当前服务用户可写的身份目录后，启动应用。将示例域名及状态路径替换为自己的配置：

   ```bash
   python -m linx_client.cloud_demo \
     --https-origin https://car.example.com \
     --linx-state /var/lib/cyberbrick-cloud-demo/identity.json \
     --real-car
   ```

   尚未绑定时，手机页面显示 6 位激活码，在智能体的「设备管理」添加设备并绑定。相同灵矽身份只能运行一个会话，不能同时启动它的另一份云端或电脑副本。
5. 通过 USB 为小车安装云端程序及网络配置：`boot.py`、`wifi_main.py`、`cloud_main.py`、`cloud_probe.py`，以及私有的 `/wifi.json`、`/cloud.json`、`/cloud-ca.pem`。车端校验 CA 和服务名并通过 NTP 校时，设备口令须与服务器一致。具体启动与恢复行为见 [小车直连云端](../docs/云端直连实车.md)。这是需要单独完成的配置步骤；`wifi-setup` 只安装局域网方案，仓库暂无完整的一键云端安装器。

车端配置好后，打开已保存的 2.4 GHz 网络和小车电源即可尝试自动上线。手机选择 App 或网页连接，同一时刻只允许一个控制端。操作方法及 APK 构建见 [Android 工程](../android-app/README.md)。

### 云端参数

| 参数 / 环境变量 | 说明 |
|---|---|
| `CAR_DEMO_TOKEN` | 手机控制密码；必填 |
| `CAR_DEVICE_TOKEN` | 独立车端口令；`--real-car` 模式必填 |
| `--port 18765` | 手机网页与 `/control` 的回环监听端口 |
| `--https-origin https://car.example.com` | 允许的 HTTPS 网页来源，须与代理入口一致 |
| `--linx-state <私有路径>` | 灵矽身份文件；不设置则不连接灵矽，仅保留手动控制 |
| `--real-car` | 启用实车网关；不设置则使用 MockBackend |
| `--car-port 18767` | `/device` 的回环监听端口 |
| `--allow-fire` | 允许当前控制者启用发射；还需车端 `fire-v1` 能力，每次连接仍默认锁定 |

云端当前限速 40；手机租约、过期指令拒绝及车端看门狗共同约束动作。Android 摇杆发送连续差速，云端仅保留最新未处理值，松手取消旧值并停车。语音使用点击开始 / 再次点击结束和半双工收放音；当前云端下行支持 PCM，不能直接套用电脑模式的 Opus / `--duplex` 设置。

不接实车的本地验证及早期部署记录见 [云端模拟验证](../docs/云端模拟验证.md)。

## 电脑模式（局域网与开发调试）

本节需要带麦克风、扬声器的电脑。UDP 模式要求电脑与车在同一局域网，串口模式通过 USB 数据线连接 Core；mock 联调不需小车。完整装车步骤见 [电脑模式运行说明](../docs/运行说明.md)。

### 第 1 步：配置智能体

1. **意图识别**选「意图识别与聊天使用同一模型（Function Call）」，所选模型需支持工具调用。不开这一项，大模型不会调用小车工具。
2. **角色介绍**粘贴 [`prompts/agent-prompt.md`](prompts/agent-prompt.md) 的内容。
3. 保存配置。程序收到 `config_updated` 会主动重连；若未收到通知，可重启本程序。

### 第 2 步：激活虚拟设备

```bash
python -m linx_client activate
```

首次运行会生成虚拟 MAC，保存在 `.linx_device.json`（别删，删了就是一台新设备），并打印 6 位激活码。到智控台「智能体 → 设备管理 → 添加设备」输入这个码；程序检测到绑定成功后会自动退出。

### 第 3 步：先用 mock 联调（不接车）

```bash
python -m linx_client run --backend mock --max-speed 40
```

对着麦克风说「往前走两秒然后开一炮」，终端应该依次出现：

```
🗣  你：往前走两秒然后开一炮
🔧 调用 self.tank.move {"direction": "forward", "duration_ms": 2000}
[mock-tank] drive(40, 40, 2300)
🔧 调用 self.tank.fire {"shots": 1}
[mock-tank] fire(500,)
🤖 小车：好嘞，冲两秒再来一发！
```

不方便说话时，可以在终端输入 `t 往前走两秒`，直接发文本测试。

### 第 4 步：接车

> ⚠️ **先把车架空、履带悬空**，发射口不要对着人。

```bash
# 逐步测试每个动作，确认方向和引脚都对
python -m linx_client tank-test --backend serial            # 自动找串口
python -m linx_client tank-test --backend serial --port COM3

# 正式运行
python -m linx_client run --backend serial --max-speed 40

# 已安装局域网固件时，通过 Wi-Fi 控车并打开电脑控制台
python -m linx_client run --backend udp --web --max-speed 40
```

- 接线按车上 `rc_config` 确认：履带 MOTOR1 Pin4/5、MOTOR2 Pin6/7（反装），炮管 SERVO1 Pin3，扳机 SERVO2 Pin2（86° 松开 / 150° 扣下），驱动调用车上的原厂模块。运动方向仍需架空核对。
- USB raw REPL 会打断车上当前程序。复位后按已安装的启动配置进入云端、局域网或原厂模式；装有本仓库启动程序时，开机按住 Pin9 板载按键可进入原厂遥控。
- 车上的 MicroPython 有看门狗（`machine.Timer`）：每个动作都会在「时长 + 300ms」后自动停车，就算电脑死机或 USB 被拔掉，车也不会一直跑。

### 电脑模式常用参数

| 参数 | 说明 |
|---|---|
| `--backend mock\|serial\|udp` | mock 只打印；serial 通过 USB 控车；udp 使用局域网 Wi-Fi |
| `--host / --port` | 指定局域网小车 IP / USB 串口 |
| `--web / --pad` | 电脑控制台 / 局域网手机手柄；与云端 `/control` 是不同入口 |
| `--max-speed 40` | 速度上限，大模型给的速度会被裁剪到这个值 |
| `--no-fire` | 锁定发射，演示前排练时用 |
| `--no-audio` | 不开声卡，只调试工具调用（配合 `t 文字`） |
| `--duplex` | 全双工。默认是半双工：播报时不上传麦克风，防止外放的声音打断自己 |
| `--mic / --speaker` | 指定音频设备，用 `python -m linx_client list-audio` 查看 |
| `--token / --ws-url` | 跳过 OTA，手动指定 |

终端快捷键：**直接回车 = 急停**，`t 文字` = 发文本，`q` = 退出。

## 执行约束

- 单个动作 ≤ 5s，排队的动作总时长 ≤ 15s
- 「停」不进队列，会立即清空队列并停车。语音和手动停止共用此逻辑；电脑模式另可用回车触发
- 发射：每次最多 3 发，脉冲后冷却 1.5s；电脑模式用 `--no-fire` 锁定，云端需 `--allow-fire` 及当前控制者启用
- 设备端看门狗兜底

## 开发

```bash
pip install -r requirements-dev.txt opencv-python-headless
python -m pytest
node --test tests/test_cloud_voice_ui.mjs
```

测试覆盖 MCP 会话、动作队列、云端认证和租约、模拟车网关、手机音频、网页录音状态及 raw REPL 解析。测试使用本机假平台与假硬件，无需真实设备或外网。完整视觉相关测试另需安装 `opencv-python-headless`（CI 采用此依赖）；Android 测试在其独立工程运行。

## 代码结构

```
linx_client/
├── cloud_demo.py    # 云端手机入口：认证、租约、手动控制和语音
├── cloud_audio.py   # 手机 PCM 与灵矽音频适配
├── cloud_linx.py    # 云端 OTA、绑定和加密平台连接
├── cloud_car.py     # 实车主动连接的网关与 CloudCarBackend
├── __main__.py      # 电脑命令行：activate / run / wifi-setup 等
├── ota.py           # OTA 上报：激活码、ws 地址和 token
├── identity.py      # 虚拟 MAC + Client-Id
├── session.py       # WebSocket：hello、音频收发、tts/stt、MCP 分发
├── audio.py         # sounddevice 采集和播放（下行 opus 时需要 opuslib）
├── mcp.py           # 设备端 MCP 服务（JSON-RPC 2.0）
├── tools.py         # 小车工具定义（名称、描述、参数）
└── tank/
    ├── executor.py  # 动作队列和安全约束
    ├── backend.py   # 驱动接口 + MockBackend
    ├── udp.py       # 局域网 UDP 驱动
    └── serial_repl.py  # USB raw REPL 驱动（含设备端看门狗）
```

车端程序位于 [`firmware/car/`](../firmware/car/)，原生 App 位于 [`android-app/`](../android-app/)。

## 验证边界

手机网页按键、语音及拔 USB 后控车已获用户确认，炮台和扳机已做空载验证。Android 0.1.0 连接和语音已确认，0.2.1 自动连接及摇杆仍待手机实测；真实断网停车、当前限速下地面行驶和有弹发射仍待验证。详情以 [交接看板](../docs/HANDOFF.md) 为准。
