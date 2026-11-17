package ru.crown.finance.ledger

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDate
import java.time.YearMonth

class LedgerTest {
    private val day = LocalDate.of(2026, 10, 18)
    private val month = YearMonth.from(day)
    private val first = Account("main", "Основная карта")
    private val second = Account("savings", "Накопительный")

    @Test
    fun transferUsesBothAccountsButOnlyFeeIsExpense() {
        val opening = Operation("0", OperationKind.Opening, day.minusMonths(1), first.id, 200_000, "Остаток")
        val transfer = Operation("1", OperationKind.Transfer, day, first.id, 50_000, "Перевод", second.id, 250)
        val snapshot = LedgerSnapshot.from(month, listOf(first, second), listOf(transfer, opening))

        assertEquals(-50_250L, transfer.movementFor(first.id))
        assertEquals(50_000L, transfer.movementFor(second.id))
        assertEquals(listOf(149_750L, 50_000L).map(Long::toBigInteger), snapshot.balances.map(AccountBalance::minor))
        assertEquals((199_750L).toBigInteger(), snapshot.totalMinor)
        assertEquals((250L).toBigInteger(), snapshot.expenseMinor)
        assertEquals((0L).toBigInteger(), snapshot.incomeMinor)
        assertEquals(listOf(transfer), snapshot.operations)
    }

    @Test
    fun monthEndExcludesLaterMovementAndAllowsExactNegativeBalance() {
        val sale = Operation("0", OperationKind.Income, day, first.id, 100_001, "Оплата")
        val purchase = Operation("1", OperationKind.Expense, day.plusDays(1), first.id, 100_002, "Покупка")
        val later = Operation("2", OperationKind.Income, day.plusMonths(1), first.id, 99_000, "Подработка")
        val snapshot = LedgerSnapshot.from(month, listOf(first), listOf(later, sale, purchase))

        assertEquals((-1L).toBigInteger(), snapshot.totalMinor)
        assertEquals((100_001L).toBigInteger(), snapshot.incomeMinor)
        assertEquals((100_002L).toBigInteger(), snapshot.expenseMinor)
        assertEquals(listOf(purchase, sale), snapshot.operations)
    }

    @Test
    fun totalsRemainExactBeyondTheSizeOfASingleOperation() {
        val income = Operation("1", OperationKind.Income, day, first.id, Long.MAX_VALUE, "Доход")
        val other = income.copy(id = "2")
        val snapshot = LedgerSnapshot.from(month, listOf(first), listOf(income, other))
        val expected = Long.MAX_VALUE.toBigInteger() * 2.toBigInteger()
        assertEquals(expected, snapshot.incomeMinor)
        assertEquals(expected, snapshot.totalMinor)
    }

    @Test
    fun moneyInputNeverRoundsOrOverflows() {
        assertEquals(123_456L, parseMinor("1 234,56"))
        assertEquals(-1L, parseMinor("−0,01", signed = true))
        assertEquals(Long.MAX_VALUE, parseMinor("92233720368547758.07"))
        assertNull(parseMinor("1,001"))
        assertNull(parseMinor("1e2"))
        assertNull(parseMinor("-10"))
        assertNull(parseMinor("92233720368547758.08"))
    }

    @Test(expected = ArithmeticException::class)
    fun overflowingTransferCannotBecomeAFictitiousCredit() {
        Operation("1", OperationKind.Transfer, day, first.id, Long.MAX_VALUE, "Перевод", second.id, 1)
    }

    @Test(expected = IllegalArgumentException::class)
    fun transferRequiresExistingDestination() {
        val transfer = Operation("1", OperationKind.Transfer, day, first.id, 100, "Перевод", second.id)
        LedgerSnapshot.from(month, listOf(first), listOf(transfer))
    }
}
