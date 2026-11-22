package ru.crown.finance.ledger

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.io.*
import java.time.LocalDate
import java.util.Base64

private const val MAX_PLAN_BYTES = 4 * 1024 * 1024
internal fun encodePlan(value: PlanningConditions): String {
    value.validate()
    val bytes = ByteArrayOutputStream()
    DataOutputStream(bytes).use { out ->
        out.writeInt(1); out.writeUTF(value.kind.wire); out.writeUTF("RUB"); out.writeInt(2)
        when (value) {
            is BudgetPlan -> {
                out.writeUTF("budget-postings-v1")
                out.writeLong(value.start.toEpochDay()); out.writeLong(value.end.toEpochDay()); out.writeLong(value.limit)
                out.writeBoolean(value.monthly)
                out.writeBoolean(value.accounts != null)
                value.accounts?.let { ids -> out.writeInt(ids.size); ids.sorted().forEach(out::writeUTF) }
                out.writeBoolean(value.categories != null)
                value.categories?.let { ids -> out.writeInt(ids.size); ids.sortedBy { it.orEmpty() }.forEach { out.writeUTF(it.orEmpty()) } }
            }
            is CalendarPlan -> {
                out.writeUTF("calendar-payments-v1"); out.writeInt(value.schedules.size)
                value.schedules.forEach { row ->
                    out.writeUTF(row.name); out.writeLong(row.amount); out.writeBoolean(row.income); out.writeLong(row.first.toEpochDay())
                    out.writeUTF(row.unit.name); out.writeInt(row.interval); out.writeBoolean(row.last != null)
                    row.last?.let { out.writeLong(it.toEpochDay()) }
                }
            }
            is GoalPlan -> {
                out.writeUTF("monthly-contribution-v1")
                out.writeLong(value.asOf.toEpochDay()); out.writeLong(value.current); out.writeLong(value.target); out.writeLong(value.monthly)
                out.writeBoolean(value.deadline != null); value.deadline?.let { out.writeLong(it.toEpochDay()) }
            }
        }
    }
    require(bytes.size() <= MAX_PLAN_BYTES)
    return Base64.getEncoder().encodeToString(bytes.toByteArray())
}
internal fun decodePlan(text: String): PlanningConditions {
    require(text.length in 1..6 * 1024 * 1024)
    val raw = Base64.getDecoder().decode(text)
    require(raw.size <= MAX_PLAN_BYTES && Base64.getEncoder().encodeToString(raw) == text)
    val result = DataInputStream(ByteArrayInputStream(raw)).use { input ->
        require(input.readInt() == 1)
        val rawKind = input.readUTF()
        val kind = requireNotNull(PlanningKind.entries.singleOrNull { it.wire == rawKind })
        require(input.readUTF() == "RUB" && input.readInt() == 2)
        val value = when (kind) {
            PlanningKind.Budget -> {
                require(input.readUTF() == "budget-postings-v1")
                val start = LocalDate.ofEpochDay(input.readLong()); val end = LocalDate.ofEpochDay(input.readLong()); val limit = input.readLong()
                val monthly = input.readBoolean()
                fun ids(): List<String>? = if (input.readBoolean()) List(input.readInt().also { require(it in 0..10000) }) { input.readUTF() } else null
                val accounts = ids(); val categories = ids()?.map { it.ifEmpty { null } }
                require(accounts == null || accounts.distinct().size == accounts.size)
                require(categories == null || categories.distinct().size == categories.size)
                BudgetPlan(start, end, limit, accounts?.toSet(), categories?.toSet(), monthly)
            }
            PlanningKind.Calendar -> {
                require(input.readUTF() == "calendar-payments-v1")
                CalendarPlan(List(input.readInt().also { require(it in 1..1000) }) {
                    val name = input.readUTF(); val amount = input.readLong(); val income = input.readBoolean(); val first = LocalDate.ofEpochDay(input.readLong())
                    val unit = RepeatUnit.valueOf(input.readUTF()); val interval = input.readInt()
                    PaymentSchedule(name, amount, income, first, unit, interval, if (input.readBoolean()) LocalDate.ofEpochDay(input.readLong()) else null)
                })
            }
            PlanningKind.Goal -> {
                require(input.readUTF() == "monthly-contribution-v1")
                val day = LocalDate.ofEpochDay(input.readLong()); val current = input.readLong(); val target = input.readLong(); val monthly = input.readLong()
                GoalPlan(day, current, target, monthly, if (input.readBoolean()) LocalDate.ofEpochDay(input.readLong()) else null)
            }
        }
        require(input.read() == -1); value
    }
    result.validate()
    require(encodePlan(result) == text) { "Нарушен формат условий планирования" }
    return result
}
@Entity(tableName = "planning_events", indices = [Index(value = ["id", "sequence"])])
internal data class PlanningEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val id: String, val kind: String, val name: String, val payload: String, val archived: Boolean, val recordedAt: Long,
) {
    fun conditions() = decodePlan(payload).also { require(it.kind.wire == kind) }
}
internal fun currentPlans(events: List<PlanningEvent>) = events.associateBy(PlanningEvent::id).values.sortedBy { it.name.lowercase() }
internal fun validatePlanReferences(value: PlanningConditions, accounts: Set<String>, categories: Set<String>) {
    value.validate()
    if (value is BudgetPlan) {
        require(value.accounts == null || value.accounts.all { it in accounts }) { "Счёт бюджета не найден" }
        require(value.categories == null || value.categories.all { it == null || it in categories }) { "Категория бюджета не найдена" }
    }
}
internal fun validatePlanning(events: List<PlanningEvent>, accounts: Set<String>, categories: Set<String>) {
    require(events.size <= 100000 && events.all { it.sequence > 0 && it.recordedAt >= 0 && it.id.length in 1..64 && it.name == it.name.trim() && it.name.length in 1..80 })
    require(events.zipWithNext().all { (a, b) -> a.sequence < b.sequence })
    val kinds = mutableMapOf<String, String>()
    for (event in events) {
        require(kinds.putIfAbsent(event.id, event.kind).let { it == null || it == event.kind }) { "Вид сохранённого плана изменён" }
        validatePlanReferences(event.conditions(), accounts, categories)
    }
    for (kind in PlanningKind.entries) {
        val active = currentPlans(events).filter { it.kind == kind.wire && !it.archived }
        require(active.size <= 1000 && active.map { it.name.lowercase() }.distinct().size == active.size)
    }
}
@Dao internal interface PlanningDao {
    @Query("SELECT * FROM planning_events ORDER BY sequence") suspend fun events(): List<PlanningEvent>
    @Insert suspend fun insert(event: PlanningEvent): Long
}
internal fun DataOutputStream.planning(events: List<PlanningEvent>) {
    writeInt(events.size)
    for (event in events) {
        writeLong(event.sequence); writeUTF(event.id); writeUTF(event.kind); writeUTF(event.name)
        val raw = event.payload.toByteArray(Charsets.US_ASCII)
        require(raw.size <= 6 * 1024 * 1024); writeInt(raw.size); write(raw)
        writeBoolean(event.archived); writeLong(event.recordedAt)
    }
}
internal fun DataInputStream.planning(): List<PlanningEvent> {
    return List(readInt().also { require(it in 0..100000) }) {
        val sequence = readLong(); val id = readUTF(); val kind = readUTF(); val name = readUTF()
        val raw = ByteArray(readInt().also { require(it in 1..6 * 1024 * 1024) }); readFully(raw)
        PlanningEvent(sequence, id, kind, name, raw.toString(Charsets.US_ASCII), readBoolean(), readLong())
    }
}
internal val calendarMigration = object : Migration(12, 13) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE INDEX index_planning_events_id_sequence ON planning_events(id, sequence)")
    }
}
internal val planningMigration = object : Migration(11, 12) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE planning_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, archived INTEGER NOT NULL, recordedAt INTEGER NOT NULL)")
    }
}
