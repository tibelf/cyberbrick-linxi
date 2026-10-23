# 交接看板

> 每个 agent 收工前更新本文件并提交。按时间倒序记录，最新的写在最上面。规则见 `AGENTS.md`「交接协议」。

**最后更新**：2026-10-23 · Claude Code

## 当前状态

- `linx-client/` 已实现：灵矽 OTA 激活、WebSocket 会话、MCP 工具、动作队列、串口 raw REPL 驱动
- 技术方案已更新为 v2（接入灵矽），见 `docs/技术方案.md`
- 还没有接实车，也还没有连真实的灵矽平台

## 进行中

无。

## 下一步（按优先级）

1. **跑单元测试**：`cd linx-client && pip install -r requirements-dev.txt && python -m pytest`。本机 base 环境没装 pytest，所以还没在本机跑过
2. **M0 备份原厂固件**：用 `mpremote` 连接 Core，把原厂文件备份到 `firmware/backup/` 并提交
3. **M1 灵矽联调**：智控台配置 Function Call 和提示词，执行 `python -m linx_client activate` 绑定设备，再执行 `run --backend mock` 验证工具调用
4. **M2 实车校准**：车架空后执行 `tank-test --backend serial`，逐项确认履带方向、转向时长、炮塔角度方向和发射

## 已验证 / 未验证

| 项 | 状态 |
|---|---|
| 单元测试 | ❓ 本机未跑（README 写的是“通过单元测试”，需要复核） |
| 灵矽 OTA / WebSocket 握手 | ❓ 只有本地假平台测试，未连真实平台 |
| 服务端下行音频格式（pcm / opus） | ❓ 未知 |
| `t 文字` 文本调试（`listen.detect`） | ❓ 未知 |
| 引脚映射（来自 CyberClaw） | ❓ 未在本车验证 |

## 已确认事实

- 硬件：CyberBrick 履带小车 + 顶部发射器，主控 ESP32-C3（MicroPython）；有原厂实体遥控器
- 开发机：macOS
- GitHub：`GaIIagherdev/cyberbrick-voice`（私有）

## 待用户确认

- 课题要求“在小车内装上灵矽”，电脑模拟设备是否被认可？（备选：树莓派或手机绑在车上）
- 比赛日期和演示形式

## 决策记录

- **2026-10-23**：放弃 v1「手机网页 + 自建网关 + UDP」，改为接入灵矽，用电脑模拟设备。原因：语音识别、大模型、语音合成都由平台提供；也避开了手机端 HTTPS 和麦克风权限的问题
- **2026-10-23**：控车先走 USB raw REPL（低延迟，不需要改固件），无线 UDP 放到 M4
- **2026-10-23**：多 agent 交接方式定为 `AGENTS.md`（共享说明）+ 本看板；`CLAUDE.md` 引用前者，`GEMINI.md` 软链接到前者
