# 交接看板

> 每个 agent 收工前更新本文件并提交。按时间倒序记录，最新的写在最上面。规则见 `AGENTS.md`「交接协议」。

**最后更新**：2026-10-24 · Claude Code（USB 连接遥控器探测）

## 当前状态

- `linx-client/` 已实现：灵矽 OTA 激活、WebSocket 会话、MCP 工具、动作队列、串口 raw REPL 驱动
- 技术方案已更新为 v2（接入灵矽），见 `docs/技术方案.md`
- 还没有接实车；**灵矽 mock 联调已打通**：文本「往前走两秒然后开一炮」→ 平台依次调用 move(forward, 2000) 和 fire(1)，并有语音回复

## 进行中

- **M1 灵矽激活**：已执行 `python -m linx_client activate`，OTA 上报成功，**设备已在智控台绑定（✅ 2026-10-23）**。虚拟设备 `Device-Id: 9A:BD:6D:4D:8A:A0`，身份文件是 `linx-client/.linx_device.json`（已 gitignore，不要删）。智控台已配置 Function Call 和提示词；`run --backend mock` 已验证文本链路。待验证：麦克风语音输入、扬声器播放 TTS。

## 下一步（按优先级）

-1. **（2026-10-24 最新）车没有外露 USB 口，改为逆向原厂蓝牙 simulation 协议**，见 v3 文档 §3.0。当前任务：让用户在 Android 上开启 HCI snoop 日志，用官方 App 的测试功能操作各执行器，再用 `adb bugreport` 导出日志分析 GATT 和 JSON 格式。官方源码可以 clone `CyberBrick-Official/CyberBrick_Controller_Core` 查看
0. **主线已切换到 v3 纯网页版**（见 `docs/技术方案-v3-网页版.md`），按 W0→W5 推进。W0：①接上 Core，查 USB 芯片类型/VID，测试 Android Chrome + web-serial-polyfill 能否进入 raw REPL；②在电脑 Chrome 里做最小页面，验证麦克风 16k PCM → 灵矽 stt
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
| WebSocket 鉴权方式（2026-10-23 实测） | ✅ 平台**只认 `device-id`**（请求头或 URL 参数 `?device-id=` 都行），**不校验 token**：错误 token 也能完整对话并触发工具调用；只带 client-id 会被拒；未绑定的随机 MAC 也能握手。ws:// 和 wss:// 都可用。子协议传 token 无效。探测脚本思路见本条，结论可能随平台更新而变 |
| 引脚映射 | ✅ 已从遥控器 `rc_config` 读出（见「已确认事实」）；❓ 哪条是左/右、方向，还要实车验证 |

## 已确认事实

- **遥控器 USB 探测（2026-10-24 实测，遥控器接 Mac）**：
  - USB 为 ESP32-C3 原生 `USB JTAG_serial debug unit`，VID `0x303A` / PID `0x1001`，没有 CH340/CP210x（回答了 v3 W0 的芯片疑问；Core 都是同款板，车上的大概率也一样）
  - 启动日志：`Firmware ver: 01.01.04.13`、`Loader ver: 99.00.02.51`，随后 `[MAIN]MASTER_INIT.` → `PARSE_UPDATE`（遥控器上存在 `rc_config`）→ `AUTO_SLEEP_EN.`
  - Flash 开了 **Secure Download Mode**：esptool 读不了 Flash，只能经 REPL 读文件
  - **Ctrl-C 打断的条件**：遥控器和车**处于配对连接状态**时，默认参数打开串口（不复位）后连发 `\x03\x03` 就能打断，进入 REPL。复位后立刻发、mpremote（打开串口会复位）、没连上车时都失败。导出脚本思路：pyserial 默认打开 → `\x03\x03` → `\x01` raw REPL → 用 base64 逐个读文件
  - **遥控器文件已备份**到 `firmware/backup-remote/`（2026-10-24）。`app/rc_main.py` 与官方源码相同（只差 CRLF）；`control/devices/parser` 是 .mpy
  - 遥控器上的 `rc_module` 只有 `rc_master_init / rc_master_data / rc_simulation / file_transfer / rc_index / ble_mem` 等，**没有发送自定义数据的接口**，无线协议在闭源 C 模块里。所以「电脑 → USB → 遥控器 → 车」不能直接调用现成 API，要么逆向无线协议（固件里有 `espnow`/`aioespnow` 模块），要么继续走 BLE simulation
- **Mini T 真实接线（来自遥控器 `rc_config`，config_name `Draft_CyberBrick Mini T`，receiver_1）**：

  | 遥控输入 | 执行器 | 端口 GPIO | 含义（推断） |
  |---|---|---|---|
  | 左摇杆 Y | MOTOR2，direction **negative** | Pin6/7 | 一侧履带（方向反装） |
  | 右摇杆 Y | MOTOR1，positive | Pin4/5 | 另一侧履带 |
  | 右肩摇杆 Y | PWM1，type `speed`（连续旋转舵机），推上 +45 / 推下 −45 / 回中 0 | SERVO1 = Pin3 | 炮塔转动 |
  | 左肩按键 | PWM2，type `angle`，按下 150°、松开 86°（初始 90，范围 80–160） | SERVO2 = Pin2 | **发射扳机舵机** |

  结论：当前 `BOOTSTRAP` 有两处错误：①发射不是 Pin6/7 电机，而是 **Pin2 舵机打到 150° 再回 86°**；②Pin6/7 是另一条履带（反向），不是发射。左右是哪一侧还要架空实测；炮塔是连续旋转舵机，不能按角度控制，要按「速度 + 时长」控制

- 车（2026-10-24 照片）：Mini T 类履带车 + 顶部发射器；**没有外露 USB 口**；有实体遥控器；官方 CyberBrick App 能通过蓝牙连接
- 充电（用户 2026-10-24 确认）：车能充电，但车上的充电口**不是 USB**；**遥控器带 USB 口**（可以用 USB 充电，能否传数据待测）
- 官方文档 `docs/cyberbrick_core/quickref.rst`：每块 Core 都**自带 USB-C 口**，MicroPython REPL 就在这个口上（USB-Serial/JTAG，GPIO18/19，115200）。所以车上的 Core 应该也有 USB-C，只是被车壳挡住了，拆开才能接
- 官方 `rc_module` 文档（`docs/cyberbrick_core/lib/rc_module.rst`）：`rc_simulation()` 的指令"通常来自 PC 或 App"，`file_transfer()` 也支持"CyberBrick App 或 PC 客户端"同步 `rc_config`。也就是说官方另有一条 PC 通道，具体走哪条链路待查
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

- **安全**：Device-Id 实际上就是唯一凭据，而本仓库是公开的，本文件里写着 Device-Id。是否需要：①换一个新的虚拟设备身份并在智控台解绑旧设备；②以后不再把 Device-Id 写进仓库

- 课题要求“在小车内装上灵矽”，电脑模拟设备是否被认可？（备选：树莓派或手机绑在车上）
- 比赛日期和演示形式

## 决策记录

- **2026-10-23**：主线改为纯网页版（用户有 Android 手机）。控车有线（WebUSB + raw REPL，车上固件不用改）和无线（Web Bluetooth + BLE 白名单固件）都要做，先做有线。依据：实测平台只认 device-id、OTA 接口允许跨域、下行音频是 PCM

- **2026-10-23**：车型确认为 Mini T。对照官方固件后发现当前代码的左履带映射可疑；决定先在 M0 读出 `rc_config` 确定真实接线，再改 `BOOTSTRAP`，不凭猜测改代码
- **2026-10-23**：观察到连接建立后平台会主动调用 `self.get_device_status`（两次），属于正常行为

- **2026-10-23**：放弃 v1「手机网页 + 自建网关 + UDP」，改为接入灵矽，用电脑模拟设备。原因：语音识别、大模型、语音合成都由平台提供；也避开了手机端 HTTPS 和麦克风权限的问题
- **2026-10-23**：控车先走 USB raw REPL（低延迟，不需要改固件），无线 UDP 放到 M4
- **2026-10-23**：多 agent 交接方式定为 `AGENTS.md`（共享说明）+ 本看板；`CLAUDE.md` 引用前者，`GEMINI.md` 软链接到前者
