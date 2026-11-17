package ru.crown.finance.ledger

import org.junit.Assert.assertEquals
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
        assertEquals(listOf(149_750L, 50_000L), snapshot.balances.map(AccountBalance::minor))
        assertEquals(199_750L, snapshot.totalMinor)
        assertEquals(250L, snapshot.expenseMinor)
        assertEquals(0L, snapshot.incomeMinor)
        assertEquals(listOf(transfer), snapshot.operations)
    }

    @Test
    fun monthEndExcludesLaterMovementAndAllowsExactNegativeBalance() {
        val sale = Operation("0", OperationKind.Income, day, first.id, 100_001, "Оплата")
        val purchase = Operation("1", OperationKind.Expense, day.plusDays(1), first.id, 100_002, "Покупка")
        val later = Operation("2", OperationKind.Income, day.plusMonths(1), first.id, 99_000, "Подработка")
        val snapshot = LedgerSnapshot.from(month, listOf(first), listOf(later, sale, purchase))

        assertEquals(-1L, snapshot.totalMinor)
        assertEquals(100_001L, snapshot.incomeMinor)
        assertEquals(100_002L, snapshot.expenseMinor)
        assertEquals(listOf(purchase, sale), snapshot.operations)
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
