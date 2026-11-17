package ru.crown.finance.ledger

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate
import java.time.YearMonth

class CategoriesTest {
    @Test
    fun periodGroupsRevenueAndSpendingAndCountsOnlyTheFeeFromATransfer() {
        val day = LocalDate.of(2026, 10, 2)
        val main = Account("main", "Основной")
        val second = Account("reserve", "Резерв")
        val food = Category("food", "Продукты", OperationKind.Expense)
        val operations = listOf(
            Operation("1", OperationKind.Expense, day, main.id, 350, "Магазин", categoryId = food.id),
            Operation("2", OperationKind.Expense, day, main.id, 515, "Покупка", categoryId = food.id),
            Operation("3", OperationKind.Income, day, main.id, 2_000, "Доход"),
            Operation("4", OperationKind.Transfer, day, main.id, 500, "Перевод", second.id, 25),
            Operation("5", OperationKind.Expense, day.minusMonths(1), main.id, 608, "Другой месяц", categoryId = food.id),
        )
        val snapshot = LedgerSnapshot.from(YearMonth.from(day), listOf(main, second), operations)
            .copy(categories = listOf(food))

        val totals = categoryTotals(snapshot).associate { it.name to it.minor }
        assertEquals(865.toBigInteger(), totals.getValue("Продукты"))
        assertEquals(2_000.toBigInteger(), totals.getValue("Без категории"))
        assertEquals(25.toBigInteger(), totals.getValue("Комиссии переводов"))
        assertEquals(3, totals.size)
    }
}
