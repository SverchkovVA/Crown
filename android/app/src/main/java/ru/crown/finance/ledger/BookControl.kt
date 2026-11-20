package ru.crown.finance.ledger

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.math.BigInteger
import java.security.MessageDigest
import java.time.LocalDate
import java.time.YearMonth

@Entity(tableName = "book_period_events")
internal data class PeriodEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val month: String,
    val closed: Boolean,
    val reason: String,
    val recordedAt: Long,
)

@Entity(tableName = "book_reconciliations")
internal data class Reconciliation(
    @PrimaryKey val id: String,
    val accountId: String,
    val date: String,
    val actualMinor: Long,
    val calculatedMinor: String,
    val fingerprint: String,
    val recordedAt: Long,
) {
    val difference: BigInteger get() = actualMinor.toBigInteger() - calculatedMinor.toBigInteger()
}

@Dao
internal interface ControlDao {
    @Query("SELECT * FROM book_period_events ORDER BY sequence")
    suspend fun periods(): List<PeriodEvent>

    @Query("SELECT * FROM book_reconciliations ORDER BY recordedAt, id")
    suspend fun reconciliations(): List<Reconciliation>

    @Insert
    suspend fun insert(event: PeriodEvent)

    @Insert
    suspend fun insert(result: Reconciliation)
}

internal fun closedMonths(events: List<PeriodEvent>): Set<YearMonth> {
    val closed = mutableSetOf<YearMonth>()
    for (event in events) {
        val month = YearMonth.parse(event.month)
        require(month.year in 1900..9999 && event.reason.length <= 200)
        require(event.closed != (month in closed))
        require(event.closed || event.reason.isNotBlank()) { "Укажите причину открытия периода" }
        if (event.closed) closed.add(month) else closed.remove(month)
    }
    return closed
}

internal fun requireOpenDates(events: List<PeriodEvent>, vararg dates: LocalDate) {
    val closed = closedMonths(events)
    require(dates.none { YearMonth.from(it) in closed }) { "Месяц закрыт. Сначала откройте его в разделе «Периоды»" }
}

internal data class ReconciliationPreview(
    val accountId: String,
    val date: LocalDate,
    val calculatedMinor: BigInteger,
    val fingerprint: String,
)

internal fun previewReconciliation(
    history: List<OperationHistory>,
    accounts: List<Account>,
    accountId: String,
    date: LocalDate,
): ReconciliationPreview {
    require(accounts.any { it.id == accountId })
    require(date.year in 1900..9999)
    val included = history.mapNotNull { it.current.takeUnless(Revision::cancelled)?.value }
        .filter { it.date <= date && (it.accountId == accountId || it.destinationId == accountId) }
        .sortedBy(Operation::id)
    val amount = included.fold(BigInteger.ZERO) { sum, operation -> sum + operation.movementFor(accountId).toBigInteger() }
    val proof = accountId + "\n" + date + "\n" + included.joinToString("\n") { it.fingerprint() }
    val hash = MessageDigest.getInstance("SHA-256").digest(proof.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
    return ReconciliationPreview(accountId, date, amount, hash)
}

internal val controlMigration = object : Migration(4, 5) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE book_period_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                month TEXT NOT NULL,
                closed INTEGER NOT NULL,
                reason TEXT NOT NULL,
                recordedAt INTEGER NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE book_reconciliations (
                id TEXT NOT NULL PRIMARY KEY,
                accountId TEXT NOT NULL,
                date TEXT NOT NULL,
                actualMinor INTEGER NOT NULL,
                calculatedMinor TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                recordedAt INTEGER NOT NULL
            )
            """.trimIndent(),
        )
    }
}
