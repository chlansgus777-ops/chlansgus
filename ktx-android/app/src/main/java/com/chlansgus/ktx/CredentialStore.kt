package com.chlansgus.ktx

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 자동 로그인을 켠 경우에만 회원번호와 비밀번호를 저장합니다.
 * 비밀번호는 기기 밖으로 꺼낼 수 없는 Android Keystore 키(AES-GCM)로 암호화되며, 앱 백업에서도 제외됩니다.
 */
class CredentialStore(context: Context) {
    private val prefs = context.getSharedPreferences("credentials", Context.MODE_PRIVATE)

    data class Saved(val userId: String, val password: String)

    val autoLogin get() = prefs.getBoolean(KEY_AUTO, false)

    fun load(): Saved? {
        if (!autoLogin) return null
        val user = prefs.getString(KEY_USER, null) ?: return null
        val iv = prefs.getString(KEY_IV, null) ?: return null
        val data = prefs.getString(KEY_DATA, null) ?: return null
        return runCatching {
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)))
            Saved(user, String(cipher.doFinal(Base64.decode(data, Base64.NO_WRAP)), Charsets.UTF_8))
        }.getOrElse {
            clear() // 키가 사라졌거나(보안 설정 변경 등) 손상된 경우
            null
        }
    }

    fun save(userId: String, password: String) {
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val data = cipher.doFinal(password.toByteArray(Charsets.UTF_8))
        prefs.edit()
            .putBoolean(KEY_AUTO, true)
            .putString(KEY_USER, userId)
            .putString(KEY_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString(KEY_DATA, Base64.encodeToString(data, Base64.NO_WRAP))
            .apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return gen.generateKey()
    }

    private companion object {
        const val ALIAS = "ktx_credentials"
        const val TRANSFORM = "AES/GCM/NoPadding"
        const val KEY_AUTO = "auto"
        const val KEY_USER = "user"
        const val KEY_IV = "iv"
        const val KEY_DATA = "data"
    }
}
