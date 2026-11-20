package ru.crown.finance.ledger

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth

class RefundsTest {
    private val day = LocalDate.of(2026, 10, 10)
    private val expense = Operation(
        "buy", OperationKind.Expense, day, "main", 1_000, "Покупка",
        parts = listOf(Allocation("food", 600), Allocation("home", 400)),
    )
    private fun history(value: Operation, vararg changes: Operation) = OperationHistory(
        listOf(Revision(value, null)) + changes.map { Revision(it, Instant.parse("2026-10-12T12:00:00Z")) },
    )
    private fun refund(id: String, amount: Long, category: String = "food") = Operation(
        id, OperationKind.Refund, day.plusDays(1), "reserve", amount, "Возврат",
        categoryId = category, refundOf = expense.id, refundVersion = expense.fingerprint(),
    )

    @Test
    fun refundCreditsChosenAccountAndReducesExpenseWithoutIncome() {
        val returned = refund("r", 150)
        validateRefunds(listOf(history(expense), history(returned)))
        val snapshot = LedgerSnapshot.from(
            YearMonth.from(day), listOf(Account("main", "Основной"), Account("reserve", "Резерв")),
            listOf(expense, returned),
        ).copy(categories = listOf(Category("food", "Продукты", OperationKind.Expense), Category("home", "Дом", OperationKind.Expense)))
        assertEquals((-850).toBigInteger(), snapshot.totalMinor)
        assertEquals(850.toBigInteger(), snapshot.expenseMinor)
        assertEquals(0.toBigInteger(), snapshot.incomeMinor)
        assertEquals(150.toBigInteger(), snapshot.balances[1].minor)
        val totals = categoryTotals(snapshot).associate { it.name to it.minor }
        assertEquals(450.toBigInteger(), totals["Продукты"])
        assertEquals(400.toBigInteger(), totals["Дом"])
    }

    @Test
    fun cumulativeRefundsCannotBorrowFromAnotherCategoryOrVersion() {
        val one = refund("r1", 400)
        val two = refund("r2", 201)
        assertThrows(IllegalArgumentException::class.java) {
            validateRefunds(listOf(history(expense), history(one), history(two)))
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateRefunds(listOf(history(expense), history(refund("r", 401, "home"))))
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateRefunds(listOf(history(expense), history(one.copy(date = day.minusDays(1)))))
        }
    }

    @Test
    fun validPurchaseCorrectionPreservesSourceVersionAndRechecksCapacity() {
        val returned = refund("r", 500)
        val increased = expense.copy(
            amountMinor = 1_100,
            parts = listOf(Allocation("food", 600), Allocation("home", 500)),
        )
        validateRefunds(listOf(history(expense, increased), history(returned)))
        val reduced = expense.copy(
            amountMinor = 800,
            parts = listOf(Allocation("food", 400), Allocation("home", 400)),
        )
        assertThrows(IllegalArgumentException::class.java) {
            validateRefunds(listOf(history(expense, reduced), history(returned)))
        }
        val cancelled = OperationHistory(listOf(Revision(expense, null), Revision(expense, Instant.now(), cancelled = true)))
        assertThrows(IllegalArgumentException::class.java) {
            validateRefunds(listOf(cancelled, history(returned)))
        }
    }

    @Test
    fun cancelledRefundReleasesCapacityAndMalformedSplitIsRejected() {
        val returned = refund("r1", 600)
        val cancelled = OperationHistory(listOf(Revision(returned, null), Revision(returned, Instant.now(), cancelled = true)))
        validateRefunds(listOf(history(expense), cancelled, history(refund("r2", 600))))
        assertThrows(IllegalArgumentException::class.java) {
            expense.copy(parts = listOf(Allocation("food", 700), Allocation("home", 400)))
        }
        assertThrows(IllegalArgumentException::class.java) {
            expense.copy(parts = listOf(Allocation("food", 500), Allocation("food", 500)))
        }
    }

    @Test
    fun detailsAndFingerprintAreStableAcrossSerialization() {
        val extra = decodeOperationDetails(expense.encodeOperationDetails())
        assertEquals(expense.parts, extra.parts)
        val returned = refund("r", 125)
        val metadata = decodeOperationDetails(returned.encodeOperationDetails())
        assertEquals(returned.refundOf, metadata.refundOf)
        assertEquals(returned.refundVersion, metadata.refundVersion)
        assertEquals(expense.fingerprint(), expense.copy(parts = extra.parts).fingerprint())
    }
}
