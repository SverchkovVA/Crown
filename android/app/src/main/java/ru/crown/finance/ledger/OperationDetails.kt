package ru.crown.finance.ledger

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.math.BigInteger
import java.security.MessageDigest

internal data class Allocation(val categoryId: String?, val amountMinor: Long) {
    init {
        require(amountMinor > 0)
        require(categoryId == null || categoryId.length in 1..64)
    }
}

internal fun Operation.allocations(): List<Allocation> =
    parts.ifEmpty { listOf(Allocation(categoryId, amountMinor)) }

internal fun Operation.fingerprint(): String {
    val stream = ByteArrayOutputStream()
    DataOutputStream(stream).use { out ->
        out.writeUTF(id)
        out.writeUTF(kind.wireName)
        out.writeLong(date.toEpochDay())
        out.writeUTF(accountId)
        out.writeLong(amountMinor)
        out.writeUTF(description)
        out.writeUTF(destinationId.orEmpty())
        out.writeLong(feeMinor)
        out.writeUTF(categoryId.orEmpty())
        val extra = encodeOperationDetails()
        out.writeInt(extra?.size ?: 0)
        if (extra != null) out.write(extra)
    }
    return MessageDigest.getInstance("SHA-256").digest(stream.toByteArray())
        .joinToString("") { "%02x".format(it) }
}

internal fun Operation.encodeOperationDetails(): ByteArray? {
    if (parts.isEmpty() && refundOf == null) return null
    val stream = ByteArrayOutputStream()
    DataOutputStream(stream).use { out ->
        out.writeByte(1)
        out.writeInt(parts.size)
        for (part in parts) {
            out.writeUTF(part.categoryId.orEmpty())
            out.writeLong(part.amountMinor)
        }
        out.writeUTF(refundOf.orEmpty())
        out.writeUTF(refundVersion.orEmpty())
    }
    return stream.toByteArray()
}

internal data class OperationDetails(
    val parts: List<Allocation> = emptyList(),
    val refundOf: String? = null,
    val refundVersion: String? = null,
)

internal fun decodeOperationDetails(bytes: ByteArray?): OperationDetails {
    if (bytes == null) return OperationDetails()
    require(bytes.size in 1..20_000)
    return DataInputStream(ByteArrayInputStream(bytes)).use { input ->
        require(input.readUnsignedByte() == 1)
        val count = input.readInt()
        require(count in 0..100)
        val parts = List(count) { Allocation(input.readUTF().ifEmpty { null }, input.readLong()) }
        val source = input.readUTF().ifEmpty { null }
        val version = input.readUTF().ifEmpty { null }
        require(input.read() == -1)
        OperationDetails(parts, source, version)
    }
}

internal fun validateRefunds(history: List<OperationHistory>) {
    val byId = history.associateBy(OperationHistory::id)
    val active = history.mapNotNull { it.current.takeUnless(Revision::cancelled)?.value }
    val refunds = active.filter { it.kind == OperationKind.Refund }
    for (refund in refunds) {
        val source = requireNotNull(byId[refund.refundOf]) { "Исходная покупка не найдена" }
        require(!source.current.cancelled && source.current.value.kind == OperationKind.Expense) {
            "Покупку с действующими возвратами нельзя отменить или превратить в другую операцию"
        }
        val version = source.revisions.firstOrNull {
            !it.cancelled && it.value.fingerprint() == refund.refundVersion
        }?.value
        require(version?.kind == OperationKind.Expense) { "Версия исходной покупки не найдена" }
        validateRefundAgainstPurchase(refund, version)
        validateRefundAgainstPurchase(refund, source.current.value)
    }
    for ((purchase, linked) in refunds.groupBy { it.refundOf }) {
        val capacity = byId.getValue(requireNotNull(purchase)).current.value.allocations()
            .associate { it.categoryId to it.amountMinor.toBigInteger() }
        val returned = linked.flatMap(Operation::allocations).groupBy(Allocation::categoryId)
        for ((category, amounts) in returned) {
            val total = amounts.fold(BigInteger.ZERO) { sum, part -> sum + part.amountMinor.toBigInteger() }
            require(total <= capacity.getOrDefault(category, BigInteger.ZERO)) {
                "Сумма возвратов превышает покупку в выбранной категории"
            }
        }
    }
}

private fun validateRefundAgainstPurchase(refund: Operation, purchase: Operation) {
    require(refund.date >= purchase.date) { "Дата возврата предшествует покупке" }
    val capacity = purchase.allocations().associate { it.categoryId to it.amountMinor }
    require(refund.allocations().all { it.amountMinor <= (capacity[it.categoryId] ?: 0L) }) {
        "Распределение возврата не соответствует покупке"
    }
}

internal val detailsMigration = object : Migration(3, 4) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE ledger_operations ADD COLUMN details BLOB")
        db.execSQL("ALTER TABLE ledger_revisions ADD COLUMN value_details BLOB")
    }
}
