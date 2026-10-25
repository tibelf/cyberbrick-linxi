# 交接看板

> 每个 agent 收工前更新本文件并提交。按时间倒序记录，最新的写在最上面。规则见 `AGENTS.md`「交接协议」。

**最后更新**：2026-10-25 · Claude Code（自由闲逛）；之前：2026-10-24 · Claude Code（车 USB 接通，BOOTSTRAP 按真实接线重写）

## 当前状态

- `linx-client/` 已实现：灵矽 OTA 激活、WebSocket 会话、MCP 工具、动作队列、串口 raw REPL 驱动
- 技术方案已更新为 v2（接入灵矽），见 `docs/技术方案.md`
- 还没有接实车；**灵矽 mock 联调已打通**：文本「往前走两秒然后开一炮」→ 平台依次调用 move(forward, 2000) 和 fire(1)，并有语音回复

## 进行中

- **（2026-10-25）关了摄像头闲逛会被打断**：用户截图：开始闲逛后连续看不清，播报“看不清画面了，先停下来”就结束了。用户希望只有说停才停。改为：连续 3 次看不清 → 闭眼慢逛（`BLIND_SPEED=60`、每段 0.5–1.1s、一半概率先转 30–150°），后台每 4s 试一次摄像头，看清了播报“又看得清了”并回到边看边逛；没开 `--camera` 时 wander 也注册（一直闭眼慢逛，工具描述会说明）。**取舍**：闭眼时没有避障，靠低速 + 短段 + 常转向降低撞墙后一直顶着的风险；executor 的看门狗和急停照旧。另：截图里大模型把“【小车播报】”也念出来了，说明智控台里还是旧提示词，要重新贴。✅ 106 passed（一直看不清仍在逛且限速、无摄像头一开始就闭眼逛、摄像头恢复后回到看图模式）
- **（2026-10-25）“去撞目标却往反方向跑”**：用户实测。最可能是车顶 0 号标记贴反（箭头朝车尾）：程序把车尾当车头，转身“对准”后往前开就是远离目标。改动：①`MarkerTracker.flip`；`marker_seek` 往前开（偏角 < 25°）时，两次观测隔 ≥0.3s、距离变大超过 max(6cm, 10%) 就判定贴反，自动翻转并通过 `on_error` 提醒（网页红字 + 语音），本次运行之后的任务都按翻转算；②`marker-test` 显示 >150° 时提示可能贴反。仿真新增 `marker_reversed`。✅ 104 passed；正常贴法 + 手持晃动 + 3 进程并行 ×2 轮都没有误判。❓ 待用户确认是不是真的贴反了（也可能是别的原因：例如 udp 后端左右/前后接反——但之前实车 tank-test 已确认前进和左转方向正确）
- **（2026-10-25）摄像头视野变窄**：用户实测两个标记都能认出（0 号车顶 + 1 号贴在盒子上），但反馈“视角比手机里小很多”。原因：上一步请求 1920×1080，4:3 传感器被裁成 16:9；手机相机 App 里还可能用的是 0.5× 超广角，而连续互通相机走 1× 主摄。改动：`OpenCVCamera` 依次试 1920×1440 / 1440×1080 / 1920×1080 / 1280×720，挑宽 ≥1280 里最高的；`--camera 0:1920x1440` 可指定；标注图字号按画面放大并加白底。✅ 103 passed（假 VideoCapture 验证选分辨率）。❓ 连续互通相机实际支持哪些分辨率未知，看启动时打印的 “📷 摄像头 0：W×H”
- **（2026-10-25）标记“要么看不到 0 号、要么看不到 1 号”**：用户实测反馈。原因：0 号平贴车顶、1 号竖贴瓶子，镜头低时 0 号被压扁，镜头高时 1 号被压扁；加上 OpenCV 默认常常只给 640×480，标记只剩十几像素。仿真复现：同一低机位 640×480 只认出 1 号，1080p 两个都认得出。改动：`OpenCVCamera` 请求 1920×1080 并打印实际分辨率；`minMarkerPerimeterRate` 0.03→0.01；`marker-test` 显示分辨率和每个标记的边长像素（`detection_report`）。✅ 101 passed。建议用户：镜头 1–1.5m 内、仰角 30–60°；1 号可斜靠 45° 或改用颜色；iPhone 连续互通要在控制中心关掉「人物居中」
- **（2026-10-25）标记打印尺寸 + 两个稳定性修复**：用户打印预览里黑框只有约 4.7 cm——SVG 比 A4 宽，Chrome 自动缩小了。改为整页 ≤ 185×270mm（2 列，6 cm 时 165×199mm），放不下就报错；用 Chromium 按 A4 + 10mm 边距导出 PDF、254dpi 栅格化实测黑框 60.1mm。并行跑测试（CPU 紧张、帧率下降）时暴露两个真问题：①颜色目标会把车周围一圈像素整块抹掉，瓶子挨着车时被一起抹了 → 改为只排除中心落在车身上的色块；②单帧看错（标记位姿偶尔翻转）会误判“到了” → 到了/对准了要停下再看一眼确认。✅ 100 passed；3 个进程并行 ×2 轮 + 4 个占满 CPU 的进程下都稳定
- **（2026-10-25）ArUco 标记闭环寻的 `--marker`**：用户反馈视觉模型给坐标太模糊，走不到目的地；又问能否手拿手机拍。方案（用户确认）：车顶贴 0 号 ArUco 标记，目标贴 1–9 号标记或按颜色认，本地 OpenCV 每帧算“目标在车头左/右几度、多少厘米”（以车为参照，摄像头可以动），10Hz 差速边开边修正；视觉模型只在没标记时兜底、以及确认撞倒。新文件 `marker.py`、`marker_seek.py`、`tests/marker_sim.py`、`tests/test_marker.py`；executor 新增 `steer()`；`OpenCVCamera` 改为常开（顺带解决了每次打开偏暗/全黑）；新命令 `marker-print`、`marker-test`。✅ 98 passed（仿真：5 个机位 × 标记/颜色目标方位误差 < 5°、距离 < 10%；焦距估错 15% 方向仍 < 8°；手持晃动 3cm 下开到目标；原地对准不挪窝；撞倒；目标丢失就停；没标记退回视觉模型；急停）。marker-print 的 SVG 渲染后 4 个标记都能识别。❓ 未实测：真实摄像头的焦距（`--focal-ratio` 默认 0.8）、运动模糊、真实车的差速响应（`STEER_GAIN`、速度 65/80 能否带动）。**下一步**：`marker-print` 打印 → 量黑框边长 → 0 号贴车顶箭头朝车头、1 号贴瓶子 → `marker-test --camera 0 --target 1号` 看方位/距离对不对（用尺子量一下距离，偏多就调 `--focal-ratio`）→ `run --backend udp --camera 0 --marker --marker-targets "1=绿色瓶子" --web`
- **（2026-10-25）seek 改为“没完成不停”+ 视觉确认 + 撞倒**：用户截图：摄像头贴地侧拍，坦克中心 49,68、瓶子 57,72，相距 < 10% 就判“已经在旁边了”，实际还差一个多车身；"去把瓶子撞倒"也只是 approach。改动（`seek.py`）：①问外框而不是中心点，用底边中点 + 以车身为单位的间隙判断远近；②间隙 < 0.5 车身时追问视觉模型“是否已经碰到/紧挨着”，答“否”就继续往前蹭；③新增 goal=hit：挨着后全速冲 900ms、退 400ms，问“倒了没有”，最多撞 3 次；④不再 10 步就收工：120s 超时，偶尔看漏重拍（连续 3 次才放弃），卡住先后退+转 60° 脱困（2 次仍卡住才放弃）；⑤`say()` 加【小车播报】前缀——**实测平台把 text_response 当成一句用户输入显示在“你”那边，再由大模型复述**，提示词新增规则：看到前缀只转告、不调工具。✅ 75 passed。❓ 未实测：视觉模型给外框是否靠谱；是/否确认的准确率；撞的速度受 `--max-speed` 裁剪。**提示词要重新贴到智控台**
- **（2026-10-25）seek 出错时不再“卡住”不吭声**：用户截图：调用 `seek` 后再无照片、无播报。原因：任务里拍照/视觉接口抛异常时，`start_mission` 的 done 回调把异常吞掉了，没有打印也没有播报。改为 `_guard_mission`：出错就停车并走 `_report`（终端 ⚠️、网页红字、语音一句）；拍照加 10 秒超时（OpenCV 打不开、连续互通相机断开时会卡住）；网页「当前动作」显示正在跑的任务。✅ 70 passed。❓ 用户那次的真实原因还不知道，重跑后看报错
- **（2026-10-25）车没反应的排查**：用户反馈"有时候发了指令车没反应"。原因之一：工具调用立即回"已安排"，动作在后台失败（UDP 超时等）只打印在终端，大模型和用户都不知道。改动：①executor 记录 `last_error`，失败时 `on_error` 通知——网页控制台显示红色错误，并用 `session.say` 语音提醒（20 秒最多一次）；②`executor.check()` 真的给车发消息，`get_device_status` 先检查再返回（`link.online / latency_ms / uptime_s / rebooted`），空闲时每 5 秒心跳一次，网页「小车状态」新增「车」一栏；③车端新增 `stat` 指令回 `up=<开机秒数>`，开机时长变小 = 车中途重启过（怀疑电机启动电流拉低电压导致主控重启，见下一步 -8），旧版车端程序不认识 stat 时退回 ping；④UDP 车没应答时自动重新找车（DHCP 换 IP），10 秒内最多扫一次。✅ 69 passed（失败如实报告、离线只提醒一次、工具真的检查、假 UDP 车：重启检测/旧程序兼容/换 IP 重找）。❓ 未实测；**车端要 `wifi-setup --host <IP> --code-only` 重新部署才有 stat（重启检测）**；提示词新增一条，要用户贴到智控台。另一种"没反应"是大模型根本没调用工具：看终端/网页有没有 🔧 那一行
- **（2026-10-25）全双工外放防回声**：用户反馈"开启打断后，AI 回复的语音又被当成输入"。原因：`--duplex` 时小车说话期间麦克风原样上传，外放的 TTS 被平台识别成用户的话，音量阈值也会被回声触发自我打断。改为（`linx_client/echo.py`）：①小车说话期间只给平台传静音；②本地统计「麦克风音量/播放音量」的中位数作为回声增益，麦克风超过预期回声 3 倍、持续 120ms 才算插话（前 0.5s 还没学到增益时不判断）；③确认插话后打断，并补传判定插话的那几帧原声；④声卡里最后约 0.4s 余音也算"还在说话"（半双工同样受益）。`--barge-in-level 0` = 不做判断、原样上传（只适合戴耳机）。✅ 63 passed（回声不触发、真人声触发、句间停顿用下限、回声帧一帧不上传、插话原声补传、余音期间不上传）。❓ 未实测：外放音量很大或麦克风离喇叭很近时，人声未必能比回声大 3 倍，那时只能戴耳机或用系统自带的回声消除
- **（2026-10-25）闲逛改为流水线、步子加大**：用户反馈"走一小步停一下很呆"。现在边走边拍照分析，下一段提前排队（每段 1.2–2.5s），队列不空、车不停；前方被挡立刻刹车转向；看不清立刻停车（不闭眼开）。executor 新增 `Action.kind / turn_deg` 和 `motion_epoch / idle_epoch / turn_total / queued / busy`，供任务判断两张照片之间车做了什么。✅ 56 passed（连跑 3 遍稳定），新增：段与段之间不停顿、前方堵住不等走完就转向。❓ 未实测：车在运动中拍的照片会有些模糊；视觉接口约 1s/次，一段 1.2s 内大约能看 1 次，速度 70 下避障来不来得及要实车看
- **（2026-10-25）自由闲逛 `self.tank.wander`**：用户要求"一直逛、随意做动作、看摄像头别撞东西别进死胡同，只有我说停才停"。实现见 `linx_client/wander.py`（规则写在 AGENTS.md 的 Architecture）。同时改了两处语义：①新任务接替旧任务（闲逛中说"去瓶子那里"直接切过去），不再报"先说停"；②`emote(from_mission=True)` 允许任务自己做情绪小动作。✅ 54 passed（一直逛到 stop、撞到后退、不出画面边缘、前视转向空的一侧、看不清就停、断线暂停/重连继续、偶发异常不结束任务、绝不开火）。❓ 未实测：视觉模型给"空地方向"是否靠谱；外部摄像头下"画面方向"受透视影响，建议摄像头抬高。**下一步**：`run --backend udp --camera <来源> --web`，说"自己逛逛"，观察网页控制台每一步的照片和回答
- **（2026-10-25）外部摄像头寻的改为「位置 + 位移估朝向」**：用户实测（手机支架约 5cm 高，侧拍）时，问"目标在几点钟方向"模型每次都答 4 点钟、距离也明显偏小，车原地打转。原因：视觉模型判断不了车头朝向，而且提示词谎称"正上方俯拍"。改为只问 `坦克=x,y；目标=x,y`（百分比坐标）；先试探前进 500ms，用前后两张照片里坦克的位移当车头方向，算出与"坦克→目标"方向的夹角后转向再走；前进时顺便标定"画面移动 1% 要多少毫秒"来估下一步时长；坦克和目标相距 < 10% 算走到；连续两次看不出位移就放弃并提示"摄像头太低/太远或车卡住"。假设画面没有左右镜像（手机用后置摄像头）。✅ 43 passed。❓ 未实测。**建议用户把手机抬高到 40–60cm 斜着往下拍**，5cm 侧拍时纵向距离被压缩得太厉害
- **（2026-10-25）看图闭环寻的 `self.tank.seek(target, goal=approach|aim, fire=false)`**：用户反馈"问一句只执行一次，不能一直微调"——平台一轮对话只做一次工具调用，大模型看不到转完后的画面。改为本地闭环（`linx_client/seek.py`）：拍照 → 视觉模型按固定格式回答 → 换算成 turn/move → `wait_idle()` 等动完 → 再拍，最多 8 步；俯视按钟点（每钟点 30°），前视按偏角（±8° 内算对准），前视看不到目标时每次右转 45° 扫描一圈。`fire=true` 只在对准成功后打一发，受 `--no-fire` 和冷却约束。说"停"/急停会取消整个任务。✅ 单元测试（脚本化视觉回答 + mock 后端：俯视转向后走到、前视扫描后对准开火、发射锁定如实播报、急停取消任务、任务期间不插情绪动作；全部 40 passed）。❓ 未实测：①`session.say()` 用 `listen.detect` + `text_response` 主动播报，平台是否真的念出来；②视觉模型是否稳定按格式回答；③`CAR_LEN_MS=600`（速度 80 走一个车身长的毫秒数）是猜的，需要量一下。**下一步**：`run --backend udp --camera 0 --web`，说"去红色杯子那里"，看网页控制台里每一步的照片和回答
- **（2026-10-25）拍照工具 `self.camera.take_photo` 已实现**：`linx_client/camera.py` + `tools.register_camera_tools`，`run --camera <来源>` 启用（不加就不注册这个工具）。来源：数字 = 电脑摄像头（需 `pip install opencv-python`，macOS 要给终端摄像头权限；iPhone 连续互通相机也会作为一个序号出现），`http://…` = 返回 JPEG 的快照地址（留给车载 XIAO ESP32S3 Sense），其他 = 图片文件。上传前缩到 640 宽。`camera-test --camera 0` 只拍照存文件，不连平台。工具描述和提示词里写了瞄准流程：拍照问偏左/偏右多少度 → turn → 再拍确认；**拍照不等于可以开火**。✅ 单元测试（本地假视觉接口校验 multipart 字段、请求头、MCP 调用链路，共 28 passed）。❓ **未在真实平台和真摄像头上验证**：下一步执行 `camera-test --camera 0` 看照片，再 `run --backend udp --camera 0` 说「看看前面有什么」。**补充（2026-10-25）**：`explain` 和 HTTP 快照不走系统代理（开着 Clash 时，到七牛的上传会被掐断）；`run --web --camera` 时，网页控制台会显示拍到的照片和分析结果。另一个会话同时做了一份重复实现（`vision.py`，俯视摄像头 + 车顶箭头），已放弃，以本实现为准。**用户选定俯视**：新增 `--camera-view overhead|front`（默认 overhead）。俯视时工具描述以炮管指向为车头（贴了箭头则以箭头为准；分不清时先前进 500ms 再拍，看移动方向），用「以车头为 12 点钟，目标在几点钟方向、离车几个车身长」来问，每差一个钟点转 30°，边走边拍照确认；front 保留原来的「偏左偏右多少度」说法，给车载摄像头用
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

- **灵矽支持视觉（2026-10-25 实测）**：智控台「对话与视觉 → 视觉分析」开启并选好视觉模型后，平台在 MCP `initialize` 的 `params.capabilities.vision` 里下发 `{url, token}`（url = `http://xrobo-io.qiniuapi.com/mcp/vision/explain2`；平台自称 `XiaozhiClient`，协议同小智）。**OTA 响应里没有视觉字段**，之前据此认为不支持，判断错了。调用方式同小智 `Esp32Camera::Explain`：POST multipart，字段 `question` + `file`（`camera.jpg`，image/jpeg），请求头 `Authorization: Bearer <token>`、`Device-Id`、`Client-Id`。返回 `{"action":"RESPONSE","response":"<分析文字>","success":true}`，实测 0.8s。`mcp.py` 现在会保存 initialize 的 capabilities（`McpServer.vision`），拍照工具已实现（见「进行中」）

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
