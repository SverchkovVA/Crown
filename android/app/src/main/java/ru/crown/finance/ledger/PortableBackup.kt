package ru.crown.finance.ledger

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.InputStream
import java.nio.ByteBuffer
import java.security.SecureRandom
import java.time.Instant
import java.time.LocalDate
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

private val signature = "CROWN-ANDROID-BACKUP".toByteArray(Charsets.US_ASCII)
private const val CURRENT_VERSION: Byte = 3
private const val SALT_BYTES = 16
private const val IV_BYTES = 12
private const val TAG_BYTES = 16
private const val MAX_ROWS = 100_000
private const val MAX_PAYLOAD = 8 * 1024 * 1024
private val headerBytes = signature.size + 1 + SALT_BYTES + IV_BYTES
private val random = SecureRandom()

internal data class BackupLedger(
    val title: String,
    val accounts: List<Account>,
    val operations: List<Operation>,
    val revisions: List<Revision>,
    val categories: List<Category> = emptyList(),
) {
    fun validate() {
        require(title.isNotBlank() && title.length <= 100)
        require(accounts.size <= MAX_ROWS && operations.size <= MAX_ROWS && revisions.size <= MAX_ROWS)
        require(categories.size <= MAX_ROWS)
        val kinds = categories.associate { it.id to it.kind }
        require(kinds.size == categories.size)
        require(categories.map { it.kind to it.name.lowercase() }.distinct().size == categories.size)
        val ids = accounts.map(Account::id).toSet()
        require(ids.size == accounts.size)
        require(accounts.all { it.id.length in 1..64 && it.name.isNotBlank() && it.name.length <= 100 })
        require(accounts.map { it.name.lowercase() }.distinct().size == accounts.size)
        val current = mutableMapOf<String, Operation>()
        val cancelled = mutableSetOf<String>()
        for (operation in operations) {
            operation.validate(ids, kinds)
            require(current.put(operation.id, operation) == null)
        }
        for (revision in revisions) {
            val value = revision.value
            require(revision.recordedAt != null && value.id !in cancelled)
            val previous = requireNotNull(current[value.id])
            require(previous.kind != OperationKind.Opening && value.kind != OperationKind.Opening)
            value.validate(ids, kinds)
            if (revision.cancelled) {
                require(value == previous)
                cancelled.add(value.id)
            } else {
                require(value != previous)
            }
            current[value.id] = value
        }
        validateRefunds(assembleHistory(operations, revisions))
    }

    private fun Operation.validate(knownAccounts: Set<String>, kinds: Map<String, OperationKind>) {
        require(id.length in 1..64 && date.year in 1900..9999)
        require(description.isNotBlank() && description.length <= 200)
        require(accountId in knownAccounts && (destinationId == null || destinationId in knownAccounts))
        val expected = if (kind == OperationKind.Refund) OperationKind.Expense else kind
        require(categoryId == null || kinds[categoryId] == expected)
        require(parts.all { it.categoryId == null || kinds[it.categoryId] == expected })
    }
}

internal object PortableBackup {
    fun seal(data: BackupLedger, password: CharArray): ByteArray {
        data.validate()
        val salt = ByteArray(SALT_BYTES).also(random::nextBytes)
        val iv = ByteArray(IV_BYTES).also(random::nextBytes)
        val header = signature + CURRENT_VERSION + salt + iv
        val bytes = encode(data)
        try {
            return header + crypt(Cipher.ENCRYPT_MODE, bytes, password, salt, iv, header)
        } finally {
            bytes.fill(0)
        }
    }

    fun open(stream: InputStream, password: CharArray): BackupLedger {
        val inFile = DataInputStream(stream)
        val header = ByteArray(headerBytes)
        inFile.readFully(header)
        require(header.copyOfRange(0, signature.size).contentEquals(signature)) { "Неизвестный формат копии" }
        val version = header[signature.size].toInt()
        require(version in 1..CURRENT_VERSION.toInt()) { "Неподдерживаемая версия копии" }
        val saltStart = signature.size + 1
        val salt = header.copyOfRange(saltStart, saltStart + SALT_BYTES)
        val iv = header.copyOfRange(saltStart + SALT_BYTES, headerBytes)
        val encrypted = readBounded(inFile, MAX_PAYLOAD + TAG_BYTES)
        require(encrypted.size > TAG_BYTES)
        val bytes = crypt(Cipher.DECRYPT_MODE, encrypted, password, salt, iv, header)
        try {
            return decode(bytes, version).also(BackupLedger::validate)
        } finally {
            bytes.fill(0)
        }
    }

    private fun crypt(
        mode: Int,
        bytes: ByteArray,
        password: CharArray,
        salt: ByteArray,
        iv: ByteArray,
        header: ByteArray,
    ): ByteArray {
        val key = derivePasswordKey(password, salt)
        try {
            return Cipher.getInstance("AES/GCM/NoPadding").run {
                init(mode, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_BYTES * 8, iv))
                updateAAD(header)
                doFinal(bytes)
            }
        } finally {
            key.fill(0)
        }
    }

    private fun encode(data: BackupLedger): ByteArray {
        val content = BoundedOutput(MAX_PAYLOAD)
        try {
            DataOutputStream(content).use { out ->
                out.text(data.title)
                out.writeInt(data.accounts.size)
                for (account in data.accounts) {
                    out.text(account.id)
                    out.text(account.name)
                }
                out.writeInt(data.categories.size)
                for (category in data.categories) {
                    out.text(category.id)
                    out.text(category.name)
                    out.text(category.kind.wireName)
                }
                out.writeInt(data.operations.size)
                data.operations.forEach { out.operation(it) }
                out.writeInt(data.revisions.size)
                for (revision in data.revisions) {
                    out.writeLong(requireNotNull(revision.recordedAt).toEpochMilli())
                    out.writeBoolean(revision.cancelled)
                    out.operation(revision.value)
                }
            }
            return content.toByteArray()
        } finally {
            content.wipe()
        }
    }

    private fun decode(bytes: ByteArray, version: Int): BackupLedger = DataInputStream(ByteArrayInputStream(bytes)).use { data ->
        val title = data.text()
        val accounts = List(data.count()) { Account(data.text(), data.text()) }
        val categories = if (version >= 2) {
            List(data.count()) { Category(data.text(), data.text(), OperationKind.fromWireName(data.text())) }
        } else {
            emptyList()
        }
        val operations = List(data.count()) { data.operation(version) }
        val revisions = List(data.count()) {
            val recordedAt = Instant.ofEpochMilli(data.readLong())
            val cancelled = data.readBoolean()
            Revision(data.operation(version), recordedAt, cancelled)
        }
        require(data.read() == -1)
        BackupLedger(title, accounts, operations, revisions, categories)
    }

    private fun readBounded(stream: InputStream, limit: Int): ByteArray {
        val output = BoundedOutput(limit)
        val block = ByteArray(8192)
        while (true) {
            val read = stream.read(block)
            if (read == -1) break
            require(read > 0)
            output.write(block, 0, read)
        }
        return output.toByteArray()
    }
}

private fun DataOutputStream.operation(value: Operation) {
    text(value.id)
    text(value.kind.wireName)
    writeLong(value.date.toEpochDay())
    text(value.accountId)
    writeLong(value.amountMinor)
    text(value.description)
    val destination = value.destinationId
    writeBoolean(destination != null)
    if (destination != null) text(destination)
    writeLong(value.feeMinor)
    val category = value.categoryId
    writeBoolean(category != null)
    if (category != null) text(category)
    val extra = value.encodeOperationDetails()
    writeInt(extra?.size ?: 0)
    if (extra != null) write(extra)
}

private fun DataInputStream.operation(version: Int): Operation {
    val base = OperationBase(
        text(), OperationKind.fromWireName(text()), LocalDate.ofEpochDay(readLong()),
        text(), readLong(), text(), if (readBoolean()) text() else null, readLong(),
        if (version >= 2 && readBoolean()) text() else null,
    )
    val bytes = if (version >= 3) {
        val size = readInt()
        require(size in 0..20_000)
        if (size == 0) null else ByteArray(size).also(::readFully)
    } else null
    val extra = decodeOperationDetails(bytes)
    return Operation(
        base.id, base.kind, base.date, base.account, base.amount, base.description, base.destination,
        base.fee, base.category, extra.parts, extra.refundOf, extra.refundVersion,
    )
}

private data class OperationBase(
    val id: String, val kind: OperationKind, val date: LocalDate, val account: String,
    val amount: Long, val description: String, val destination: String?, val fee: Long, val category: String?,
)

private fun DataInputStream.count(): Int = readInt().also { require(it in 0..MAX_ROWS) }

private fun DataOutputStream.text(value: String) {
    val raw = value.toByteArray(Charsets.UTF_8)
    try {
        require(raw.size in 1..800)
        writeShort(raw.size)
        write(raw)
    } finally {
        raw.fill(0)
    }
}

private fun DataInputStream.text(): String {
    val size = readUnsignedShort()
    require(size in 1..800)
    val raw = ByteArray(size)
    try {
        readFully(raw)
        return Charsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(raw)).toString()
    } finally {
        raw.fill(0)
    }
}

private class BoundedOutput(private val limit: Int) : ByteArrayOutputStream() {
    override fun write(b: Int) {
        require(count < limit) { "Размер копии больше допустимого" }
        super.write(b)
    }

    override fun write(b: ByteArray, off: Int, len: Int) {
        require(len <= limit - count) { "Размер копии больше допустимого" }
        super.write(b, off, len)
    }

    fun wipe() {
        buf.fill(0)
        reset()
    }
}
