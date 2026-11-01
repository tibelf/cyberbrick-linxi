package com.cyberbrick.remote

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** 保存自动连接设置；密码用 Android Keystore 加密，备份已禁用。 */
class ConfigStore(context: Context) {
    private val prefs = context.getSharedPreferences("cloud_connection", Context.MODE_PRIVATE)
    private val alias = "cyberbrick.connection.password"
    fun load(): Pair<ConnectionConfig, Boolean> {
        val saved = prefs.getString("password", null)
        val password = runCatching {
            if (saved == null) "" else {
                val pieces = saved.split(":")
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(pieces[0], Base64.NO_WRAP)))
                String(cipher.doFinal(Base64.decode(pieces[1], Base64.NO_WRAP)), Charsets.UTF_8)
            }
        }.getOrDefault("")
        return ConnectionConfig(prefs.getString("server", null) ?: "https://110.40.227.30",
            prefs.getString("username", null) ?: "car", password) to prefs.getBoolean("remember", true)
    }
    fun save(config: ConnectionConfig, remember: Boolean) {
        val editor = prefs.edit().putString("server", config.server).putString("username", config.username)
            .putBoolean("remember", remember)
        if (remember && config.password.isNotEmpty()) {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key())
            val encrypted = cipher.doFinal(config.password.toByteArray(Charsets.UTF_8))
            editor.putString("password", Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" + Base64.encodeToString(encrypted, Base64.NO_WRAP))
        } else editor.remove("password")
        editor.apply()
    }
    fun setRemember(value: Boolean) {
        val editor = prefs.edit().putBoolean("remember", value)
        if (!value) editor.remove("password")
        editor.apply()
    }
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
}
