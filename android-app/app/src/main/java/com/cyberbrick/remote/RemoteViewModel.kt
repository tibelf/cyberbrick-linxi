package com.cyberbrick.remote

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch

class RemoteViewModel(app: Application) : AndroidViewModel(app) {
    private val store = ConfigStore(app)
    val initialSettings = store.load()
    val cloud = CloudClient()
    val audio = NativeAudio(app, cloud)
    private var pendingConfig: ConnectionConfig? = null
    private var activeConfig: ConnectionConfig? = null
    private var remember = initialSettings.second

    init {
        viewModelScope.launch {
            cloud.state.collect { state ->
                if (state.connected) pendingConfig?.let { config ->
                    activeConfig = config
                    runCatching { store.save(config, remember) }.onFailure { cloud.report("连接成功，但本机保存失败；下次需重新填写密码") }
                    pendingConfig = null
                }
            }
        }
        if (remember && initialSettings.first.password.isNotEmpty()) connect(initialSettings.first, true, restoring = true)
    }
    fun connect(config: ConnectionConfig, savePassword: Boolean, restoring: Boolean = false) {
        audio.stopRecording(); audio.flush()
        pendingConfig = config; remember = savePassword
        if (restoring) cloud.restore(config) else cloud.connect(config)
    }
    fun stop() { audio.stopRecording(); audio.flush(); cloud.stop() }
    fun setRemember(value: Boolean) {
        remember = value
        runCatching {
            store.setRemember(value)
            if (value && cloud.state.value.connected) activeConfig?.let { store.save(it, true) }
        }.onFailure { cloud.report("无法保存连接设置，请重试") }
    }
    fun disconnect() {
        pendingConfig = null; activeConfig = null
        audio.stopRecording(); audio.flush(); cloud.disconnect()
    }
    fun foreground(on: Boolean) {
        if (!on) { audio.stopRecording(); audio.flush() }
        cloud.setForeground(on)
    }
    override fun onCleared() { audio.close(); cloud.close() }
}
