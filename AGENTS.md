# AGENTS.md

本文件是所有 AI 编码代理（Claude Code、Codex、Gemini CLI、Cursor、Copilot 等）的**唯一共享说明**。`CLAUDE.md` 通过引用加载本文件，`GEMINI.md` 是指向本文件的软链接。修改项目约定只改这里。

## 交接协议（必读）

本项目会在多个 agent 之间接力（某个工具额度用完后由另一个接手）。所有上下文只存在于仓库里，不要依赖任何工具的私有记忆或聊天记录。

1. **开工前**：读本文件和 `docs/HANDOFF.md`（当前状态、进行中的任务、下一步），再执行 `git log --oneline -10` 和 `git status`。
2. **工作中**：每完成一个可验证的小步骤就提交一次。提交信息写清做了什么、验证结果如何。不要攒一大坨未提交的改动。
3. **收工前，或预计额度/上下文快用完时**：更新 `docs/HANDOFF.md` 并提交。需要更新的内容：
   - 「进行中」：做到哪一步，改了哪些文件，停在哪
   - 「下一步」：接手者打开就能执行的具体动作
   - 「已验证 / 未验证」：如实写，没跑过的测试不要写成通过
   - 「决策记录」：做了什么取舍、为什么
4. 用户口头确认的新信息（车型、引脚、平台行为等）当场写进 `docs/HANDOFF.md` 的「已确认事实」，不要只留在对话里。

## Project

七牛 Hackathon「智能硬件」课题：一辆能发射子弹的 CyberBrick 小车（Mini T 类履带车，主控 Core = ESP32-C3 / MicroPython），接入灵矽（Linx）平台实现语音控制。没有灵矽实体硬件，由 `linx-client/` 在电脑上**模拟一台灵矽设备**（电脑麦克风/扬声器），再通过 USB 串口控车。

- **主线（v3）**：Android Chrome 网页本身就是灵矽设备，通过 WebUSB（有线）或 Web Bluetooth（无线）控车，见 `docs/技术方案-v3-网页版.md`。`linx-client/` 是 Python 参考实现：网页的协议、工具描述和安全约束必须与它一致，改动其中一边时同步另一边。
- 设计文档（中文，含里程碑、风险、待确认项）：`docs/技术方案-v3-网页版.md`（主线）、`docs/技术方案.md`（v2，协议细节）。改架构或协议时同步更新。
- **Device-Id 就是灵矽的唯一凭据**（平台不校验 token），绝不能写进仓库（仓库是公开的）。
- 文档、注释、终端输出、MCP 工具描述均使用中文，保持一致。

## Commands

全部在 `linx-client/` 下执行（Python ≥ 3.11）：

```bash
pip install -r requirements-dev.txt                 # 运行依赖 + pytest
python -m pytest                                    # 全部测试（无需硬件/网络）
python -m pytest tests/test_executor.py::test_fire_locked   # 单个测试

python -m linx_client activate                      # OTA 激活，打印 6 位激活码，到智控台绑定
python -m linx_client run --backend mock            # 连灵矽，工具调用只打印不控车
python -m linx_client run --backend serial --max-speed 40
python -m linx_client run --no-audio --backend mock # 不开声卡，终端输入 `t 文字` 发文本调试
python -m linx_client run --backend udp --web       # Wi-Fi 控车 + 网页控制台 http://localhost:8080
python -m linx_client wifi-setup [--host IP] [--code-only|--uninstall]  # 给车装/更新/卸载 Wi-Fi 控车程序
python -m linx_client car-exec --host IP "print(1)" # 无线在车上执行代码（维护密码读 .car_admin_password）
python -m linx_client tank-test --backend serial    # 逐步测试每个动作（车先架空）
python -m linx_client camera-test --camera 0        # 拍一张存成 camera-test.jpg，检查摄像头
python -m linx_client run --backend udp --camera 0  # 加拍照工具 self.camera.take_photo（需智控台开启视觉分析；默认俯视 --camera-view overhead，车载用 front）
python -m linx_client list-audio
```

运行时终端：回车 = 急停，`t 文字` = 发文本，`q` = 退出。串口可用 `--port` 或环境变量 `CYBERBRICK_PORT` 指定。`.linx_device.json` 是虚拟设备身份（MAC/Client-Id），删除即变成新设备、需重新绑定。

## Architecture

三层，职责严格分开：

1. **灵矽平台（听和想）**：ASR → LLM（Function Call）→ TTS。把「前进两秒再开火」拆成多次 MCP `tools/call`。智控台必须把意图识别设为 Function Call，角色提示词在 `prompts/agent-prompt.md`。
2. **linx-client（排程和安全）**
   - `session.py`：WebSocket 会话。握手头必须带 `Authorization: Bearer`、`Device-Id`、`Client-Id`、`Protocol-Version`（所以客户端只能是本地程序，不能是网页）。上行 PCM 16k/20ms，下行格式以服务端 hello 为准（可能是 opus，需 opuslib）。默认半双工：TTS 播报期间丢弃麦克风帧（声卡余音约 0.4s 也算播报中）。`--duplex` 全双工：播报期间只上传静音，`echo.py` 的 `BargeInDetector` 按「麦克风音量 / 播放音量」估回声增益，明显超过回声才算插话，打断后补传那几帧原声。断线指数退避重连；收到 `config_updated` 通知会主动断开重连。
   - `mcp.py`：设备端 MCP 服务（平台是客户端），消息封装为 `{"type":"mcp","payload":<JSON-RPC>}`。参数只用 integer/string/boolean；`Prop.coerce` 会把整数裁剪到 min/max。工具异常一律转成 `isError` 结果返回，绝不让连接断掉。
   - `tools.py`：工具名、描述、参数 schema。**描述文字直接决定大模型何时调用**（如 fire 只在用户明确要求时调用）。
   - `tank/executor.py`：动作队列。工具调用**立即返回「已安排」**，动作在后台 worker 中顺序执行，因此多次调用会自然串成序列。`stop()` 不进队列：清空队列、取消当前动作、halt。
   - `camera.py`：拍照工具。取图来源 `--camera`（数字=电脑摄像头 OpenCV，http=JPEG 快照地址如车载 ESP32S3，其他=文件）；视觉接口 url/token 来自 MCP initialize 的 `capabilities.vision`（`McpServer.vision`），字段 `question` + `file`（camera.jpg）
   - `seek.py`：看图闭环寻的 `self.tank.seek`（有 `--camera` 才注册）。平台一轮只执行一次工具调用，所以“拍照→换算→转向/前进→再拍”的循环在本地跑：`TankExecutor.start_mission` 启动，`stop()` 会连任务一起取消，每一步仍走 move/turn/fire 的安全约束；任务期间不插情绪小动作；结束后 `session.say()` 播报。视觉模型被要求按固定格式回答，解析不了就重问一次再放弃。外部摄像头（overhead）只问位置 `坦克=x,y；目标=x,y`（百分比坐标），车头方向由程序比较前进前后的位置算出（不要让视觉模型判断朝向，实测不可靠）；车载前视问 `找到=…；偏角=…；近=…`
   - `wander.py`：自由闲逛 `self.tank.wander`（有 `--camera` 才注册）。**没有步数上限，只有 stop()（语音“停”、回车、网页急停）才结束**；**流水线**：车走着就拍照分析，下一段（1.2–2.5s）提前排进队列，当前段走完立刻接上；最多提前一段。前方堵住立刻刹车转向；偶尔停下做小动作（`emote(..., from_mission=True)`、摇炮管、停顿）。车头方向靠 executor 的 `motion_epoch / idle_epoch / turn_total`：两张照片之间只做过前进时用位移测朝向，转过之后按 `turn_total` 推算。一直在走却没挪动或四周堵死就后退掉头；看不清立刻停车；外部摄像头下靠近画面边缘不往外走。**闲逛绝不开火**。和平台断开时暂停（`paused=lambda: session.ws is None`），重连后继续；一步出错不结束任务，连续 3 次看不清才停下播报。新任务（seek/wander）会接替正在跑的任务
   - `tank/backend.py` 定义 `TankBackend` Protocol + `MockBackend`；`serial_repl.py` 是实车驱动。
3. **CyberBrick Core（执行和保底）**：`serial_repl.py` 连接时进入 raw REPL，一次性注入 `BOOTSTRAP`（定义 `drive/aim/fire/halt` + `machine.Timer` 看门狗），之后每条指令只发一行函数调用，设备端不 sleep。

### 关键约定

- **时长由主机控制，backend 只设状态**：`drive(left, right, failsafe_ms)` / `fire(pulse_ms)` 只设置输出并启动设备端看门狗；executor 负责 sleep 后 halt。`failsafe_ms = 时长 + 300ms`，主机崩溃或拔 USB 后设备会自己停。新增 backend（如计划中的 Wi-Fi UDP）必须保留这套语义。
- 安全约束集中在 executor：单动作 ≤ 5s、队列总时长 ≤ 15s（超出就抛错拒绝）、速度裁剪到 `--max-speed`、发射每次 ≤ 3 发且间隔 1.5s 冷却、`--no-fire` 锁定。不要绕过 executor 直接调用 backend。
- 接线以车上 `rc_config` 为准（见 `docs/HANDOFF.md`「已确认事实」）：履带 MOTOR1 Pin4/5、MOTOR2 Pin6/7（反装），炮塔 SERVO1 Pin3（连续旋转舵机），发射扳机 SERVO2 Pin2（86° 松开 / 150° 扣下）；`BOOTSTRAP` 调用车上原厂驱动 `/bbl/motors.py`、`/bbl/servos.py`。左右和方向待架空实测。raw REPL 会打断原厂程序，退出后按 Core 复位键才能恢复实体遥控。

## Tests

`tests/` 覆盖 MCP 协议、executor（急停/限速/冷却）、raw REPL 解析（用假串口对象），以及 `test_session.py` 用本地 `websockets` 假平台跑通完整握手 → initialize → tools/list → tools/call。新增平台交互时优先扩展这个假平台测试。
