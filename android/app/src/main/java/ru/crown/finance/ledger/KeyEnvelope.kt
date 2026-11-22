package ru.crown.finance.ledger

import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** Меняет только зашифрованную обёртку. Отказ до атомарного переименования сохраняет прежний файл. */
internal fun replaceKeyEnvelope(file: File, expected: ByteArray, replacement: ByteArray) {
    require(replacement.size in 1..4096)
    check(file.isFile && file.length() in 1..4096 && file.readBytes().contentEquals(expected)) {
        "Данные доступа изменились. Откройте книгу заново"
    }
    val staged = Files.createTempFile(file.parentFile!!.toPath(), "key-change-", ".tmp").toFile()
    try {
        staged.outputStream().use { stream -> stream.write(replacement); stream.fd.sync() }
        Files.move(staged.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    } finally { if (staged.exists()) check(staged.delete()) }
}
