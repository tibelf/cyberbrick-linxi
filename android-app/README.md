# CyberBrick 原生 Android 遥控器

Kotlin + Jetpack Compose 原生界面，直接复用已部署的云端 `/control` WebSocket；不是网页套壳，不需要修改小车固件。摄像头功能按用户要求暂停。

## 使用

1. 在 Android 8.0 或更新的手机安装 APK。下载地址为现有 HTTPS 入口的 `/android/cyberbrick-v0.1.0.apk`，仍需原有用户名和密码。
2. 先断开旧网页控制端，在 App「连接」填写现有云端地址、用户名 `car` 与控制密码。仓库和 APK 不包含密码或灵矽 Device-Id。
3. 小车开机并连接原有 2.4 GHz 热点；页面显示真实小车在线后，可按住方向键移动、松手停车。
4. 「语音」点击开始说话，首次允许麦克风权限，再次点击结束；音量条、计时、识别与回复来自实际链路。回复期间不能继续收音。
5. 炮台每次点击上/下微调 200 ms；显式启用发射后才可单发。停车、断线和切后台重新锁定。

同一时刻只允许一个控制端。切后台会停止收音和播放，发送停车与锁定后断开；回到前台自动重连，但不会补发上次动作，也不会自动开启麦克风或发射。

「记住密码」默认关闭；启用时用 Android Keystore 的 AES-GCM 加密后保存在本机，取消勾选立即删除已保存密文。App 禁止备份，只接受正常证书验证通过的 HTTPS/WSS 地址。

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
- `NativeAudio.kt`：AudioRecord 收音、AudioTrack 播放、音频焦点与半双工。
- `ConfigStore.kt`：可选的 Keystore 密码保存。
- `RemoteViewModel.kt`：生命周期与状态协调。
- `RemoteApp.kt`、`ControlScreens.kt`、`ConnectionScreen.kt`、`Appearance.kt`：原生界面与灰黑黄色设计。

`assembleDebug`、10 项本地 WSS 协议测试与 `lintDebug` 已通过；Lint 0 错误、9 个版本/风格建议。APK 的 v2 签名验证通过，约 13 MB，Android 8.0+。协议测试涵盖认证、租约过期停车、单发锁定与冷却、PCM 帧与静音、回复停麦、后台/断线重连不重放和未知 CA 拒绝，均不连接实车。

真机安装、原生麦克风/扬声器、实际云端语音和控车仍待实测。用户已开启调试并重新插线，但 Mac 的 ADB 暂未识别手机；本机 API 37.2 模拟器两种渲染模式均在系统启动时崩溃，未用于声称 App 启动成功。
