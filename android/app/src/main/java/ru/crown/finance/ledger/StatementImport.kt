package ru.crown.finance.ledger

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.util.UUID

@Entity(tableName = "statement_receipts")
internal data class StatementReceipt(
    @PrimaryKey val sourceKey: String,
    val payloadHash: String,
    val operationId: String,
    val operationVersion: String,
)

@Dao
internal interface StatementDao {
    @Query("SELECT * FROM statement_receipts")
    suspend fun all(): List<StatementReceipt>

    @Insert
    suspend fun insert(receipt: StatementReceipt)
}

internal enum class ImportStatus(val title: String) {
    New("Новая"),
    PossibleDuplicate("Возможный повтор"),
    Duplicate("Уже записана"),
    Conflict("Изменённый внешний ID"),
}

internal data class StatementCandidate(
    val row: StatementRow,
    val status: ImportStatus,
    val matching: List<Operation>,
)

internal data class ImportChoice(
    val position: Int,
    val operation: Operation,
    val linkExisting: Boolean = false,
    val reviewed: Boolean = false,
)

internal fun StatementDocument.key(account: String, row: StatementRow): String {
    val identity = row.externalId?.let { "id:$it" } ?: "file:$fileHash:row:${row.position}"
    return digest(listOf(account, scope, identity).joinToString("\u0000"))
}

internal fun StatementRow.proposal(account: String, key: String): Operation = Operation(
    UUID.nameUUIDFromBytes(key.toByteArray(Charsets.UTF_8)).toString(),
    if (signedMinor > 0) OperationKind.Income else OperationKind.Expense,
    date, account, kotlin.math.abs(signedMinor), description,
)

internal fun previewStatement(
    document: StatementDocument,
    snapshot: LedgerSnapshot,
    account: String,
): List<StatementCandidate> {
    require(snapshot.balances.any { it.account.id == account }) { "Выберите счёт книги" }
    val receipts = snapshot.statementReceipts.associateBy(StatementReceipt::sourceKey)
    val groups = document.rows.groupBy { document.key(account, it) }
    val seen = mutableSetOf<String>()
    val current = snapshot.history.mapNotNull { it.current.takeUnless(Revision::cancelled)?.value }
    return document.rows.map { row ->
        val key = document.key(account, row)
        val receipt = receipts[key]
        val matches = current.filter { it.matchesStatement(account, row) }
        val status = when {
            groups.getValue(key).map(StatementRow::payloadHash).distinct().size > 1 -> ImportStatus.Conflict
            receipt != null && receipt.payloadHash != row.payloadHash -> ImportStatus.Conflict
            receipt != null || !seen.add(key) -> ImportStatus.Duplicate
            matches.isNotEmpty() -> ImportStatus.PossibleDuplicate
            else -> ImportStatus.New
        }
        StatementCandidate(row, status, matches)
    }
}

internal fun Operation.matchesStatement(account: String, row: StatementRow): Boolean =
    kind != OperationKind.Opening && date == row.date && (accountId == account || destinationId == account) &&
        movementFor(account) == row.signedMinor

internal val statementMigration = object : Migration(5, 6) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE statement_receipts (
                sourceKey TEXT NOT NULL PRIMARY KEY,
                payloadHash TEXT NOT NULL,
                operationId TEXT NOT NULL,
                operationVersion TEXT NOT NULL
            )
            """.trimIndent(),
        )
    }
}
