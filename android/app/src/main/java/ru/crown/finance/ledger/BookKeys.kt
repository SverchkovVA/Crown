package ru.crown.finance.ledger

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyStore
import java.security.SecureRandom
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

private const val KEYS_ALIAS = "ru.crown.finance.mobile-book.v1"
private const val KEY_SIZE = 32
private const val IV_SIZE = 12
private const val TAG_SIZE = 16
private const val METADATA_LIMIT = 4096
private const val ENVELOPE_AAD = "CrownAndroid:metadata:1"
private val random = SecureRandom()

internal enum class CredentialKind { Password, Recovery }

internal class BookKeys {
    data class Creation(val id: String, val key: ByteArray, val recoveryCode: String, val metadata: ByteArray)
    data class Unlocked(val id: String, val key: ByteArray)

    fun create(password: CharArray): Creation {
        require(password.isNotEmpty() && password.size <= 1024)
        val id = UUID.randomUUID().toString()
        val salt = randomBytes(16)
        val recovery = randomBytes(KEY_SIZE)
        val database = randomBytes(KEY_SIZE)
        try {
            val derived = derivePasswordKey(password, salt)
            val passwordWrap = try {
                seal(aesKey(derived), database, bookPurpose(id, CredentialKind.Password))
            } finally {
                derived.fill(0)
            }
            val recoveryWrap = seal(aesKey(recovery), database, bookPurpose(id, CredentialKind.Recovery))
            val raw = JSONObject().put("id", id).put("salt", encode(salt))
                .put("password", passwordWrap).put("recovery", recoveryWrap)
                .toString().toByteArray(Charsets.UTF_8)
            val envelope = try {
                seal(deviceKey(create = true), raw, ENVELOPE_AAD)
            } finally {
                raw.fill(0)
            }
            return Creation(
                id, database, "CA1-" + encode(recovery),
                JSONObject().put("version", 1).put("metadata", envelope)
                    .toString().toByteArray(Charsets.UTF_8),
            )
        } catch (error: Exception) {
            database.fill(0)
            throw error
        } finally {
            recovery.fill(0)
        }
    }

    fun unlock(metadata: ByteArray, credential: CharArray, kind: CredentialKind): Unlocked {
        require(metadata.size in 1..METADATA_LIMIT)
        val outer = JSONObject(metadata.toString(Charsets.UTF_8))
        require(outer.getInt("version") == 1)
        val raw = unseal(
            deviceKey(create = false), outer.getJSONObject("metadata"), ENVELOPE_AAD, METADATA_LIMIT,
        )
        val inner = try {
            JSONObject(raw.toString(Charsets.UTF_8))
        } finally {
            raw.fill(0)
        }
        val id = inner.getString("id")
        require(UUID.fromString(id).toString() == id)
        val secret = when (kind) {
            CredentialKind.Password -> {
                require(credential.isNotEmpty() && credential.size <= 1024)
                val salt = decode(inner.getString("salt"))
                require(salt.size == 16)
                derivePasswordKey(credential, salt)
            }
            CredentialKind.Recovery -> {
                require(credential.size <= 64)
                val code = credential.concatToString().trim()
                require(Regex("CA1-[A-Za-z0-9_-]{43}").matches(code))
                val text = code.removePrefix("CA1-")
                val decoded = decode(text)
                if (decoded.size != KEY_SIZE || encode(decoded) != text) {
                    decoded.fill(0)
                    error("Неверный формат кода")
                }
                decoded
            }
        }
        try {
            val field = if (kind == CredentialKind.Password) "password" else "recovery"
            val database = unseal(aesKey(secret), inner.getJSONObject(field), bookPurpose(id, kind), KEY_SIZE)
            if (database.size != KEY_SIZE) {
                database.fill(0)
                error("Длина ключа книги не совпадает")
            }
            return Unlocked(id, database)
        } finally {
            secret.fill(0)
        }
    }
}

private fun randomBytes(size: Int) = ByteArray(size).also(random::nextBytes)
private fun aesKey(bytes: ByteArray) = SecretKeySpec(bytes, "AES")
private fun bookPurpose(id: String, kind: CredentialKind) = "CrownAndroid:key:1:" + id + ":" + kind.name

private fun encode(bytes: ByteArray) =
    Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)

private fun decode(text: String) = Base64.decode(text, Base64.URL_SAFE or Base64.NO_WRAP)

private fun seal(key: SecretKey, bytes: ByteArray, purpose: String): JSONObject {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, key)
    require(cipher.iv.size == IV_SIZE)
    cipher.updateAAD(purpose.toByteArray(Charsets.UTF_8))
    return JSONObject().put("iv", encode(cipher.iv)).put("body", encode(cipher.doFinal(bytes)))
}

private fun unseal(key: SecretKey, wrapper: JSONObject, purpose: String, maxSize: Int): ByteArray {
    val iv = decode(wrapper.getString("iv"))
    val ciphertext = decode(wrapper.getString("body"))
    require(iv.size == IV_SIZE && ciphertext.size in (TAG_SIZE + 1)..(maxSize + TAG_SIZE))
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_SIZE * 8, iv))
    cipher.updateAAD(purpose.toByteArray(Charsets.UTF_8))
    return cipher.doFinal(ciphertext)
}

private fun deviceKey(create: Boolean): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getKey(KEYS_ALIAS, null) as? SecretKey)?.let { return it }
    check(create) { "Нет ключа устройства" }
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    generator.init(
        KeyGenParameterSpec.Builder(KEYS_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setKeySize(256)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build(),
    )
    return generator.generateKey()
}
