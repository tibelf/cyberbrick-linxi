# 交接看板

> 每个 agent 收工前更新本文件并提交。按时间倒序记录，最新的写在最上面。规则见 `AGENTS.md`「交接协议」。

**最后更新**：2026-10-23 · Claude Code

## 当前状态

- `linx-client/` 已实现：灵矽 OTA 激活、WebSocket 会话、MCP 工具、动作队列、串口 raw REPL 驱动
- 技术方案已更新为 v2（接入灵矽），见 `docs/技术方案.md`
- 还没有接实车；灵矽平台已通过 OTA 上报，还没建立 WebSocket 会话

## 进行中

- **M1 灵矽激活**：已执行 `python -m linx_client activate`，OTA 上报成功并拿到激活码，正在等用户到智控台绑定。虚拟设备 `Device-Id: 9A:BD:6D:4D:8A:A0`，身份文件是 `linx-client/.linx_device.json`（已 gitignore，不要删）。绑定后下一步：智控台设置 Function Call 和提示词，然后执行 `run --backend mock`。

## 下一步（按优先级）

1. **M0 备份原厂固件**：用 `mpremote` 连接 Core，把原厂文件备份到 `firmware/backup/` 并提交
2. **M1 灵矽联调**：智控台绑定设备、配置 Function Call 和提示词，再执行 `run --backend mock` 验证工具调用
3. **M2 实车校准**：车架空后执行 `tank-test --backend serial`，逐项确认履带方向、转向时长、炮塔角度方向和发射

## 已验证 / 未验证

| 项 | 状态 |
|---|---|
| 单元测试 | ✅ 14 passed（`linx-client/.venv`，Python 3.13） |
| 灵矽 OTA 上报 | ✅ 真实平台返回激活码 |
| 灵矽 WebSocket 握手 | ❓ 待绑定后验证 |
| 服务端下行音频格式（pcm / opus） | ❓ 未知 |
| `t 文字` 文本调试（`listen.detect`） | ❓ 未知 |
| 引脚映射（来自 CyberClaw） | ❓ 未在本车验证 |

## 已确认事实

- 硬件：CyberBrick 履带小车 + 顶部发射器，主控 ESP32-C3（MicroPython）；有原厂实体遥控器
- 开发机：macOS；Python 环境用 `linx-client/.venv`（`python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`）
- GitHub：`GaIIagherdev/cyberbrick-voice`（私有）

## 待用户确认

- 课题要求“在小车内装上灵矽”，电脑模拟设备是否被认可？（备选：树莓派或手机绑在车上）
- 比赛日期和演示形式

## 决策记录

- **2026-10-23**：放弃 v1「手机网页 + 自建网关 + UDP」，改为接入灵矽，用电脑模拟设备。原因：语音识别、大模型、语音合成都由平台提供；也避开了手机端 HTTPS 和麦克风权限的问题
- **2026-10-23**：控车先走 USB raw REPL（低延迟，不需要改固件），无线 UDP 放到 M4
- **2026-10-23**：多 agent 交接方式定为 `AGENTS.md`（共享说明）+ 本看板；`CLAUDE.md` 引用前者，`GEMINI.md` 软链接到前者
