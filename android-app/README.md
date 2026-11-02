# CyberBrick 原生 Android 遥控器

Kotlin + Jetpack Compose 原生界面，直接复用已部署的云端 `/control` WebSocket；不是网页套壳，不需要修改小车固件。摄像头功能按用户要求暂停。

## 使用

1. 将本机打包的 `dist/cyberbrick-v0.2.1.apk` 传到 Android 8.0 或更新的手机，覆盖安装旧版。按用户要求，0.2.1 不上传服务器。
2. 先断开旧网页控制端。App 默认记住密码，已保存密码时打开即自动连接；旧版没有保存密码时，需要在「连接」填写一次原控制密码。仓库和 APK 不包含密码或灵矽 Device-Id。
3. 小车开机并连接原有 2.4 GHz 热点；页面显示真实小车在线后，按住圆形摇杆拖动，松手回中并停车。两侧的数字和色条表示当前请求力度，不是履带传感器读数。
4. 「语音」点击开始说话，首次允许麦克风权限，再次点击结束；音量条、计时、识别与回复来自实际链路。回复期间不能继续收音。
5. 炮台每次点击上/下微调 200 ms；显式启用发射后才可单发。停车、断线和切后台重新锁定。

同一时刻只允许一个控制端。切后台会停止收音和播放，发送停车与锁定后断开；回到前台自动重连，但不会补发上次动作，也不会自动开启麦克风或发射。

「记住密码，下次自动连接」默认开启，连接成功后用 Android Keystore 的 AES-GCM 加密保存在本机；取消勾选立即删除密文，并在下次启动时保持关闭。App 禁止备份，只接受正常证书验证通过的 HTTPS/WSS 地址。

## 构建

需要 JDK 17 或更新版本及 Android SDK 37（运行目标为 Android 8.0+，target SDK 36）：

```bash
cd android-app
./gradlew assembleDebug
./gradlew testDebugUnitTest lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

输出 `app/build/outputs/apk/debug/app-debug.apk` 为开发测试签名，正式发布需配置独立签名密钥。`local.properties`、本机签名密钥和构建产物不入库。

## 结构

- `CloudClient.kt`：认证、租约、指令、音频帧、状态、重连与前后台控制。
- `JoystickMath.kt`：参考网页摇杆的死区、圆形限制和双履带差速，受当前 ±40 限速约束。
- `NativeAudio.kt`：AudioRecord 收音、AudioTrack 播放、音频焦点与半双工。
- `ConfigStore.kt`：可选的 Keystore 密码保存。
- `RemoteViewModel.kt`：生命周期与状态协调。
- `RemoteApp.kt`、`ControlScreens.kt`、`ConnectionScreen.kt`、`Appearance.kt`：原生界面与灰黑黄色设计。

0.2.1 的 `assembleDebug`、18 项本地原生测试与 `lintDebug` 已通过；Lint 0 错误、9 个版本/风格建议。APK 的 v2 签名与 0.1.0 一致，约 12 MB，Android 8.0+。冷启动自动连接遇到旧会话短暂占用时有限重试，持续占用时停止尝试。原生网络模块通过临时 TLS 假云端控制模拟车，差速、限速和松手停车符合预期；另有 36 项云端/车端/音频/执行器测试、1 项网关停车补充测试及 9 项旧网页测试通过，均不连接实车。此前 0.2.0 已上传腾讯云，0.2.1 仅本地交付。

0.1.0 曾获用户确认“已安装，能看到连接界面”和“小车在线，能听到回复”。0.2.1 的手机安装、自动连接、摇杆手感及实车动作仍待验证；不能把 0.1.0 的语音或本地模拟测试当作新版实测。Mac 的 ADB 暂未识别手机；本机 API 37.2 模拟器在系统启动时崩溃。
