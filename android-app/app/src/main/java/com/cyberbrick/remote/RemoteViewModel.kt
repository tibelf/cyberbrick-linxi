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
    private var remember = false

    init {
        viewModelScope.launch {
            cloud.state.collect { state ->
                if (state.connected) pendingConfig?.let { config ->
                    runCatching { store.save(config, remember) }.onFailure { cloud.report("连接成功，但本机保存失败；下次需重新填写密码") }
                    pendingConfig = null
                }
            }
        }
    }
    fun connect(config: ConnectionConfig, savePassword: Boolean) {
        audio.stopRecording(); audio.flush()
        pendingConfig = config; remember = savePassword
        cloud.connect(config)
    }
    fun stop() { audio.stopRecording(); audio.flush(); cloud.stop() }
    fun forgetPassword() { remember = false; store.forgetPassword() }
    fun foreground(on: Boolean) {
        if (!on) { audio.stopRecording(); audio.flush() }
        cloud.setForeground(on)
    }
    override fun onCleared() { audio.close(); cloud.close() }
}
