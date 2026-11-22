package ru.crown.finance.ledger

import java.math.BigInteger
import java.time.LocalDate
import java.time.YearMonth

internal enum class PlanningKind(val wire: String, val label: String) { Budget("budget", "Бюджеты"), Goal("savings_goal", "Цели"), Calendar("payment_calendar", "Календари") }
internal sealed interface PlanningConditions {
    val kind: PlanningKind
    fun validate()
}
internal data class BudgetPlan(
    val start: LocalDate, val end: LocalDate, val limit: Long,
    val accounts: Set<String>? = null, val categories: Set<String?>? = null, val monthly: Boolean = false,
) : PlanningConditions {
    override val kind get() = PlanningKind.Budget
    override fun validate() {
        require(start.year in 1900..9999 && end.year in 1900..9999 && end >= start) { "Проверьте период бюджета" }
        require(limit >= 0) { "Лимит не может быть отрицательным" }
        require(accounts == null || (accounts.size <= 10000 && accounts.all { it.length in 1..64 }))
        require(categories == null || (categories.size <= 10000 && categories.all { it == null || it.length in 1..64 }))
        if (monthly) require(start.dayOfMonth == 1 && YearMonth.from(start).atEndOfMonth() == end) { "Месячный бюджет должен охватывать месяц целиком" }
    }
}
internal data class GoalPlan(
    val asOf: LocalDate, val current: Long, val target: Long, val monthly: Long, val deadline: LocalDate? = null,
) : PlanningConditions {
    override val kind get() = PlanningKind.Goal
    override fun validate() {
        require(asOf.year in 1900..9999 && (deadline == null || (deadline.year in 1900..9999 && deadline >= asOf))) { "Срок цели не может быть раньше даты расчёта" }
        require(current >= 0 && target > 0 && monthly >= 0) { "Цель должна быть положительной, накопления и пополнение — неотрицательными" }
    }
}
internal data class BudgetSource(val operation: Operation, val spent: BigInteger)
internal data class BudgetExecution(val plan: BudgetPlan, val sources: List<BudgetSource>) {
    val spent: BigInteger get() = sources.fold(BigInteger.ZERO) { total, row -> total + row.spent }
    val remaining: BigInteger get() = plan.limit.toBigInteger() - spent
}
internal fun executeBudget(plan: BudgetPlan, operations: List<Operation>, checkCancelled: () -> Unit = {}): BudgetExecution {
    plan.validate()
    require(operations.map { it.id }.distinct().size == operations.size)
    val sources = operations.mapNotNull { operation ->
        checkCancelled()
        if (operation.date !in plan.start..plan.end || (plan.accounts != null && operation.accountId !in plan.accounts)) return@mapNotNull null
        val spent = when (operation.kind) {
            OperationKind.Expense, OperationKind.Refund -> {
                val sign = if (operation.kind == OperationKind.Refund) -BigInteger.ONE else BigInteger.ONE
                operation.allocations().filter { plan.categories == null || it.categoryId in plan.categories }
                    .fold(BigInteger.ZERO) { total, part -> total + part.amountMinor.toBigInteger() * sign }
            }
            OperationKind.Transfer -> if (plan.categories == null || null in plan.categories) operation.feeMinor.toBigInteger() else BigInteger.ZERO
            else -> BigInteger.ZERO
        }
        if (spent == BigInteger.ZERO) null else BudgetSource(operation, spent)
    }
    return BudgetExecution(plan, sources.sortedWith(compareByDescending<BudgetSource> { it.operation.date }.thenBy { it.operation.id }))
}
internal fun LedgerSnapshot.currentOperations(): List<Operation> =
    if (history.isEmpty()) operations else history.mapNotNull { it.current.value.takeUnless { _ -> it.current.cancelled } }

internal data class GoalProjection(
    val plan: GoalPlan, val remaining: BigInteger, val basisPoints: Int, val firstOn: LocalDate?,
    val contributions: BigInteger?, val reachedOn: LocalDate?, val byDeadline: Int?,
    val requiredMonthly: BigInteger?, val atDeadline: BigInteger?,
)
private fun calendarMonth(anchor: LocalDate, count: BigInteger): LocalDate? {
    if (count.signum() < 0 || count > (9999L * 12).toBigInteger()) return null
    return runCatching { anchor.plusMonths(count.toLong()).takeIf { it.year in 1900..9999 } }.getOrNull()
}
private fun ceilPositive(numerator: BigInteger, denominator: BigInteger) =
    (numerator + denominator - BigInteger.ONE) / denominator
internal fun projectGoal(plan: GoalPlan): GoalProjection {
    plan.validate()
    val current = plan.current.toBigInteger()
    val target = plan.target.toBigInteger()
    val monthly = plan.monthly.toBigInteger()
    val remaining = (target - current).max(BigInteger.ZERO)
    val contributions = if (remaining == BigInteger.ZERO) BigInteger.ZERO else if (monthly > BigInteger.ZERO) ceilPositive(remaining, monthly) else null
    val reached = contributions?.let { calendarMonth(plan.asOf, it) }
    var payments: Int? = null
    var required: BigInteger? = null
    var projected: BigInteger? = null
    if (plan.deadline != null) {
        var count = (plan.deadline.year - plan.asOf.year) * 12 + plan.deadline.monthValue - plan.asOf.monthValue
        if (count > 0 && plan.asOf.plusMonths(count.toLong()) > plan.deadline) count--
        payments = count
        required = if (remaining == BigInteger.ZERO) BigInteger.ZERO else if (count > 0) ceilPositive(remaining, count.toBigInteger()) else null
        projected = current + count.toBigInteger() * monthly
    }
    return GoalProjection(plan, remaining, (current.min(target) * 10000.toBigInteger() / target).toInt(),
        calendarMonth(plan.asOf, BigInteger.ONE), contributions, reached, payments, required, projected)
}
internal data class GoalAccountProgress(val account: Account, val projection: GoalProjection, val sources: List<Operation>)
internal fun goalAccountProgress(plan: GoalPlan, account: Account, operations: List<Operation>): GoalAccountProgress {
    plan.validate()
    require(operations.map { it.id }.distinct().size == operations.size)
    val sources = operations.filter { it.date <= plan.asOf && (it.accountId == account.id || it.destinationId == account.id) }
        .sortedWith(compareBy<Operation> { it.date }.thenBy { it.id })
    val balance = sources.fold(BigInteger.ZERO) { sum, operation -> sum + operation.movementFor(account.id).toBigInteger() }
    require(balance >= BigInteger.ZERO && balance <= Long.MAX_VALUE.toBigInteger()) { "Для цели нужен неотрицательный остаток в допустимом диапазоне суммы" }
    return GoalAccountProgress(account, projectGoal(plan.copy(current = balance.toLong())), sources)
}
