package ru.crown.finance.ledger

import org.bouncycastle.crypto.generators.Argon2BytesGenerator
import org.bouncycastle.crypto.params.Argon2Parameters

internal fun derivePasswordKey(password: CharArray, salt: ByteArray): ByteArray {
    require(password.isNotEmpty() && password.size <= 1024 && salt.size == 16)
    val params = Argon2Parameters.Builder(Argon2Parameters.ARGON2_id)
        .withVersion(Argon2Parameters.ARGON2_VERSION_13)
        .withMemoryAsKB(64 * 1024)
        .withIterations(3)
        .withParallelism(1)
        .withSalt(salt)
        .build()
    val generator = Argon2BytesGenerator()
    generator.init(params)
    return ByteArray(32).also { generator.generateBytes(password, it) }
}
