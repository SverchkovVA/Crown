package ru.crown.finance.ledger

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.io.DataInputStream
import java.io.DataOutputStream
import java.security.MessageDigest
import java.time.LocalDate

internal fun PlanningEvent.conditionsHash(): String = MessageDigest.getInstance("SHA-256")
    .digest(payload.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

internal data class CalendarPaymentKey(
    val calendarId: String, val conditionsHash: String, val accountId: String,
    val scheduleIndex: Int, val date: String,
) {
    val group get() = Triple(calendarId, conditionsHash, accountId)
    fun validate() {
        require(calendarId.length in 1..64 && accountId.length in 1..64)
        require(conditionsHash.matches(Regex("[0-9a-f]{64}")) && scheduleIndex in 0..999)
        val day = requireNotNull(runCatching { LocalDate.parse(date) }.getOrNull()) { "Неверная дата ожидания" }
        require(day.year in 1900..9999 && day.toString() == date)
    }
}
internal fun PlanningEvent.paymentKey(account: String, payment: ExpectedPayment) =
    CalendarPaymentKey(id, conditionsHash(), account, payment.scheduleIndex, payment.date.toString())

@Entity(tableName = "calendar_match_events", indices = [Index(value = ["calendarId", "conditionsHash", "accountId", "scheduleIndex", "date", "sequence"])])
internal data class CalendarMatchEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    @Embedded val key: CalendarPaymentKey,
    val calendarSequence: Long, val operationId: String, val operationVersion: String,
    val cancelled: Boolean, val previousSequence: Long?, val recordedAt: Long,
)
@Dao internal interface CalendarMatchesDao {
    @Query("SELECT * FROM calendar_match_events ORDER BY sequence") suspend fun events(): List<CalendarMatchEvent>
    @Insert suspend fun insert(event: CalendarMatchEvent): Long
}
internal fun currentCalendarMatches(events: List<CalendarMatchEvent>) =
    events.associateBy(CalendarMatchEvent::key).values.toList()

internal fun calendarPayment(plan: PlanningEvent, key: CalendarPaymentKey): ExpectedPayment {
    key.validate()
    require(plan.kind == PlanningKind.Calendar.wire && plan.id == key.calendarId && plan.conditionsHash() == key.conditionsHash) { "Условия календаря изменились" }
    return specificPayment(plan.conditions() as CalendarPlan, key)
}
private fun specificPayment(plan: CalendarPlan, key: CalendarPaymentKey): ExpectedPayment {
    val schedule = requireNotNull(plan.schedules.getOrNull(key.scheduleIndex)) { "Расписание не найдено" }
    val day = LocalDate.parse(key.date)
    return requireNotNull(forecastPayments(CalendarPlan(listOf(schedule)), day, day).singleOrNull()) {
        "Ожидание не найдено в условиях календаря"
    }.copy(scheduleIndex = key.scheduleIndex)
}
internal fun Operation.matchesPayment(account: String, payment: ExpectedPayment): Boolean =
    kind != OperationKind.Opening && (accountId == account || destinationId == account) &&
        date == payment.date && movementFor(account).toBigInteger() == payment.signed

internal fun validateCalendarMatches(
    events: List<CalendarMatchEvent>, plans: List<PlanningEvent>, accounts: Set<String>, history: List<OperationHistory>,
) {
    require(events.size <= 100000 && events.all { it.sequence > 0 && it.recordedAt >= 0 })
    require(events.zipWithNext().all { (a, b) -> a.sequence < b.sequence })
    val calendars = plans.associateBy(PlanningEvent::sequence)
    val versions = history.associate { it.id to it.revisions.associate { row -> row.value.fingerprint() to row.value } }
    val latest = mutableMapOf<CalendarPaymentKey, CalendarMatchEvent>()
    val used = mutableMapOf<Pair<Triple<String, String, String>, String>, CalendarPaymentKey>()
    val expected = mutableMapOf<Pair<Long, CalendarPaymentKey>, ExpectedPayment>()
    val specifications = mutableMapOf<Long, Pair<String, CalendarPlan>>()
    for (event in events) {
        event.key.validate()
        require(event.key.accountId in accounts && event.calendarSequence > 0)
        require(event.operationId.length in 1..64 && event.operationVersion.matches(Regex("[0-9a-f]{64}")))
        val plan = requireNotNull(calendars[event.calendarSequence]) { "Версия календаря не найдена" }
        require(plan.kind == PlanningKind.Calendar.wire && plan.id == event.key.calendarId)
        val specification = specifications.getOrPut(plan.sequence) { plan.conditionsHash() to (plan.conditions() as CalendarPlan) }
        require(specification.first == event.key.conditionsHash) { "Условия календаря изменились" }
        val payment = expected.getOrPut(event.calendarSequence to event.key) { specificPayment(specification.second, event.key) }
        val operation = requireNotNull(versions[event.operationId]?.get(event.operationVersion)) { "Версия проведённой операции не найдена" }
        require(operation.matchesPayment(event.key.accountId, payment)) { "Дата и движение операции не совпадают с ожиданием" }
        val previous = latest[event.key]
        require(event.previousSequence == previous?.sequence) { "Нарушена цепочка сопоставления" }
        if (event.cancelled) {
            require(previous != null && !previous.cancelled && previous.operationId == event.operationId &&
                previous.operationVersion == event.operationVersion && previous.calendarSequence == event.calendarSequence) { "Снятие должно сохранять прежний источник" }
        } else {
            require(previous == null || previous.cancelled || previous.operationId != event.operationId || previous.operationVersion != event.operationVersion) { "Сопоставление не изменилось" }
        }
        if (previous != null && !previous.cancelled) used.remove(previous.key.group to previous.operationId)
        if (!event.cancelled) require(used.put(event.key.group to event.operationId, event.key) == null) { "Операция уже сопоставлена с другим ожиданием" }
        latest[event.key] = event
    }
}
internal data class ResolvedCalendarMatches(
    val matches: List<PaymentMatch> = emptyList(),
    val active: List<CalendarMatchEvent> = emptyList(),
    val stale: List<CalendarMatchEvent> = emptyList(),
) {
    private val byPayment = active.associateBy { it.key.scheduleIndex to it.key.date }
    fun eventFor(payment: ExpectedPayment) = byPayment[payment.scheduleIndex to payment.date.toString()]
}
internal fun resolveCalendarMatches(
    plan: PlanningEvent, accountId: String, expected: List<ExpectedPayment>,
    operations: List<Operation>, events: List<CalendarMatchEvent>, checkCancelled: () -> Unit = {},
): ResolvedCalendarMatches {
    require(operations.map { it.id }.distinct().size == operations.size)
    val group = Triple(plan.id, plan.conditionsHash(), accountId)
    val current = operations.associateBy(Operation::id)
    val active = currentCalendarMatches(events).filter { it.key.group == group && !it.cancelled }
    val indices = expected.withIndex().associate { (index, payment) -> (payment.scheduleIndex to payment.date.toString()) to index }
    val stale = mutableListOf<CalendarMatchEvent>()
    val matches = mutableListOf<PaymentMatch>()
    for (event in active) {
        checkCancelled()
        val operation = current[event.operationId]
        if (operation == null || operation.fingerprint() != event.operationVersion) {
            stale += event
        } else {
            indices[event.key.scheduleIndex to event.key.date]?.let { index ->
                require(operation.matchesPayment(accountId, expected[index])) { "Источник сопоставления не совпал с ожиданием" }
                matches += PaymentMatch(index, operation.id, event.operationVersion)
            }
        }
    }
    return ResolvedCalendarMatches(matches, active, stale)
}
internal fun DataOutputStream.calendarMatches(events: List<CalendarMatchEvent>) {
    writeInt(events.size)
    events.forEach {
        writeLong(it.sequence); writeUTF(it.key.calendarId); writeUTF(it.key.conditionsHash); writeUTF(it.key.accountId)
        writeInt(it.key.scheduleIndex); writeUTF(it.key.date); writeLong(it.calendarSequence)
        writeUTF(it.operationId); writeUTF(it.operationVersion); writeBoolean(it.cancelled)
        writeBoolean(it.previousSequence != null); it.previousSequence?.let(::writeLong); writeLong(it.recordedAt)
    }
}
internal fun DataInputStream.calendarMatches(): List<CalendarMatchEvent> = List(readInt().also { require(it in 0..100000) }) {
    val sequence = readLong()
    val key = CalendarPaymentKey(readUTF(), readUTF(), readUTF(), readInt(), readUTF())
    CalendarMatchEvent(sequence, key, readLong(), readUTF(), readUTF(), readBoolean(), if (readBoolean()) readLong() else null, readLong())
}
internal val calendarMatchesMigration = object : Migration(14, 15) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE calendar_match_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, calendarId TEXT NOT NULL, conditionsHash TEXT NOT NULL, accountId TEXT NOT NULL, scheduleIndex INTEGER NOT NULL, date TEXT NOT NULL, calendarSequence INTEGER NOT NULL, operationId TEXT NOT NULL, operationVersion TEXT NOT NULL, cancelled INTEGER NOT NULL, previousSequence INTEGER, recordedAt INTEGER NOT NULL)")
        db.execSQL("CREATE INDEX index_calendar_match_events_calendarId_conditionsHash_accountId_scheduleIndex_date_sequence ON calendar_match_events(calendarId, conditionsHash, accountId, scheduleIndex, date, sequence)")
    }
}
