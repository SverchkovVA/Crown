package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.YearMonth

class BookControlTest {
    private val month = YearMonth.of(2026, 11)
    private fun event(closed: Boolean, reason: String = "") = PeriodEvent(
        month = month.toString(), closed = closed, reason = reason, recordedAt = 1,
    )

    @Test
    fun closeBlocksBothDatesAndReopenRequiresReason() {
        val events = listOf(event(true))
        assertThrows(IllegalArgumentException::class.java) { requireOpenDates(events, month.atDay(1)) }
        assertThrows(IllegalArgumentException::class.java) {
            requireOpenDates(events, month.plusMonths(1).atDay(1), month.atDay(1))
        }
        requireOpenDates(events, month.plusMonths(1).atDay(1))
        assertThrows(IllegalArgumentException::class.java) { closedMonths(events + event(false)) }
        assertTrue(closedMonths(events + event(false, "Уточнение")).isEmpty())
    }

    @Test
    fun datedReconciliationExcludesLaterOperationsAndChangesFingerprint() {
        val account = Account("main", "Основной")
        val date = month.atDay(5)
        val early = Operation("early", OperationKind.Income, date, account.id, 100, "Раньше")
        val later = early.copy(id = "later", date = date.plusDays(1))
        fun histories(vararg values: Operation) = values.map { OperationHistory(listOf(Revision(it, null))) }
        val checked = previewReconciliation(histories(early, later), listOf(account), account.id, date)
        assertEquals(100.toBigInteger(), checked.calculatedMinor)
        assertEquals(checked, previewReconciliation(histories(early), listOf(account), account.id, date))
        assertNotEquals(checked.fingerprint, previewReconciliation(
            histories(early.copy(amountMinor = 150), later), listOf(account), account.id, date,
        ).fingerprint)
    }

    @Test
    fun reconciliationDifferenceHandlesBalancesBeyondOneAmount() {
        val account = Account("main", "Основной")
        val first = Operation("one", OperationKind.Income, month.atDay(1), account.id, Long.MAX_VALUE, "Доход")
        val history = listOf(first, first.copy(id = "two")).map { OperationHistory(listOf(Revision(it, null))) }
        val preview = previewReconciliation(history, listOf(account), account.id, month.atEndOfMonth())
        val record = Reconciliation(
            "record", account.id, month.atEndOfMonth().toString(), -1,
            preview.calculatedMinor.toString(), preview.fingerprint, 1,
        )
        assertEquals(-Long.MAX_VALUE.toBigInteger() * 2.toBigInteger() - 1.toBigInteger(), record.difference)
    }
}
