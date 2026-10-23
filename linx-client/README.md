# linx-client

把电脑模拟成一台**灵矽（Linx）设备**：用灵矽平台的语音识别、大模型和语音合成听懂指令，再通过 MCP 工具调用控制 CyberBrick 小车（移动、炮塔、发射）。不需要实体灵矽硬件。

```
麦克风 ─PCM 16k─▶ ┌─────────────┐  WebSocket  ┌──────────────┐
扬声器 ◀─TTS───── │ linx-client │ ◀─────────▶ │ 灵矽平台      │
                  │ (本程序)     │  MCP 工具调用 │ ASR/LLM/TTS  │
                  └──────┬──────┘             └──────────────┘
                         │ USB 串口（MicroPython raw REPL）
                         ▼
                  CyberBrick Core（车上的 ESP32-C3）→ 履带 / 炮塔 / 发射电机
```

## 准备

- Python ≥ 3.11，带麦克风和扬声器的电脑（建议戴耳机，或用外接麦克风）
- 灵矽智控台账号：<https://xrobo.qiniu.com/>，并创建一个智能体
- 小车 Core 用 USB 线连到电脑（只用 mock 联调时可以不接）

```bash
cd linx-client
pip install -r requirements.txt
```

## 第 1 步：配置智能体（智控台）

1. **意图识别**选「意图识别与聊天使用同一模型（Function Call）」，所选模型需支持工具调用。不开这一项，大模型不会调用小车工具。
2. **角色介绍**粘贴 [`prompts/agent-prompt.md`](prompts/agent-prompt.md) 的内容。
3. 保存配置。之后每次改配置都要重新运行本程序，让配置生效。

## 第 2 步：激活虚拟设备

```bash
python -m linx_client activate
```

首次运行会生成虚拟 MAC，保存在 `.linx_device.json`（别删，删了就是一台新设备），并打印 6 位激活码。到智控台「智能体 → 设备管理 → 添加设备」输入这个码；程序检测到绑定成功后会自动退出。

## 第 3 步：先用 mock 联调（不接车）

```bash
python -m linx_client run --backend mock
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

不方便说话时，可以在终端输入 `t 往前走两秒`，直接发文本测试。这个功能借用了 `listen.detect` 消息，平台上的效果需要实测确认。

## 第 4 步：接车

> ⚠️ **先把车架空、履带悬空**，发射口不要对着人。

```bash
# 逐步测试每个动作，确认方向和引脚都对
python -m linx_client tank-test --backend serial            # 自动找串口
python -m linx_client tank-test --backend serial --port COM3

# 正式运行
python -m linx_client run --backend serial --max-speed 40
```

- 引脚映射沿用 [unbug/CyberClaw](https://github.com/unbug/CyberClaw)（Mini T）：左履带 Pin2、右履带 Pin4/5、炮塔 Pin3、发射 Pin6/7。**这套映射还没在我们的车上验证。** 如果方向反了，改 `linx_client/tank/serial_repl.py` 里的 `BOOTSTRAP`。
- 程序会打断车上的原厂程序，进入 raw REPL。退出后**按一下 Core 的复位键（或重新上电）**即可恢复原厂遥控。
- 车上的 MicroPython 有看门狗（`machine.Timer`）：每个动作都会在「时长 + 300ms」后自动停车，就算电脑死机或 USB 被拔掉，车也不会一直跑。

## 运行参数

| 参数 | 说明 |
|---|---|
| `--backend mock\|serial` | mock 只打印；serial 通过 USB 控车 |
| `--max-speed 40` | 速度上限，大模型给的速度会被裁剪到这个值 |
| `--no-fire` | 锁定发射，演示前排练时用 |
| `--no-audio` | 不开声卡，只调试工具调用（配合 `t 文字`） |
| `--duplex` | 全双工。默认是半双工：播报时不上传麦克风，防止外放的声音打断自己 |
| `--mic / --speaker` | 指定音频设备，用 `python -m linx_client list-audio` 查看 |
| `--token / --ws-url` | 跳过 OTA，手动指定 |

终端快捷键：**直接回车 = 急停**，`t 文字` = 发文本，`q` = 退出。

## 安全约束（代码里强制执行）

- 单个动作 ≤ 5s，排队的动作总时长 ≤ 15s
- 「停」不进队列，会立即清空队列并停车。语音、键盘回车两条路都能触发
- 发射：每次最多 3 发，两发之间冷却 1.5s；`--no-fire` 可整体锁定
- 设备端看门狗兜底

## 开发

```bash
pip install -r requirements-dev.txt
python -m pytest
```

测试覆盖 MCP 协议、动作队列（急停、限速、发射冷却）、raw REPL 解析，以及一个模拟灵矽平台的端到端握手测试。都不需要硬件和网络。

## 代码结构

```
linx_client/
├── __main__.py      # 命令行：activate / run / tank-test / list-audio
├── ota.py           # OTA 上报：激活码、ws 地址和 token
├── identity.py      # 虚拟 MAC + Client-Id
├── session.py       # WebSocket：hello、音频收发、tts/stt、MCP 分发
├── audio.py         # sounddevice 采集和播放（下行 opus 时需要 opuslib）
├── mcp.py           # 设备端 MCP 服务（JSON-RPC 2.0）
├── tools.py         # 小车工具定义（名称、描述、参数）
└── tank/
    ├── executor.py  # 动作队列和安全约束
    ├── backend.py   # 驱动接口 + MockBackend
    └── serial_repl.py  # USB raw REPL 驱动（含设备端看门狗）
```

## 已知待确认

- 服务端下行音频格式以 hello 响应为准。文档示例是 opus 24kHz；如果是 opus，需要 `pip install opuslib` 并在系统里装 libopus（macOS：`brew install opus`）。
- `t 文字` 文本调试依赖平台对 `listen.detect` 的处理方式，需要实测。
- 履带方向、转向时长和炮塔角度方向都要实车校准。
