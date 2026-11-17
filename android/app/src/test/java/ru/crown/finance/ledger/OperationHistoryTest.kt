package ru.crown.finance.ledger

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

class OperationHistoryTest {
    @Test
    fun correctionsAndCancellationKeepTheOriginalAndPreviousRevisions() {
        val operation = Operation("1", OperationKind.Expense, LocalDate.of(2026, 10, 18), "main", 110, "Тест")
        val other = operation.copy(id = "2")
        val revised = operation.copy(amountMinor = 310)
        val firstChange = Revision(revised, Instant.parse("2026-10-20T12:00:00Z"))
        val cancellation = firstChange.copy(recordedAt = Instant.parse("2026-10-21T12:00:00Z"), cancelled = true)

        val (current, unchanged) = assembleHistory(listOf(operation, other), listOf(firstChange, cancellation))
        assertEquals(listOf(operation, revised, revised), current.revisions.map(Revision::value))
        assertEquals(cancellation, current.current)
        assertNull(current.revisions.first().recordedAt)
        assertEquals(other, unchanged.current.value)
        assertFalse(unchanged.current.cancelled)
    }
}
