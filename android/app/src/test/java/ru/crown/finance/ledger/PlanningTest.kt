package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.YearMonth

class PlanningTest {
    private val day = LocalDate.of(2026, 11, 15)
    private val food = Category("food", "Продукты", OperationKind.Expense)
    private val purchase = Operation("buy", OperationKind.Expense, day, "main", 1000, "Покупка",
        parts = listOf(Allocation("food", 600), Allocation(null, 400)))
    private val operations = listOf(
        Operation("open", OperationKind.Opening, day, "main", 10000, "Остаток"),
        purchase,
        Operation("refund", OperationKind.Refund, day.plusDays(1), "main", 200, "Возврат", categoryId = "food", refundOf = "buy", refundVersion = purchase.fingerprint()),
        Operation("transfer", OperationKind.Transfer, day, "main", 5000, "Перевод", "other", 25),
        Operation("income", OperationKind.Income, day, "main", 300, "Доход"),
    )
    private fun budget() = BudgetPlan(day, day.plusDays(1), 1000)

    @Test fun budgetUsesPartsRefundDateAndOnlyOriginFee() {
        val all = executeBudget(budget(), operations)
        assertEquals(825.toBigInteger(), all.spent)
        assertEquals(175.toBigInteger(), all.remaining)
        assertEquals(3, all.sources.size)
        assertEquals(400.toBigInteger(), executeBudget(budget().copy(categories = setOf("food")), operations).spent)
        assertEquals(425.toBigInteger(), executeBudget(budget().copy(categories = setOf(null)), operations).spent)
        assertEquals(0.toBigInteger(), executeBudget(budget().copy(accounts = setOf("other")), operations).spent)
        assertEquals(600.toBigInteger(), executeBudget(budget().copy(end = day, categories = setOf("food")), operations).spent)
        assertEquals((-200).toBigInteger(), executeBudget(budget().copy(start = day.plusDays(1)), operations).spent)
    }

    @Test fun noSelectionAndAllSelectionAreDifferentAndTotalsDoNotOverflow() {
        assertEquals(0.toBigInteger(), executeBudget(budget().copy(accounts = emptySet()), operations).spent)
        assertEquals(0.toBigInteger(), executeBudget(budget().copy(categories = emptySet()), operations).spent)
        val big = listOf(purchase.copy(id = "one", amountMinor = Long.MAX_VALUE, parts = emptyList()), purchase.copy(id = "two", amountMinor = Long.MAX_VALUE, parts = emptyList()))
        assertEquals(Long.MAX_VALUE.toBigInteger() * 2.toBigInteger(), executeBudget(budget(), big).spent)
        assertThrows(IllegalArgumentException::class.java) { executeBudget(budget(), listOf(purchase, purchase)) }
    }

    @Test fun goalUsesCalendarAnchorsAndCeilingDivision() {
        val goal = GoalPlan(LocalDate.of(2024, 1, 31), 100, 1000, 200, LocalDate.of(2024, 5, 30))
        val result = projectGoal(goal)
        assertEquals(900.toBigInteger(), result.remaining)
        assertEquals(1000, result.basisPoints)
        assertEquals(LocalDate.of(2024, 2, 29), result.firstOn)
        assertEquals(5.toBigInteger(), result.contributions)
        assertEquals(LocalDate.of(2024, 6, 30), result.reachedOn)
        assertEquals(3, result.byDeadline)
        assertEquals(300.toBigInteger(), result.requiredMonthly)
        assertEquals(700.toBigInteger(), result.atDeadline)
        assertNull(projectGoal(goal.copy(monthly = 0)).reachedOn)
        assertNull(projectGoal(goal.copy(deadline = goal.asOf)).requiredMonthly)
        val reached = projectGoal(goal.copy(current = 2000))
        assertEquals(10000, reached.basisPoints)
        assertEquals(goal.asOf, reached.reachedOn)
        assertEquals(0.toBigInteger(), reached.remaining)
    }

    @Test fun goalKeepsExactLargeAmountsAndCalendarLimit() {
        val goal = GoalPlan(LocalDate.of(9999, 12, 31), 0, Long.MAX_VALUE, 1)
        val result = projectGoal(goal)
        assertEquals(Long.MAX_VALUE.toBigInteger(), result.contributions)
        assertNull(result.reachedOn)
        assertNull(result.firstOn)
        val later = projectGoal(GoalPlan(LocalDate.of(2026, 1, 1), Long.MAX_VALUE, Long.MAX_VALUE, Long.MAX_VALUE, LocalDate.of(2027, 1, 1)))
        assertEquals(Long.MAX_VALUE.toBigInteger() * 13.toBigInteger(), later.atDeadline)
    }

    @Test fun accountProgressUsesHistoryAtDateAndLeavesSavedPlanUntouched() {
        val goal = GoalPlan(day.plusDays(1), 0, 10000, 100)
        val result = goalAccountProgress(goal, Account("main", "Основной"), operations)
        assertEquals(4475L, result.projection.plan.current)
        assertEquals(0L, goal.current)
        assertEquals(5, result.sources.size)
        assertThrows(IllegalArgumentException::class.java) { goalAccountProgress(goal, Account("main", "Основной"), listOf(purchase)) }
        val snapshot = LedgerSnapshot.from(YearMonth.from(day), listOf(Account("main", "Основной")), listOf(purchase))
            .copy(history = listOf(OperationHistory(listOf(Revision(purchase, null), Revision(purchase, java.time.Instant.EPOCH, true)))))
        assertTrue(snapshot.currentOperations().isEmpty())
    }

    @Test fun codecPreservesNullEmptyAndReferences() {
        for (plan in listOf(budget(), budget().copy(accounts = emptySet(), categories = emptySet()), budget().copy(accounts = setOf("main"), categories = setOf(null, "food")), GoalPlan(day, 100, 1000, 100, day.plusYears(1)))) {
            val encoded = encodePlan(plan)
            assertEquals(plan, decodePlan(encoded))
            assertEquals(encoded, encodePlan(decodePlan(encoded)))
            assertThrows(Exception::class.java) { decodePlan(encoded.dropLast(7)) }
        }
        assertNotEquals(encodePlan(budget()), encodePlan(budget().copy(categories = emptySet())))
        assertThrows(IllegalArgumentException::class.java) { validatePlanReferences(budget().copy(accounts = setOf("absent")), setOf("main"), setOf("food")) }
        assertThrows(IllegalArgumentException::class.java) { validatePlanReferences(budget().copy(categories = setOf("absent")), setOf("main"), setOf("food")) }
    }

    @Test fun invalidConditionsAndTypeChangeAreRejected() {
        for (plan in listOf(budget().copy(limit = -1), budget().copy(end = day.minusDays(1)), budget().copy(monthly = true), GoalPlan(day, -1, 100, 0), GoalPlan(day, 0, 0, 0), GoalPlan(day, 0, 100, -1), GoalPlan(day, 0, 100, 0, day.minusDays(1)))) {
            assertThrows(IllegalArgumentException::class.java) { plan.validate() }
        }
        val first = PlanningEvent(1, "plan", "budget", "Учебный", encodePlan(budget()), false, 1)
        assertThrows(IllegalArgumentException::class.java) { validatePlanning(listOf(first, first.copy(sequence = 2, kind = "savings_goal", payload = encodePlan(GoalPlan(day, 0, 100, 10)))), emptySet(), emptySet()) }
    }

    @Test fun protectedCopyPreservesVersionsOfBothKinds() {
        val first = PlanningEvent(1, "b", "budget", "Отпуск", encodePlan(budget().copy(accounts = setOf("main"), categories = setOf("food"))), false, 1)
        val last = first.copy(sequence = 2, archived = true)
        val goal = PlanningEvent(3, "g", "savings_goal", "Отпуск", encodePlan(GoalPlan(day, 0, 10000, 1000)), false, 2)
        val ledger = BackupLedger("Синтетика", listOf(Account("main", "Основной")), emptyList(), emptyList(), listOf(food), planningEvents = listOf(first, last, goal))
        assertEquals(ledger, PortableBackup.open(PortableBackup.seal(ledger, "copy-only".toCharArray()).inputStream(), "copy-only".toCharArray()))
    }
}
