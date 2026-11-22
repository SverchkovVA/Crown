package ru.crown.finance.ledger

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.io.DataInputStream
import java.io.DataOutputStream
import java.math.BigInteger
import java.time.LocalDate
import java.time.YearMonth

internal enum class BonusKind(val label: String) {
    Opening("Начальный остаток"), Accrual("Начисление"), Spending("Списание"),
    Expiry("Сгорание"), Adjustment("Корректировка"),
}

@Entity(tableName = "bonus_program_events")
internal data class BonusProgramEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val id: String, val name: String, val archived: Boolean, val recordedAt: Long,
)

@Entity(tableName = "bonus_entry_events")
internal data class BonusEntryEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val id: String, val programId: String, val date: String, val kind: String, val units: Long,
    val categoryId: String?, val operationId: String?, val note: String,
    val cancelled: Boolean, val recordedAt: Long,
) {
    fun bonusKind() = BonusKind.valueOf(kind)
    fun validate() {
        require(id.length in 1..64 && programId.length in 1..64)
        require(LocalDate.parse(date).year in 1900..9999)
        require(units != 0L && units != Long.MIN_VALUE && recordedAt >= 0)
        require(note.length <= 500 && note.none { it.isISOControl() && it != '\n' })
        when (bonusKind()) {
            BonusKind.Opening, BonusKind.Accrual -> require(units > 0)
            BonusKind.Spending, BonusKind.Expiry -> require(units < 0)
            BonusKind.Adjustment -> Unit
        }
    }
    fun sameFact(other: BonusEntryEvent) = copy(sequence = 0, recordedAt = 0, cancelled = false) ==
        other.copy(sequence = 0, recordedAt = 0, cancelled = false)
}

internal data class BonusState(
    val programEvents: List<BonusProgramEvent> = emptyList(),
    val entryEvents: List<BonusEntryEvent> = emptyList(),
) {
    val programs get() = programEvents.associateBy(BonusProgramEvent::id).values.sortedBy { it.name.lowercase() }
    val entries get() = entryEvents.associateBy(BonusEntryEvent::id).values.toList()
    fun validate(categories: List<Category>, operations: Set<String>) {
        fun sequence(values: List<Long>) {
            require(values.size <= 100_000 && values.all { it > 0 } && values.zipWithNext().all { (a, b) -> a < b })
        }
        sequence(programEvents.map(BonusProgramEvent::sequence))
        sequence(entryEvents.map(BonusEntryEvent::sequence))
        require(programs.size <= 1000)
        require(programEvents.all { it.id.length in 1..64 && it.name == it.name.trim() && it.name.length in 1..80 && it.recordedAt >= 0 })
        require(programs.map { it.name.lowercase() }.distinct().size == programs.size)
        val ids = programs.map(BonusProgramEvent::id).toSet()
        val expense = categories.filter { it.kind == OperationKind.Expense }.map(Category::id).toSet()
        val previous = mutableMapOf<String, BonusEntryEvent>()
        for (entry in entryEvents) {
            entry.validate()
            require(entry.programId in ids && (entry.categoryId == null || entry.categoryId in expense))
            require(entry.operationId == null || entry.operationId in operations)
            val before = previous.put(entry.id, entry)
            require(before?.cancelled != true)
            if (entry.cancelled) require(before != null && entry.sameFact(before))
            else if (before != null) require(!entry.sameFact(before))
        }
    }
    fun report(programId: String, month: YearMonth): BonusReport {
        val active = entries.filter { !it.cancelled && it.programId == programId }
        val period = active.filter { YearMonth.from(LocalDate.parse(it.date)) == month }
        fun sum(rows: List<BonusEntryEvent>) = rows.fold(BigInteger.ZERO) { total, entry -> total + entry.units.toBigInteger() }
        return BonusReport(
            sum(active.filter { LocalDate.parse(it.date) <= month.atEndOfMonth() }),
            sum(period.filter { it.bonusKind() == BonusKind.Accrual }),
            -sum(period.filter { it.bonusKind() == BonusKind.Spending }),
            -sum(period.filter { it.bonusKind() == BonusKind.Expiry }),
            sum(period.filter { it.bonusKind() == BonusKind.Adjustment }),
            period.filter { it.bonusKind() == BonusKind.Accrual }.groupBy { it.categoryId }.mapValues { sum(it.value) },
        )
    }
}

internal data class BonusReport(
    val balance: BigInteger, val accrued: BigInteger, val spent: BigInteger,
    val expired: BigInteger, val adjustment: BigInteger, val categories: Map<String?, BigInteger>,
)

@Dao
internal interface BonusDao {
    @Query("SELECT * FROM bonus_program_events ORDER BY sequence") suspend fun programs(): List<BonusProgramEvent>
    @Query("SELECT * FROM bonus_entry_events ORDER BY sequence") suspend fun entries(): List<BonusEntryEvent>
    @Insert suspend fun insert(event: BonusProgramEvent): Long
    @Insert suspend fun insert(event: BonusEntryEvent): Long
}

internal fun DataOutputStream.bonuses(state: BonusState) {
    writeInt(state.programEvents.size)
    for (e in state.programEvents) {
        writeLong(e.sequence); writeUTF(e.id); writeUTF(e.name); writeBoolean(e.archived); writeLong(e.recordedAt)
    }
    writeInt(state.entryEvents.size)
    for (e in state.entryEvents) {
        writeLong(e.sequence); writeUTF(e.id); writeUTF(e.programId); writeUTF(e.date); writeUTF(e.kind); writeLong(e.units)
        writeUTF(e.categoryId.orEmpty()); writeUTF(e.operationId.orEmpty()); writeUTF(e.note); writeBoolean(e.cancelled); writeLong(e.recordedAt)
    }
}

internal fun DataInputStream.bonuses(): BonusState {
    fun count() = readInt().also { require(it in 0..100_000) }
    val programs = List(count()) { BonusProgramEvent(readLong(), readUTF(), readUTF(), readBoolean(), readLong()) }
    val entries = List(count()) {
        BonusEntryEvent(readLong(), readUTF(), readUTF(), readUTF(), readUTF(), readLong(),
            readUTF().ifEmpty { null }, readUTF().ifEmpty { null }, readUTF(), readBoolean(), readLong())
    }
    return BonusState(programs, entries)
}

internal val bonusMigration = object : Migration(9, 10) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE bonus_program_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, archived INTEGER NOT NULL, recordedAt INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE bonus_entry_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, id TEXT NOT NULL, programId TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL, units INTEGER NOT NULL, categoryId TEXT, operationId TEXT, note TEXT NOT NULL, cancelled INTEGER NOT NULL, recordedAt INTEGER NOT NULL)")
    }
}
