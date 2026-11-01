package com.cyberbrick.remote

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.io.Closeable
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.abs

data class NativeAudioState(
    val preparing: Boolean = false,
    val recording: Boolean = false,
    val level: Float = 0f,
    val seconds: Int = 0,
    val playing: Boolean = false,
)

/** 原生 PCM 收放音：16k/单声道/16位/20ms 上行，下行按云端协商的采样率播放。 */
class NativeAudio(private val context: Context, private val cloud: CloudClient) : Closeable {
    private val mutable = MutableStateFlow(NativeAudioState())
    val state = mutable.asStateFlow()
    private val capture = Executors.newSingleThreadExecutor { Thread(it, "原生麦克风") }
    private val playerExecutor = Executors.newSingleThreadExecutor { Thread(it, "原生语音播放") }
    private val clock = Executors.newSingleThreadScheduledExecutor { Thread(it, "音频状态时钟") }
    private val recordEpoch = AtomicInteger()
    private val playEpoch = AtomicInteger()
    @Volatile private var recorder: AudioRecord? = null
    @Volatile private var player: AudioTrack? = null
    @Volatile private var running = true
    @Volatile private var startedAt = 0L
    @Volatile private var framesRead = 0
    @Volatile private var playbackUntil = 0L
    @Volatile private var spec: AudioSpec? = null
    private val packets = ArrayBlockingQueue<Packet>(32)
    private val manager = context.getSystemService(AudioManager::class.java)
    @Volatile private var hasFocus = false
    private val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
        .setAudioAttributes(attributes)
        .setOnAudioFocusChangeListener({ change ->
            if (change < 0) { hasFocus = false; stopRecording(); flush(); cloud.stop() }
        }, Handler(Looper.getMainLooper())).build()

    init {
        cloud.onAudioFormat = { next -> spec = next; flush() }
        cloud.onPcm = ::play
        cloud.onAudioFlush = ::flush
        cloud.onLost = { stopRecording(); flush(); spec = cloud.state.value.audio }
        cloud.onSpeaking = ::stopRecording
        clock.scheduleAtFixedRate({
            val now = SystemClock.elapsedRealtime()
            val recording = mutable.value.recording
            if (recording) {
                val seconds = ((now - startedAt) / 1000).toInt()
                mutable.update { it.copy(seconds = seconds) }
                if (framesRead == 0 && seconds >= 2) { stopRecording(); cloud.report("没有收到麦克风音频，请检查设备与权限") }
                else if (seconds >= 30) { stopRecording(); cloud.report("已录音 30 秒，收音已结束") }
            }
            val playing = now < playbackUntil + 450 && playbackUntil > 0
            if (mutable.value.playing != playing) mutable.update { it.copy(playing = playing) }
            if (!playing && !recording && !mutable.value.preparing && hasFocus) {
                manager.abandonAudioFocusRequest(focus); hasFocus = false
            }
        }, 100, 100, TimeUnit.MILLISECONDS)
        playerExecutor.execute(::playLoop)
    }

    private fun focus(): Boolean {
        if (hasFocus) return true
        hasFocus = manager.requestAudioFocus(focus) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        return hasFocus
    }

    @Synchronized
    fun startRecording() {
        if (mutable.value.recording || mutable.value.preparing) return
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            cloud.report("请允许麦克风权限后再说话"); return
        }
        if (!cloud.state.value.canControl || spec == null) { cloud.report("请先连接小车与灵矽语音"); return }
        if (cloud.state.value.speaking || mutable.value.playing) { cloud.report("请等回复播完后再说话"); return }
        val epoch = recordEpoch.incrementAndGet()
        mutable.update { it.copy(preparing = true, seconds = 0, level = 0f) }
        capture.execute {
            var input: AudioRecord? = null
            var echo: AcousticEchoCanceler? = null
            var noise: NoiseSuppressor? = null
            try {
                check(focus()) { "暂时无法使用音频，请结束通话后重试" }
                val minimum = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
                check(minimum > 0) { "手机不支持所需的麦克风格式" }
                input = AudioRecord.Builder().setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
                    .setAudioFormat(AudioFormat.Builder().setSampleRate(16000).setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                    .setBufferSizeInBytes(maxOf(minimum * 2, 640 * 8)).build()
                check(input.state == AudioRecord.STATE_INITIALIZED) { "麦克风初始化失败" }
                if (epoch != recordEpoch.get()) return@execute
                recorder = input
                echo = runCatching { if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(input.audioSessionId)?.apply { enabled = true } else null }.getOrNull()
                noise = runCatching { if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(input.audioSessionId)?.apply { enabled = true } else null }.getOrNull()
                input.startRecording()
                check(input.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "麦克风未开始录音" }
                synchronized(this@NativeAudio) {
                    if (epoch != recordEpoch.get()) return@execute
                    check(cloud.microphone(true)) { "语音连接暂不可用，请重试" }
                    startedAt = SystemClock.elapsedRealtime(); framesRead = 0
                    mutable.update { it.copy(preparing = false, recording = true) }
                }
                val frame = ByteArray(640)
                var offset = 0
                while (running && epoch == recordEpoch.get()) {
                    val n = input.read(frame, offset, frame.size - offset, AudioRecord.READ_BLOCKING)
                    if (epoch != recordEpoch.get()) break
                    check(n > 0) { "麦克风音频读取失败" }
                    offset += n
                    if (offset < frame.size) continue
                    if (!cloud.pcm(frame)) error("语音上传中断，请检查连接后重试")
                    framesRead++
                    var peak = 0
                    for (i in frame.indices step 2) {
                        val value = ((frame[i].toInt() and 255) or (frame[i + 1].toInt() shl 8)).toShort().toInt()
                        peak = maxOf(peak, abs(value))
                    }
                    mutable.update { it.copy(level = (peak / 32768f * 4).coerceIn(0f, 1f)) }
                    offset = 0
                }
            } catch (error: Exception) {
                if (epoch == recordEpoch.get()) cloud.report(if (error is SecurityException) "麦克风权限不可用，请在系统设置中允许" else error.message ?: "麦克风启动失败")
            } finally {
                runCatching { input?.stop() }; echo?.release(); noise?.release(); input?.release()
                if (recorder === input) recorder = null
                if (epoch == recordEpoch.get()) {
                    cloud.microphone(false)
                    mutable.update { it.copy(preparing = false, recording = false, level = 0f) }
                }
            }
        }
    }

    @Synchronized
    fun stopRecording() {
        recordEpoch.incrementAndGet()
        cloud.microphone(false)
        runCatching { recorder?.stop() }
        mutable.update { it.copy(preparing = false, recording = false, level = 0f) }
    }

    private data class Packet(val bytes: ByteArray, val spec: AudioSpec, val epoch: Int)

    @Synchronized
    private fun play(bytes: ByteArray) {
        val format = spec ?: return
        if (bytes.isEmpty() || bytes.size % 2 != 0) { cloud.report("收到的回复音频格式无效"); return }
        val now = SystemClock.elapsedRealtime()
        if (playbackUntil - now > 2500 || bytes.size > format.rate * 4) {
            flush(); cloud.report("音频缓冲过多，已清除延迟的回复"); return
        }
        if (!packets.offer(Packet(bytes, format, playEpoch.get()))) {
            flush(); cloud.report("音频缓冲已满，已清除延迟的回复"); return
        }
        playbackUntil = maxOf(playbackUntil, now) + bytes.size * 1000L / (format.rate * 2)
        mutable.update { it.copy(playing = true) }
    }

    private fun playLoop() {
        var rate = 0
        try {
            while (running) {
                val packet = packets.poll(300, TimeUnit.MILLISECONDS) ?: continue
                try {
                if (packet.epoch != playEpoch.get()) continue
                if (!focus()) { cloud.report("无法播放回复，请结束通话后重试"); flush(); continue }
                if (player == null || rate != packet.spec.rate) {
                    player?.release()
                    rate = packet.spec.rate
                    val minimum = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
                    check(minimum > 0) { "手机不支持回复音频格式" }
                    player = AudioTrack.Builder().setAudioAttributes(attributes)
                        .setAudioFormat(AudioFormat.Builder().setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                        .setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(maxOf(minimum * 2, rate / 5 * 2)).build()
                }
                val output = player ?: continue
                synchronized(this) {
                    if (packet.epoch != playEpoch.get()) continue
                    if (output.playState != AudioTrack.PLAYSTATE_PLAYING) output.play()
                }
                var offset = 0
                while (running && packet.epoch == playEpoch.get() && offset < packet.bytes.size) {
                    val written = output.write(packet.bytes, offset, packet.bytes.size - offset, AudioTrack.WRITE_BLOCKING)
                    check(written > 0 || packet.epoch != playEpoch.get()) { "语音播放设备不可用" }
                    if(written <= 0) break
                    offset += written
                }
                } catch (_: Exception) {
                    runCatching { player?.stop(); player?.release() }; player = null; rate = 0
                    flush()
                    if (running) cloud.report("语音回复播放失败，请检查音频设备后重试")
                }
            }
        } catch (_: InterruptedException) {
            // 关闭 App 时结束播放线程。
        } finally {
            runCatching { player?.stop() }; player?.release(); player = null
        }
    }

    @Synchronized
    fun flush() {
        playEpoch.incrementAndGet(); packets.clear(); playbackUntil = 0
        runCatching { player?.pause(); player?.flush() }
        mutable.update { it.copy(playing = false) }
    }

    override fun close() {
        running = false; stopRecording(); flush()
        runCatching { player?.stop() }
        capture.shutdownNow(); playerExecutor.shutdownNow(); clock.shutdownNow()
        manager.abandonAudioFocusRequest(focus)
    }
}
