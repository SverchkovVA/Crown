package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate

class JournalQueryTest {
    private val accounts = listOf(Account("one", "Карта"), Account("two", "Накопительный"))
    private val categories = listOf(
        Category("food", "Продукты", OperationKind.Expense),
        Category("cafe", "Кафе", OperationKind.Expense, "food"),
        Category("home", "Дом", OperationKind.Expense),
    )
    private val expense = Operation(
        "buy", OperationKind.Expense, LocalDate.of(2026, 11, 20), "one", 300, "Обед с другом",
        parts = listOf(Allocation("cafe", 100), Allocation("home", 200)),
    )
    private val transfer = Operation(
        "transfer", OperationKind.Transfer, expense.date, "one", 500, "Резерв", destinationId = "two",
    )
    private val rows = listOf(expense, transfer)

    @Test
    fun parentCategoryFindsSplitChildAndDoesNotChangeOriginal() {
        assertEquals(listOf(expense), JournalQuery(categoryId = "food").apply(rows, accounts, categories))
        assertEquals(listOf(expense), JournalQuery(categoryId = "home").apply(rows, accounts, categories))
        assertEquals(2, rows.size)
        assertEquals(rows, JournalQuery().apply(rows, accounts, categories))
    }

    @Test
    fun searchUsesAllWordsAcrossDescriptionAccountsAndCategories() {
        assertEquals(listOf(expense), JournalQuery("  ОБЕД  кафе ").apply(rows, accounts, categories))
        assertEquals(listOf(transfer), JournalQuery("накопительный резерв").apply(rows, accounts, categories))
        assertTrue(JournalQuery("неизвестно").apply(rows, accounts, categories).isEmpty())
        assertEquals(0, JournalQuery("  ").count)
    }

    @Test
    fun negativeOpeningBalanceCanBeShownAndSearched() {
        val opening = Operation("opening", OperationKind.Opening, expense.date, "one", -500, "Начальный долг")
        assertEquals(listOf(opening), JournalQuery().apply(listOf(opening), accounts, categories))
        assertEquals(listOf(opening), JournalQuery("долг", OperationKind.Opening).apply(listOf(opening), accounts, categories))
        assertTrue(JournalQuery(categoryId = "food").apply(listOf(opening), accounts, categories).isEmpty())
    }

    @Test
    fun kindCombinesWithSearchAndCategory() {
        assertEquals(listOf(transfer), JournalQuery(kind = OperationKind.Transfer).apply(rows, accounts, categories))
        assertTrue(JournalQuery(kind = OperationKind.Income).apply(rows, accounts, categories).isEmpty())
        assertTrue(JournalQuery("Резерв", OperationKind.Transfer, "food").apply(rows, accounts, categories).isEmpty())
        assertEquals(3, JournalQuery("Обед", OperationKind.Expense, "food").count)
    }
}
