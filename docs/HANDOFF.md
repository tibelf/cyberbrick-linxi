# 交接看板

> 每个 agent 收工前更新本文件并提交。按时间倒序记录，最新的写在最上面。规则见 `AGENTS.md`「交接协议」。

**最后更新**：2026-10-23 · Claude Code（车型确认 + 官方端口对照）

## 当前状态

- `linx-client/` 已实现：灵矽 OTA 激活、WebSocket 会话、MCP 工具、动作队列、串口 raw REPL 驱动
- 技术方案已更新为 v2（接入灵矽），见 `docs/技术方案.md`
- 还没有接实车；**灵矽 mock 联调已打通**：文本「往前走两秒然后开一炮」→ 平台依次调用 move(forward, 2000) 和 fire(1)，并有语音回复

## 进行中

- **M1 灵矽激活**：已执行 `python -m linx_client activate`，OTA 上报成功，**设备已在智控台绑定（✅ 2026-10-23）**。虚拟设备 `Device-Id: 9A:BD:6D:4D:8A:A0`，身份文件是 `linx-client/.linx_device.json`（已 gitignore，不要删）。智控台已配置 Function Call 和提示词；`run --backend mock` 已验证文本链路。待验证：麦克风语音输入、扬声器播放 TTS。

## 下一步（按优先级）

1. **M0 备份原厂固件 + 读出接线配置**：用 USB 连接**车上的** Core（不是遥控器），执行 `mpremote connect <串口> fs cp -r :/ firmware/backup/`，然后提交。重点看 `rc_config`：它是 CyberBrick App 写入的配置，记录了每个 MOTOR / SERVO 端口接的是什么（哪条履带、炮塔、发射），可以据此确定引脚映射（见「已确认事实」里的端口表）
2. **按 `rc_config` 修正 `linx-client/linx_client/tank/serial_repl.py` 的 `BOOTSTRAP`**，再用 `tank-test --backend serial` 架空逐项确认履带方向、转向时长、炮塔角度方向和发射（M2）
3. **M1 收尾**：真实麦克风说话测试，确认外放回声不会触发误识别（必要时戴耳机）

## 已验证 / 未验证

| 项 | 状态 |
|---|---|
| 单元测试 | ✅ 14 passed（`linx-client/.venv`，Python 3.13） |
| 灵矽 OTA 上报 + 设备绑定 | ✅ 已绑定 |
| 灵矽 WebSocket 握手 + MCP 工具调用 | ✅ 真实平台（OTA 下发的是 `ws://xrobo-io.qiniuapi.com/v1/ws/`，不是 wss） |
| 服务端下行音频格式 | ✅ pcm 16kHz/16bit/单声道/20ms，不需要 opus |
| `t 文字` 文本调试（`listen.detect`） | ✅ 可用，平台当作用户输入处理 |
| 麦克风语音 / 扬声器 TTS | ❓ 待用户实测 |
| 引脚映射 | ❓ 未在本车验证，且与官方固件的端口定义有出入（见下） |

## 已确认事实

- 硬件：**CyberBrick Mini T 坦克**（用户 2026-10-23 确认），主控 ESP32-C3（MicroPython），顶部有发射器；有原厂实体遥控器
- 官方固件 [CyberBrick_Controller_Core](https://github.com/CyberBrick-Official/CyberBrick_Controller_Core) `src/app_rc/bbl/` 定义的接收板端口：

  | 端口 | GPIO | 说明 |
  |---|---|---|
  | MOTOR1 | Pin4 / Pin5 | 双通道 H 桥：正转 CH1=PWM、CH2=0；反转相反；停止时两路都拉满（刹车） |
  | MOTOR2 | Pin6 / Pin7 | 同上 |
  | SERVO1–4 | Pin3 / Pin2 / Pin1 / Pin0 | 50Hz 舵机 |
  | 其他 | Pin8 灯（NeoPixel）、Pin9 板载按键、Pin10 主从角色脚（遥控器/接收端） | 原厂 `control.py` 也用了 `Timer(0)` |

- 对照当前代码的映射：右履带 Pin4/5 = MOTOR1，发射 Pin6/7 = MOTOR2，炮塔 Pin3 = SERVO1，这三项和官方端口一致；**左履带 Pin2 其实是 SERVO2 端口**，当前代码却用 1000Hz PWM 驱动它、"duty 0 = 前进"，很可能不对。Mini T 实际每个端口接什么，以车上的 `rc_config` 为准
- 开发机：macOS；Python 环境用 `linx-client/.venv`（`python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`）
- GitHub：`tibelf/cyberbrick-linxi`（**公开**仓库，origin）；旧私有仓库 `GaIIagherdev/cyberbrick-voice` 已不再使用

## 待用户确认

- 课题要求“在小车内装上灵矽”，电脑模拟设备是否被认可？（备选：树莓派或手机绑在车上）
- 比赛日期和演示形式

## 决策记录

- **2026-10-23**：车型确认为 Mini T。对照官方固件后发现当前代码的左履带映射可疑；决定先在 M0 读出 `rc_config` 确定真实接线，再改 `BOOTSTRAP`，不凭猜测改代码
- **2026-10-23**：观察到连接建立后平台会主动调用 `self.get_device_status`（两次），属于正常行为

- **2026-10-23**：放弃 v1「手机网页 + 自建网关 + UDP」，改为接入灵矽，用电脑模拟设备。原因：语音识别、大模型、语音合成都由平台提供；也避开了手机端 HTTPS 和麦克风权限的问题
- **2026-10-23**：控车先走 USB raw REPL（低延迟，不需要改固件），无线 UDP 放到 M4
- **2026-10-23**：多 agent 交接方式定为 `AGENTS.md`（共享说明）+ 本看板；`CLAUDE.md` 引用前者，`GEMINI.md` 软链接到前者
