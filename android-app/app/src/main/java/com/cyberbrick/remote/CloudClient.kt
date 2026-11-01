package com.cyberbrick.remote

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import okhttp3.Credentials
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.Closeable
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException

data class ConnectionConfig(
    val server: String = "https://110.40.227.30",
    val username: String = "car",
    val password: String = "",
) {
    fun endpoint(): HttpUrl {
        val url = server.trim().toHttpUrlOrNull() ?: error("请输入完整的 HTTPS 服务器地址")
        require(url.isHttps && url.username.isEmpty() && url.password.isEmpty() &&
            url.encodedPath == "/" && url.query == null && url.fragment == null) {
            "服务器地址只能使用 HTTPS，不能带账号、路径或查询参数"
        }
        require(username.isNotBlank() && ':' !in username && password.isNotEmpty()) { "请填写用户名和控制密码" }
        return url
    }
    override fun toString() = "ConnectionConfig(server=$server, username=$username, password=已隐藏)"
}

enum class LinkPhase { DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING }

data class AudioSpec(val rate: Int)

data class CloudState(
    val phase: LinkPhase = LinkPhase.DISCONNECTED,
    val realCar: Boolean = true,
    val carOnline: Boolean = false,
    val linx: String = "offline",
    val audio: AudioSpec? = null,
    val speaking: Boolean = false,
    val fireAvailable: Boolean = false,
    val armed: Boolean = false,
    val fireReason: String = "连接后显示发射状态",
    val cooldownMs: Long = 0,
    val motion: String = "准备就绪",
    val userText: String = "",
    val botText: String = "",
    val message: String = "请先连接云端",
    val error: String? = null,
) {
    val connected get() = phase == LinkPhase.CONNECTED
    val canControl get() = connected && carOnline
}

/** 复用云端协议，凭据只在请求头与认证消息中使用；任何重连都不重放动作。 */
class CloudClient(
    private val http: OkHttpClient = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS).writeTimeout(5, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false).pingInterval(10, TimeUnit.SECONDS).build(),
    private val now: () -> Long = { System.nanoTime() / 1_000_000 },
) : Closeable {
    private val lock = Any()
    private val executor = Executors.newSingleThreadScheduledExecutor { task -> Thread(task, "云端控制时钟").apply { isDaemon = true } }
    private val mutable = MutableStateFlow(CloudState())
    val state = mutable.asStateFlow()
    var onAudioFormat: (AudioSpec) -> Unit = {}
    var onPcm: (ByteArray) -> Unit = {}
    var onAudioFlush: () -> Unit = {}
    var onLost: () -> Unit = {}
    var onSpeaking: () -> Unit = {}
    private var socket: WebSocket? = null
    private var desired: ConnectionConfig? = null
    private var generation = 0
    private var foreground = true
    private var retryCount = 0
    private var retry: ScheduledFuture<*>? = null
    private var opening: ScheduledFuture<*>? = null
    private var lease: String? = null
    private var leaseAt = Long.MIN_VALUE
    private var manual: String? = null
    private var micLive = false
    private var shotUntil = 0L
    private val silence = ByteArray(640).toByteString()

    init {
        executor.scheduleWithFixedDelay({
            synchronized(lock) {
                if (foreground && mutable.value.connected && mutable.value.audio != null && !micLive) raw(silence)
            }
        }, 20, 20, TimeUnit.MILLISECONDS)
        executor.scheduleWithFixedDelay({
            synchronized(lock) {
                manual?.let { direction ->
                    if (canAct()) command("drive", "direction" to direction)
                    else stopLocked("控制连接过期，已停止")
                }
                val remaining = (shotUntil - now()).coerceAtLeast(0)
                if (remaining != mutable.value.cooldownMs) mutable.update { it.copy(cooldownMs = remaining) }
            }
        }, 100, 120, TimeUnit.MILLISECONDS)
    }

    fun connect(config: ConnectionConfig) {
        try { config.endpoint() } catch (error: IllegalArgumentException) { report(error.message ?: "连接信息无效"); return }
        catch (error: IllegalStateException) { report(error.message ?: "连接信息无效"); return }
        disconnect()
        synchronized(lock) { desired = config; retryCount = 0; if (foreground) open(config) }
    }

    private fun open(config: ConnectionConfig) {
        val id = ++generation
        lease = null; manual = null; micLive = false
        mutable.value = CloudState(phase = LinkPhase.CONNECTING, message = "正在连接云端")
        val base = config.endpoint()
        val request = Request.Builder().url(base.newBuilder().encodedPath("/control").build())
            .header("Origin", base.toString().removeSuffix("/"))
            .header("Authorization", Credentials.basic(config.username, config.password)).build()
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                synchronized(lock) {
                    if (id != generation || !foreground) { webSocket.cancel(); return }
                    webSocket.send(JSONObject().put("type", "auth").put("token", config.password).toString())
                }
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (!current(id)) return
                try { message(id, JSONObject(text)) }
                catch (_: Exception) { failed(id, "收到无法解析的云端消息", false) }
            }
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                if (current(id) && mutable.value.audio != null) onPcm(bytes.toByteArray())
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
                finish(code, id)
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = finish(code, id)
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val reason = when {
                    response?.code == 401 -> "用户名或控制密码不正确"
                    response?.code == 403 -> "服务器拒绝连接，请检查服务器地址"
                    response?.code == 429 -> "连接过于频繁，请稍后重试"
                    t is SSLException -> "证书验证失败，请检查服务器证书和手机时间"
                    else -> "网络连接中断，正在尝试恢复"
                }
                failed(id, reason, response?.code !in listOf(401, 403) && t !is SSLException)
            }
        })
        opening = executor.schedule({ if (current(id) && !mutable.value.connected) failed(id, "连接云端超时", true) }, 16, TimeUnit.SECONDS)
    }

    private fun current(id: Int) = synchronized(lock) { id == generation && foreground }

    private fun finish(code: Int, id: Int) {
        val reason = when (code) {
            4001 -> "控制密码验证失败"
            4002 -> "已有控制端在线，请先断开网页或其他手机"
            4008 -> "控制连接超时，正在重连"
            1000 -> "连接已关闭"
            else -> "云端连接中断，正在重连"
        }
        failed(id, reason, code !in listOf(4001, 4002))
    }

    private fun failed(id: Int, reason: String, retryable: Boolean) {
        synchronized(lock) {
            if (id != generation) return
            generation++; socket?.cancel(); socket = null
            opening?.cancel(false); retry?.cancel(false)
            lease = null; manual = null; micLive = false; shotUntil = 0
            val resume = retryable && foreground && desired != null
            mutable.value = mutable.value.copy(phase = if (resume) LinkPhase.RECONNECTING else LinkPhase.DISCONNECTED,
                carOnline = false, linx = "offline", audio = null, armed = false, fireAvailable = false,
                speaking = false, cooldownMs = 0, motion = "已暂停控制", message = reason, error = reason)
            if (resume) {
                val expected = generation
                val delay = (1L shl retryCount.coerceAtMost(4)).coerceAtMost(15)
                retryCount++
                retry = executor.schedule({
                    synchronized(lock) { if (expected == generation && foreground) desired?.let { open(it) } }
                }, delay, TimeUnit.SECONDS)
            }
        }
        onLost()
    }

    private fun message(id: Int, msg: JSONObject) {
        var format: AudioSpec? = null
        var flush = false
        var lost = false
        var speaking = false
        synchronized(lock) {
            if (id != generation || !foreground) return
            when (msg.optString("type")) {
                "ready" -> {
                    opening?.cancel(false); retryCount = 0
                    mutable.update { it.copy(phase = LinkPhase.CONNECTED, realCar = msg.optString("mode") == "真实小车",
                        carOnline = msg.optBoolean("car_online"), linx = msg.optString("linx", "offline"),
                        message = "云端已连接", error = null) }
                    fireState(msg.optJSONObject("fire") ?: JSONObject())
                }
                "lease" -> { lease = msg.optString("id"); leaseAt = now(); command("presence") }
                "car_status" -> {
                    val online = msg.optBoolean("online")
                    mutable.update { it.copy(carOnline = online, message = if (online) "小车已上线" else "小车暂时离线",
                        armed = it.armed && online, motion = if (online) "准备就绪" else "已暂停控制") }
                    if (!online) { manual = null; micLive = false; command("mic", "on" to false); lost = true }
                }
                "fire_state" -> fireState(msg)
                "audio_format" -> {
                    val rate = msg.optInt("sample_rate")
                    require(msg.optString("format") == "pcm" && msg.optInt("channels") == 1 && msg.optInt("bit_depth") == 16 && rate in 8000..48000)
                    format = AudioSpec(rate)
                    mutable.update { it.copy(audio = format) }
                }
                "audio_flush" -> flush = true
                "linx_event" -> {
                    val event = msg.optJSONObject("event") ?: return
                    when (event.optString("type")) {
                        "conn" -> {
                            val status = event.optString("state", "offline")
                            mutable.update { it.copy(linx = status, audio = if (status == "connected") it.audio else null,
                                speaking = status == "connected" && it.speaking,
                                message = if (status == "connected") "灵矽已连接" else "灵矽语音连接中") }
                            if (status != "connected") { micLive = false; lost = true }
                        }
                        "user" -> mutable.update { it.copy(userText = event.optString("text")) }
                        "bot" -> mutable.update { it.copy(botText = event.optString("text")) }
                        "speaking" -> {
                            speaking = event.optBoolean("on")
                            mutable.update { it.copy(speaking = speaking) }
                            if (speaking) { micLive = false; command("mic", "on" to false) }
                        }
                    }
                }
                "activation" -> {
                    val code = if (msg.isNull("code")) "" else msg.optString("code")
                    mutable.update { it.copy(message = if (code.matches(Regex("[0-9]{6}")))
                        "请先在灵矽设备管理绑定激活码：$code"
                        else if (it.linx == "connected") "灵矽已连接" else "正在连接灵矽") }
                }
                "action_result" -> {
                    if (msg.optString("action") == "fire") shotUntil = now() + msg.optLong("cooldown_ms", 2000).coerceIn(0, 10_000)
                    mutable.update { it.copy(message = msg.optString("message"), error = null) }
                }
                "error" -> report(msg.optString("message", "云端操作失败"))
            }
        }
        format?.let(onAudioFormat)
        if (flush) onAudioFlush()
        if (lost) onLost()
        if (speaking) onSpeaking()
    }

    private fun fireState(msg: JSONObject) {
        mutable.update { it.copy(fireAvailable = msg.optBoolean("available"), armed = msg.optBoolean("armed"),
            fireReason = msg.optString("reason", "发射已锁定")) }
    }

    private fun canAct() = foreground && mutable.value.canControl &&
        (!mutable.value.realCar || (lease != null && now() - leaseAt in 0..800))

    /** 每次动作带本次租约；旧连接和过期消息不能变成稍后执行的动作。 */
    private fun command(type: String, vararg args: Pair<String, Any>): Boolean {
        val ws = socket ?: return false
        if (ws.queueSize() >= 65536) { ws.cancel(); return false }
        val msg = JSONObject().put("type", type)
        lease?.let { msg.put("lease", it) }
        args.forEach { (key, value) -> msg.put(key, value) }
        return ws.send(msg.toString())
    }

    private fun raw(frame: ByteString): Boolean {
        val ws = socket ?: return false
        if (ws.queueSize() >= 65536) { ws.cancel(); return false }
        return ws.send(frame)
    }

    fun drive(direction: String) = synchronized(lock) {
        require(direction in listOf("forward", "backward", "left", "right"))
        if (!canAct()) { report("小车或控制连接尚未就绪"); return@synchronized }
        manual = direction
        command("drive", "direction" to direction)
        mutable.update { it.copy(motion = when (direction) { "forward" -> "前进中"; "backward" -> "后退中"; "left" -> "左转中"; else -> "右转中" }, error = null) }
    }

    fun stop() = synchronized(lock) { stopLocked("已发送停止 · 发射已锁定") }
    private fun stopLocked(reason: String) {
        manual = null; command("stop")
        mutable.update { it.copy(armed = false, motion = "已停止操作", message = reason) }
    }

    fun barrel(direction: String) = synchronized(lock) {
        require(direction in listOf("up", "down"))
        if (!canAct()) { report("小车或控制连接尚未就绪"); return@synchronized }
        manual = null; command("barrel", "direction" to direction)
    }

    fun arm(on: Boolean) = synchronized(lock) {
        if (on && (!canAct() || !mutable.value.fireAvailable)) { report("小车暂不能启用发射"); return@synchronized }
        if (!on) mutable.update { it.copy(armed = false) }
        command("fire_arm", "on" to on)
    }

    fun fire() = synchronized(lock) {
        if (!canAct() || !mutable.value.armed) { report("请先连接小车并启用发射"); return@synchronized }
        if (now() < shotUntil) return@synchronized
        manual = null
        if (command("fire")) {
            shotUntil = now() + 2000
            mutable.update { it.copy(cooldownMs = 2000, message = "正在请求单发", error = null) }
        }
    }

    fun microphone(on: Boolean): Boolean = synchronized(lock) {
        if (on && (!canAct() || mutable.value.audio == null || mutable.value.speaking)) return@synchronized false
        val sent = command("mic", "on" to on)
        micLive = on && sent
        if (micLive) mutable.update { it.copy(userText = "", botText = "", error = null) }
        sent
    }

    fun pcm(frame: ByteArray): Boolean = synchronized(lock) {
        require(frame.size == 640)
        if (!micLive || !foreground || !mutable.value.connected) return@synchronized false
        raw(frame.toByteString())
    }

    fun report(message: String) { mutable.update { it.copy(error = message, message = message) } }
    fun clearError() { mutable.update { it.copy(error = null) } }

    fun setForeground(value: Boolean) {
        synchronized(lock) {
            if (foreground == value) return
            foreground = value
            if (!value) endConnection("已暂停，返回 App 后重连")
            else desired?.let { open(it) }
        }
        if (!value) onLost()
    }

    fun disconnect() {
        synchronized(lock) { desired = null; retryCount = 0; endConnection("连接已断开") }
        onLost()
    }

    private fun endConnection(reason: String) {
        retry?.cancel(false); opening?.cancel(false)
        manual = null; micLive = false; lease = null; shotUntil = 0
        command("mic", "on" to false); command("fire_arm", "on" to false); command("stop")
        generation++
        val closing = socket
        socket = null
        closing?.close(1000, "控制端离开")
        if (closing != null) executor.schedule({ closing.cancel() }, 300, TimeUnit.MILLISECONDS)
        mutable.value = CloudState(message = reason)
    }

    override fun close() {
        disconnect(); executor.shutdown()
        http.dispatcher.executorService.shutdown()
        http.connectionPool.evictAll()
    }
}
