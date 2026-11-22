package ru.crown.finance.ledger

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.math.BigDecimal
import java.time.LocalDate
import java.util.Base64

private const val DEPOSIT_BYTES = 4 * 1024 * 1024
private const val DEPOSIT_TEXT = 6 * 1024 * 1024
private fun <T> pick(raw: String, values: List<T>, wire: (T) -> String): T =
    requireNotNull(values.singleOrNull { wire(it) == raw }) { "Неизвестное правило расчёта" }

internal fun encodeDeposit(scenario: DepositScenario): String {
    scenario.validate()
    val output = ByteArrayOutputStream()
    DataOutputStream(output).use { out ->
        out.writeInt(1); out.writeUTF("deposit-daily-v1")
        out.writeLong(scenario.opened.toEpochDay()); out.writeLong(scenario.matures.toEpochDay())
        out.writeLong(scenario.initial); out.writeLong(scenario.minimum)
        out.writeUTF(scenario.boundary.wire); out.writeUTF(scenario.moment.wire); out.writeUTF(scenario.dayCount.wire)
        out.writeUTF(scenario.rounding.wire); out.writeUTF(scenario.roundingStage.wire)
        out.writeBoolean(scenario.capitalize); out.writeBoolean(scenario.allowTopups); out.writeBoolean(scenario.allowWithdrawals)
        out.writeInt(scenario.rates.size)
        for (rate in scenario.rates.sortedBy { it.date }) {
            out.writeUTF(rate.id); out.writeLong(rate.date.toEpochDay()); out.writeUTF(rate.percent.stripTrailingZeros().toPlainString())
        }
        out.writeInt(scenario.movements.size)
        for (move in scenario.movements.sortedWith(compareBy<DepositMovement> { it.date }.thenBy { it.id })) {
            out.writeUTF(move.id); out.writeLong(move.date.toEpochDay()); out.writeLong(move.minor)
        }
        out.writeInt(scenario.payouts.size)
        scenario.payouts.sorted().forEach { out.writeLong(it.toEpochDay()) }
    }
    require(output.size() <= DEPOSIT_BYTES) { "Сценарий слишком большой" }
    return Base64.getEncoder().encodeToString(output.toByteArray())
}
internal fun decodeDeposit(text: String): DepositScenario {
    require(text.length in 1..DEPOSIT_TEXT)
    val bytes = Base64.getDecoder().decode(text)
    require(bytes.size <= DEPOSIT_BYTES && Base64.getEncoder().encodeToString(bytes) == text)
    val value = DataInputStream(ByteArrayInputStream(bytes)).use { input ->
        require(input.readInt() == 1 && input.readUTF() == "deposit-daily-v1") { "Неизвестный формат сценария вклада" }
        val opened = LocalDate.ofEpochDay(input.readLong()); val matures = LocalDate.ofEpochDay(input.readLong())
        val initial = input.readLong(); val minimum = input.readLong()
        val boundary = pick(input.readUTF(), AccrualBoundary.entries, AccrualBoundary::wire)
        val moment = pick(input.readUTF(), BalanceMoment.entries, BalanceMoment::wire)
        val count = pick(input.readUTF(), DepositDayCount.entries, DepositDayCount::wire)
        val rounding = pick(input.readUTF(), DepositRounding.entries, DepositRounding::wire)
        val stage = pick(input.readUTF(), RoundingStage.entries, RoundingStage::wire)
        val capitalize = input.readBoolean(); val topups = input.readBoolean(); val withdrawals = input.readBoolean()
        fun size(minimum: Int = 0) = input.readInt().also { require(it in minimum..10000) }
        val rates = List(size(1)) { DepositRate(input.readUTF(), LocalDate.ofEpochDay(input.readLong()), BigDecimal(input.readUTF())) }
        val moves = List(size()) { DepositMovement(input.readUTF(), LocalDate.ofEpochDay(input.readLong()), input.readLong()) }
        val payouts = List(size(1)) { LocalDate.ofEpochDay(input.readLong()) }
        require(input.read() == -1)
        DepositScenario(opened, matures, initial, rates, moves, payouts, boundary, moment, count, rounding, stage, capitalize, topups, withdrawals, minimum)
    }
    value.validate()
    require(encodeDeposit(value) == text) { "Нарушен канонический формат сценария" }
    return value
}

@Entity(tableName = "deposit_scenario_events")
internal data class DepositEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val id: String, val name: String, val payload: String, val archived: Boolean, val recordedAt: Long,
) {
    fun scenario() = decodeDeposit(payload)
}
internal fun currentDeposits(events: List<DepositEvent>) = events.associateBy(DepositEvent::id).values.sortedBy { it.name.lowercase() }

internal fun validateDeposits(events: List<DepositEvent>) {
    require(events.size <= 100000 && events.all { it.sequence > 0 && it.recordedAt >= 0 && it.id.length in 1..64 && it.name == it.name.trim() && it.name.length in 1..80 })
    require(events.zipWithNext().all { (a, b) -> a.sequence < b.sequence })
    val current = currentDeposits(events).filterNot { it.archived }
    require(current.size <= 1000 && current.map { it.name.lowercase() }.distinct().size == current.size)
    for (payload in events.map { it.payload }.distinct()) forecastDeposit(decodeDeposit(payload))
}
@Dao internal interface DepositDao {
    @Query("SELECT * FROM deposit_scenario_events ORDER BY sequence") suspend fun events(): List<DepositEvent>
    @Insert suspend fun insert(event: DepositEvent): Long
}
internal fun DataOutputStream.deposits(events: List<DepositEvent>) {
    writeInt(events.size)
    for (event in events) {
        writeLong(event.sequence); writeUTF(event.id); writeUTF(event.name)
        val raw = event.payload.toByteArray(Charsets.US_ASCII)
        require(raw.size <= DEPOSIT_TEXT); writeInt(raw.size); write(raw)
        writeBoolean(event.archived); writeLong(event.recordedAt)
    }
}
internal fun DataInputStream.deposits(): List<DepositEvent> {
    val count = readInt().also { require(it in 0..100000) }
    return List(count) {
        val sequence = readLong(); val id = readUTF(); val name = readUTF()
        val length = readInt().also { require(it in 1..DEPOSIT_TEXT) }
        val payload = ByteArray(length).also(::readFully).toString(Charsets.US_ASCII)
        DepositEvent(sequence, id, name, payload, readBoolean(), readLong())
    }
}
internal val depositMigration = object : Migration(10, 11) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE deposit_scenario_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, archived INTEGER NOT NULL, recordedAt INTEGER NOT NULL)")
    }
}
