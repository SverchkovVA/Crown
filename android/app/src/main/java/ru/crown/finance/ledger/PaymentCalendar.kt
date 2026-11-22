package ru.crown.finance.ledger

import java.math.BigInteger
import java.time.LocalDate
import java.time.temporal.ChronoUnit

internal enum class RepeatUnit(val label: String) { Once("Разово"), Days("Дни"), Weeks("Недели"), Months("Месяцы") }
internal data class PaymentSchedule(
    val name: String, val amount: Long, val income: Boolean, val first: LocalDate,
    val unit: RepeatUnit = RepeatUnit.Once, val interval: Int = 1, val last: LocalDate? = null,
) {
    fun validate() {
        require(name == name.trim() && name.length in 1..100) { "Введите название платежа" }
        require(amount > 0) { "Сумма платежа должна быть больше нуля" }
        require(first.year in 1900..9999 && (last == null || (last.year in 1900..9999 && last >= first))) { "Проверьте даты расписания" }
        require(interval in 1..1200) { "Интервал должен быть от 1 до 1200" }
        require(unit != RepeatUnit.Once || (interval == 1 && last == null)) { "Разовый платёж имеет только первую дату" }
    }
}
internal data class CalendarPlan(val schedules: List<PaymentSchedule>) : PlanningConditions {
    override val kind get() = PlanningKind.Calendar
    override fun validate() {
        require(schedules.size in 1..1000) { "Добавьте от 1 до 1000 расписаний" }
        schedules.forEach(PaymentSchedule::validate)
    }
}
internal data class ExpectedPayment(val scheduleIndex: Int, val date: LocalDate, val schedule: PaymentSchedule) {
    val signed: BigInteger get() = schedule.amount.toBigInteger().let { if (schedule.income) it else -it }
}
internal fun forecastPayments(plan: CalendarPlan, start: LocalDate, end: LocalDate, checkCancelled: () -> Unit = {}): List<ExpectedPayment> {
    plan.validate()
    require(start.year in 1900..9999 && end.year in 1900..9999 && end >= start) { "Проверьте период прогноза" }
    val rows = mutableListOf<ExpectedPayment>()
    plan.schedules.forEachIndexed { index, schedule ->
        checkCancelled()
        val first = maxOf(start, schedule.first)
        val last = minOf(end, schedule.last ?: end)
        if (first <= last) {
            fun add(date: LocalDate) {
                checkCancelled()
                if (date in first..last) {
                    require(rows.size < 10000) { "В прогнозе больше 10 000 платежей. Сократите период" }
                    rows += ExpectedPayment(index, date, schedule)
                }
            }
            when (schedule.unit) {
                RepeatUnit.Once -> add(schedule.first)
                RepeatUnit.Days, RepeatUnit.Weeks -> {
                    val step = schedule.interval.toLong() * if (schedule.unit == RepeatUnit.Weeks) 7 else 1
                    val elapsed = ChronoUnit.DAYS.between(schedule.first, first)
                    var ordinal = schedule.first.toEpochDay() + (elapsed + step - 1) / step * step
                    while (ordinal <= last.toEpochDay()) { add(LocalDate.ofEpochDay(ordinal)); ordinal += step }
                }
                RepeatUnit.Months -> {
                    fun offset(date: LocalDate) = (date.year - schedule.first.year) * 12 + date.monthValue - schedule.first.monthValue
                    var month = offset(first) / schedule.interval * schedule.interval
                    while (month <= offset(last)) {
                        add(schedule.first.plusMonths(month.toLong()))
                        month += schedule.interval
                    }
                }
            }
        }
    }
    return rows.sortedWith(compareBy<ExpectedPayment> { it.date }.thenBy { it.scheduleIndex })
}
internal data class PaymentMatch(val expectedIndex: Int, val operationId: String, val version: String)
internal data class ForecastDay(val date: LocalDate, val booked: BigInteger, val planned: BigInteger, val closing: BigInteger)
internal data class AccountForecast(
    val start: LocalDate, val end: LocalDate, val opening: BigInteger, val expected: List<ExpectedPayment>,
    val booked: List<Operation>, val matches: List<PaymentMatch>, val days: List<ForecastDay>,
) {
    val closing get() = days.lastOrNull()?.closing ?: opening
}
internal fun forecastAccount(
    plan: CalendarPlan, accountIds: Set<String>, operations: List<Operation>, start: LocalDate, end: LocalDate,
    matches: List<PaymentMatch> = emptyList(), checkCancelled: () -> Unit = {},
): AccountForecast {
    require(accountIds.isNotEmpty() && accountIds.all { it.length in 1..64 }) { "Выберите счета прогноза" }
    require(operations.map { it.id }.distinct().size == operations.size)
    val expected = forecastPayments(plan, start, end, checkCancelled)
    fun movement(operation: Operation) = accountIds.fold(BigInteger.ZERO) { total, id ->
        total + if (id == operation.accountId || id == operation.destinationId) operation.movementFor(id).toBigInteger() else BigInteger.ZERO
    }
    var opening = BigInteger.ZERO
    val booked = mutableListOf<Operation>()
    val changes = sortedMapOf<LocalDate, Pair<BigInteger, BigInteger>>()
    for (operation in operations) {
        checkCancelled()
        val signed = movement(operation)
        if (operation.date < start) opening += signed
        else if (operation.date <= end && signed != BigInteger.ZERO) {
            booked += operation
            val value = changes[operation.date] ?: (BigInteger.ZERO to BigInteger.ZERO)
            changes[operation.date] = value.first + signed to value.second
        }
    }
    require(matches.isEmpty() || accountIds.size == 1) { "Для сопоставления выберите один счёт" }
    require(matches.map { it.expectedIndex }.distinct().size == matches.size && matches.map { it.operationId }.distinct().size == matches.size) { "Каждое ожидание и запись сопоставляются один раз" }
    val used = mutableSetOf<Int>()
    for (match in matches) {
        val payment = requireNotNull(expected.getOrNull(match.expectedIndex)) { "Ожидание не найдено" }
        val operation = requireNotNull(booked.singleOrNull { it.id == match.operationId }) { "Операция не найдена" }
        require(operation.fingerprint() == match.version && operation.date == payment.date && movement(operation) == payment.signed) { "Версия, дата и сумма операции должны совпадать с ожиданием" }
        used += match.expectedIndex
    }
    expected.forEachIndexed { index, payment ->
        checkCancelled()
        if (index !in used) {
            val value = changes[payment.date] ?: (BigInteger.ZERO to BigInteger.ZERO)
            changes[payment.date] = value.first to value.second + payment.signed
        }
    }
    var balance = opening
    val days = changes.map { (date, values) ->
        balance += values.first + values.second
        ForecastDay(date, values.first, values.second, balance)
    }
    return AccountForecast(start, end, opening, expected, booked.sortedWith(compareBy<Operation> { it.date }.thenBy { it.id }), matches.sortedBy { it.expectedIndex }, days)
}
internal data class ReserveResult(val minimum: BigInteger, val on: LocalDate, val firstBelow: LocalDate?, val shortfall: BigInteger)
internal fun checkReserve(forecast: AccountForecast, reserve: Long): ReserveResult {
    require(reserve >= 0) { "Резерв не может быть отрицательным" }
    var minimum = forecast.days.firstOrNull()?.takeIf { it.date == forecast.start }?.closing ?: forecast.opening
    var on = forecast.start
    var firstBelow = forecast.start.takeIf { minimum < reserve.toBigInteger() }
    for (day in forecast.days) {
        if (day.closing < minimum) { minimum = day.closing; on = day.date }
        if (firstBelow == null && day.closing < reserve.toBigInteger()) firstBelow = day.date
    }
    return ReserveResult(minimum, on, firstBelow, (reserve.toBigInteger() - minimum).max(BigInteger.ZERO))
}
