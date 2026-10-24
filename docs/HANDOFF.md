# 交接看板

> 每个 agent 收工前更新本文件并提交。按时间倒序记录，最新的写在最上面。规则见 `AGENTS.md`「交接协议」。

**最后更新**：2026-10-24 · Claude Code（车 USB 接通，BOOTSTRAP 按真实接线重写）

## 当前状态

- `linx-client/` 已实现：灵矽 OTA 激活、WebSocket 会话、MCP 工具、动作队列、串口 raw REPL 驱动
- 技术方案已更新为 v2（接入灵矽），见 `docs/技术方案.md`
- 还没有接实车；**灵矽 mock 联调已打通**：文本「往前走两秒然后开一炮」→ 平台依次调用 move(forward, 2000) 和 fire(1)，并有语音回复

## 进行中

- **M1 灵矽激活**：已执行 `python -m linx_client activate`，OTA 上报成功，**设备已在智控台绑定（✅ 2026-10-23）**。虚拟设备 `Device-Id: 9A:BD:6D:4D:8A:A0`，身份文件是 `linx-client/.linx_device.json`（已 gitignore，不要删）。智控台已配置 Function Call 和提示词；`run --backend mock` 已验证文本链路。待验证：麦克风语音输入、扬声器播放 TTS。

## 下一步（按优先级）

-11. **（2026-10-25 最新）情绪肢体语言 + 人格切换**：`tank/mood.py` 把平台 `llm` 消息里的 emotion 映射成 5 种小动作和车灯颜色。车正忙时跳过；两次反应至少间隔 4 秒，同一种情绪 20 秒内不重复；所有动作都成对抵消，车和炮管会回到原位。工具新增 `self.tank.set_persona`（正常/特种兵/老爷爷/醉汉），只改变前进/后退的风格。车灯走新指令 `led r g b`，**车端程序要重新部署才会生效**（顺带还有关闭 Wi-Fi 省电的改动）。提示词 `prompts/agent-prompt.md` 已更新（要求回复以表情开头、人格说话风格、按角度转向），**要用户手动贴到智控台**。`--no-mood` 可关闭。**未在实车/真实平台验证**：测试时用户自己的 run 进程占着同一个 Device-Id，两边互相踢下线
-10. **（2026-10-25）语音打断**：新增 `LinxSession.abort()`，发送 `{"type":"abort","reason":"wake_word_detected"}`（沿用小智协议），本地清空扬声器，并丢弃本轮回复残余的音频。触发方式：网页上按住说话或点「小车说话中」、急停、终端输入 `a`；加 `--duplex`（需戴耳机）时，按麦克风音量自动判断插话（`--barge-in-level`，默认 2000）。**真实平台实测**：发出 abort 约 0.1s 后平台回 `tts stop`，后面的句子不再下发，说明平台支持打断。注意：同一个 Device-Id 只能有一个连接，两个程序同时连会互相踢下线，测试时要先关掉另一个
-9. **（2026-10-24）炮管俯仰**：SERVO1 其实控制炮管上下俯仰，不是水平旋转。工具已改为 `self.tank.barrel(up/down)`，**`BARREL_UP_SPEED = 45` 的方向用户已实车确认**。左右瞄准靠转动车身
-8. **（2026-10-24）速度与转向调整**：用户反馈前进慢、履带带不动，原因是之前限速 40%，40% 占空比落地后扭力不够。已把默认速度改为 80、`--max-speed` 默认改为 100。转向从单侧履带改为原地转向（两条履带反向），`turn` 工具改成按角度。**实测满速原地转 90° ≈ 400ms**（`MS_PER_90_AT_FULL`）。**待确认**：①低速（50）落地能否带动，带不动就要加低速补偿；②满速转向时找不到车过一次，怀疑电机启动电流把电池电压拉低、导致主控板重启（未证实，用户还没回答灯是否变过黄色），如果反复出现，就降低转向速度或加软启动
-7. **（2026-10-24）网页控制台**：`run --web`（默认端口 8080，只监听本机）会打开 `linx_client/web/index.html`。页面上有对话记录、急停（Esc）、麦克风常开/按住说话（空格）、手动控车按钮、小车状态。页面只是界面，连平台和控车仍由 Python 进程负责，手动按钮也经过 executor 的安全约束。已用真实平台 + mock 后端跑通文本指令（截图验证过）。**未验证**：用真实麦克风按住说话，以及配合 udp 后端控实车
-6. **（2026-10-24）无线维护通道**：车的固件**没有 WebREPL**（`no module named '_webrepl'`），所以在 `wifi_main.py` 里自己实现了 TCP 8266 维护通道（先验证维护密码，然后执行代码）。装车后不用 USB：`car-exec --host <IP> "print(...)"` 用来调试，`wifi-setup --host <IP> --code-only` 用来更新程序，`wifi-setup --host <IP> --uninstall` 用来恢复原厂。`/wifi.json` 支持配置多个网络，都连不上时车会自己开热点 `CyberBrick-Tank`（192.168.4.1，热点密码 = 维护密码，只用于救急，因为 Mac 连上后无法访问灵矽）。比赛现场建议用手机热点
-5. **（2026-10-24）Wi-Fi 无线控车已装到车上**：用户运行了 `wifi-setup`，车连上 2.4G 网络 `CMCC-7GE2`，局域网内 UDP `ping` 能收到 `pong`（车的 IP 当时是 192.168.1.8，DHCP 分配，可能会变）。主机端 `--backend udp` 会自动发现。**下一步**：拔掉 USB 跑 `tank-test --backend udp`，再跑 `run --backend udp` 语音控车。开机按住板载按键 = 原厂遥控；`wifi-setup --uninstall` = 恢复原厂 boot.py
-4. **（2026-10-24）实车架空测试通过**（见「已验证」表）。下一步：装回炮塔和发射器，确认 `turret left` 的方向，再执行 `env -u https_proxy -u http_proxy -u all_proxy .venv/bin/python -m linx_client run --backend serial --max-speed 40` 做语音控车；落地后校准 turn 的时长（转 90° 要多少毫秒）
-3. **（2026-10-24）车有 USB，已接通**。`BOOTSTRAP` 已改为调用车上原厂驱动 `/bbl/motors.py`、`/bbl/servos.py`，并在实车上注入成功（未发运动指令）。**下一步**：把车架空，执行 `cd linx-client && .venv/bin/python -m linx_client tank-test --backend serial --max-speed 40`，逐项确认：①前进时两条履带是否都向前（不对就调整 `drive` 里 MOTOR1/2 的正负号或左右对调）；②turn left 是否真的左转；③炮塔 left 是否向左转（不对就把 executor 的 `TURRET_SPEED` 取反）；④发射时扳机 150° 能否打出、能否回到 86°。之后再跑 `run --backend serial` 做语音控车。**2026-10-24 首次 tank-test 时电机不动**：已确认软件正常（实车上 `_m.set_speed(1, 820)` 后 Pin4 占空比 40%，没有报错），原因是**车的电池没电**（开电源后主控板灯不亮）。USB 只能给主控板供电，带不动电机，充好电后再测
-2. **（2026-10-24）Mini T 的 simulation JSON 对照表已写好**（v3 文档 §3.0「Mini T 动作 → simulation JSON 对照」）。抓到蓝牙日志后，逐条核对 JSON 格式和 GATT 写入内容；MOTOR2 前进是否要发负值、发射后扳机能否自动回位，都要实测
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
| 引脚映射 | ✅ 已从遥控器和车的 `rc_config` 读出（两者相同），`BOOTSTRAP` 已按此重写（见「已确认事实」）；❓ 哪条是左/右、方向，还要实车验证 |

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

- 车（2026-10-24）：Mini T 类履带车 + 顶部发射器；**有 USB 口**（之前记成没有，用户已更正），接 Mac 后是 `USB JTAG_serial debug unit`；连接时用默认参数打开串口、连发 `\x03\x03`，一次就能打断原厂程序。车上文件已备份到 `firmware/backup-car/`，与遥控器上的**完全相同**（含 `rc_config`），所以接线表对车同样成立；有实体遥控器；官方 CyberBrick App 能通过蓝牙连接
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

- **2026-10-24**：装车后没有 USB，需要无线维护。原计划用 WebREPL，但固件里没有这个模块，于是在 `wifi_main.py` 里加了 TCP 8266 维护通道。会执行网络发来的代码，所以必须先过维护密码，而且会话期间车会先停下。主机端 `NetRepl` 和 `RawRepl` 接口一致，`put_file` 等函数通用。主循环改为 `select.poll` 同时监听 UDP 和 TCP；已在实车验证，`poll` 阻塞期间 Timer 回调照常触发（301ms）

- **2026-10-24**：无线控车选 Wi-Fi UDP（车上 `firmware/car/wifi_main.py` + 改过的 `boot.py`），没选逆向原厂蓝牙。原因：工作量小、延迟低、确定性高。车只接受白名单指令（drive/turret/fire/halt/ping），不执行任意代码；Wi-Fi 密码只写到车上的 `/wifi.json`，不进仓库。已在实车验证 `Timer` 回调在 `recvfrom` 阻塞期间能正常触发，断网后看门狗照样停车。注意：ESP32-C3 只支持 2.4G；Mac 上有虚拟网卡时，发 `255.255.255.255` 广播会走错网卡，所以自动发现要绑定默认网卡并发子网广播

- **2026-10-24**：车有 USB，控车回到「USB raw REPL」主线。`BOOTSTRAP` 不再自己拼 PWM，而是调用车上的原厂驱动（H 桥刹车、舵机占空比都由官方实现）。炮塔是连续旋转舵机，所以工具 `self.tank.aim(angle)` 改为 `self.tank.turret(direction, duration_ms)`，速度固定为 45，与原厂遥控一致；后端接口相应从 `aim(angle)` 改为 `turret(speed, failsafe_ms)`，保留 failsafe 语义。`halt()` 会刹停两条履带、停炮塔，并让扳机回到 86°

- **2026-10-23**：主线改为纯网页版（用户有 Android 手机）。控车有线（WebUSB + raw REPL，车上固件不用改）和无线（Web Bluetooth + BLE 白名单固件）都要做，先做有线。依据：实测平台只认 device-id、OTA 接口允许跨域、下行音频是 PCM

- **2026-10-23**：车型确认为 Mini T。对照官方固件后发现当前代码的左履带映射可疑；决定先在 M0 读出 `rc_config` 确定真实接线，再改 `BOOTSTRAP`，不凭猜测改代码
- **2026-10-23**：观察到连接建立后平台会主动调用 `self.get_device_status`（两次），属于正常行为

- **2026-10-23**：放弃 v1「手机网页 + 自建网关 + UDP」，改为接入灵矽，用电脑模拟设备。原因：语音识别、大模型、语音合成都由平台提供；也避开了手机端 HTTPS 和麦克风权限的问题
- **2026-10-23**：控车先走 USB raw REPL（低延迟，不需要改固件），无线 UDP 放到 M4
- **2026-10-23**：多 agent 交接方式定为 `AGENTS.md`（共享说明）+ 本看板；`CLAUDE.md` 引用前者，`GEMINI.md` 软链接到前者
