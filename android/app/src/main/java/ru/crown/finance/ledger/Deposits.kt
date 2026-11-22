package ru.crown.finance.ledger

import java.math.BigDecimal
import java.math.BigInteger
import java.time.LocalDate
import java.time.temporal.ChronoUnit

internal enum class AccrualBoundary(val wire: String, val label: String) {
    OpeningIncluded("opening_included", "Включать день открытия"),
    MaturityIncluded("maturity_included", "Включать день окончания"),
}
internal enum class BalanceMoment(val wire: String, val label: String) {
    Before("before_movements", "До движений дня"), After("after_movements", "После движений дня"),
}
internal enum class DepositDayCount(val wire: String, val label: String) {
    Fixed365("fixed_365", "Всегда 365 дней"), CalendarYear("calendar_year", "365 или 366 по календарю"),
}
internal enum class DepositRounding(val wire: String, val label: String) {
    HalfUp("half_up", "Половину вверх"), HalfEven("half_even", "Половину к чётному"), Down("down", "Вниз"),
}
internal enum class RoundingStage(val wire: String, val label: String) {
    Daily("daily", "Каждый день"), Payout("payout", "В дату выплаты"),
}
internal data class DepositRate(val id: String, val date: LocalDate, val percent: BigDecimal)
internal data class DepositMovement(val id: String, val date: LocalDate, val minor: Long)
internal data class DepositScenario(
    val opened: LocalDate, val matures: LocalDate, val initial: Long,
    val rates: List<DepositRate>, val movements: List<DepositMovement>, val payouts: List<LocalDate>,
    val boundary: AccrualBoundary = AccrualBoundary.MaturityIncluded,
    val moment: BalanceMoment = BalanceMoment.After, val dayCount: DepositDayCount = DepositDayCount.CalendarYear,
    val rounding: DepositRounding = DepositRounding.HalfUp, val roundingStage: RoundingStage = RoundingStage.Payout,
    val capitalize: Boolean = false, val allowTopups: Boolean = false, val allowWithdrawals: Boolean = false, val minimum: Long = 0,
) {
    fun validate() {
        require(opened.year in 1900..9999 && matures.year in 1900..9999 &&
            ChronoUnit.DAYS.between(opened, matures) in 1..36600) { "Срок вклада — от 1 до 36600 дней" }
        require(initial > 0 && minimum in 0..initial) { "Проверьте начальную сумму и неснижаемый остаток" }
        require(rates.size in 1..10000 && movements.size <= 10000 && payouts.size in 1..10000) { "Недопустимое число ставок, движений или выплат" }
        require(rates.map { it.id }.distinct().size == rates.size && rates.map { it.date }.distinct().size == rates.size) { "Ставки повторяются" }
        require(rates.minOf { it.date } == opened && rates.all { it.date in opened..matures && it.id.length in 1..64 }) { "Первая ставка должна действовать с даты открытия" }
        rates.forEach { rateUnits(it.percent) }
        require(movements.map { it.id }.distinct().size == movements.size) { "Движение повторяется" }
        for (movement in movements) {
            require(movement.id.length in 1..64 && movement.date in opened..matures && movement.minor != 0L && movement.minor != Long.MIN_VALUE) { "Проверьте движение вклада" }
            require(movement.minor <= 0 || allowTopups) { "Пополнение запрещено условиями" }
            require(movement.minor >= 0 || allowWithdrawals) { "Снятие запрещено условиями" }
        }
        require(payouts.distinct().size == payouts.size && payouts.max() == matures && payouts.all { it > opened && it <= matures }) { "Выплаты не должны повторяться; последняя — в дату окончания" }
    }
}
internal data class DepositDay(
    val date: LocalDate, val rateId: String, val base: Long, val movement: Long,
    val interestNumerator: BigInteger, val payout: Long, val capitalized: Long, val closing: Long,
)
internal data class DepositForecast(val scenario: DepositScenario, val days: List<DepositDay>) {
    val interest: BigInteger get() = days.fold(BigInteger.ZERO) { sum, day -> sum + day.payout.toBigInteger() }
    val closing: Long get() = days.last().closing
}
internal val depositDenominator = BigInteger("1335900000000000")
private val maximum = Long.MAX_VALUE.toBigInteger()
private fun BigInteger.depositLong(): Long {
    require(this >= Long.MIN_VALUE.toBigInteger() && this <= maximum) { "Сумма выходит за допустимый диапазон" }
    return toLong()
}

internal fun rateUnits(value: BigDecimal): BigInteger {
    val normalized = value.stripTrailingZeros()
    require(value >= BigDecimal.ZERO && value <= BigDecimal("1000") && normalized.scale() <= 8 && normalized.precision() <= 32) {
        "Ставка — от 0 до 1000%, не более 8 знаков после запятой"
    }
    return normalized.movePointRight(8).toBigIntegerExact()
}
internal fun parseDepositRate(text: String): BigDecimal? {
    if (!Regex("[0-9]+(?:[.,][0-9]+)?").matches(text.trim()) || text.length > 40) return null
    return runCatching { BigDecimal(text.trim().replace(',', '.')).also { rateUnits(it) } }.getOrNull()
}
private fun roundedInterest(numerator: BigInteger, policy: DepositRounding): BigInteger {
    val (whole, remainder) = numerator.divideAndRemainder(depositDenominator)
    val half = remainder * 2.toBigInteger()
    val round = when (policy) {
        DepositRounding.Down -> false
        DepositRounding.HalfUp -> half >= depositDenominator
        DepositRounding.HalfEven -> half > depositDenominator || (half == depositDenominator && whole.testBit(0))
    }
    return whole + if (round) BigInteger.ONE else BigInteger.ZERO
}
internal fun forecastDeposit(scenario: DepositScenario, checkCancelled: () -> Unit = {}): DepositForecast {
    scenario.validate()
    val rates = scenario.rates.sortedBy { it.date }
    val units = rates.map { rateUnits(it.percent) }
    val moves = scenario.movements.groupBy { it.date }.mapValues { (_, values) -> values.fold(BigInteger.ZERO) { sum, v -> sum + v.minor.toBigInteger() } }
    val payouts = scenario.payouts.toSet()
    var index = 0
    var balance = scenario.initial.toBigInteger()
    var accrued = BigInteger.ZERO
    var date = scenario.opened
    val result = ArrayList<DepositDay>()
    while (date <= scenario.matures) {
        checkCancelled()
        while (index + 1 < rates.size && rates[index + 1].date <= date) index++
        val rate = rates[index]
        val movement = moves[date] ?: BigInteger.ZERO
        val before = balance
        balance += movement
        require(balance >= scenario.minimum.toBigInteger() && balance <= maximum) { "Движение нарушает остаток или диапазон суммы" }
        val base = if (scenario.moment == BalanceMoment.Before) before else balance
        val included = if (scenario.boundary == AccrualBoundary.OpeningIncluded) date != scenario.matures else date != scenario.opened
        val divisor = if (scenario.dayCount == DepositDayCount.CalendarYear && date.isLeapYear) 366 else 365
        val numerator = if (included) base * units[index] * (365 * 366 / divisor).toBigInteger() else BigInteger.ZERO
        accrued += if (scenario.roundingStage == RoundingStage.Daily) roundedInterest(numerator, scenario.rounding) * depositDenominator else numerator
        val payout = if (date in payouts) roundedInterest(accrued, scenario.rounding) else BigInteger.ZERO
        val capitalized = if (scenario.capitalize) payout else BigInteger.ZERO
        require(payout <= maximum && balance + capitalized <= maximum) { "Проценты выходят за допустимый диапазон" }
        balance += capitalized
        if (date in payouts) accrued = BigInteger.ZERO
        result += DepositDay(date, rate.id, base.depositLong(), movement.depositLong(), numerator, payout.depositLong(), capitalized.depositLong(), balance.depositLong())
        if (date == scenario.matures) break
        date = date.plusDays(1)
    }
    return DepositForecast(scenario, result)
}

internal fun monthlyDepositPayouts(opened: LocalDate, matures: LocalDate): List<LocalDate> {
    require(matures > opened && ChronoUnit.DAYS.between(opened, matures) <= 36600)
    val dates = mutableListOf<LocalDate>()
    var index = 1L
    while (opened.plusMonths(index) < matures) { dates += opened.plusMonths(index); index++ }
    return dates + matures
}
