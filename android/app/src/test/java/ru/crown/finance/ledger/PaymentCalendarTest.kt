package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate

class PaymentCalendarTest {
    private val day = LocalDate.of(2024, 1, 31)
    private fun payment() = PaymentSchedule("Учебный платёж", 1000, false, day, RepeatUnit.Months)
    private fun plan() = CalendarPlan(listOf(payment()))
    private val opening = Operation("opening", OperationKind.Opening, day.minusDays(1), "main", 10000, "Остаток")
    private val purchase = Operation("purchase", OperationKind.Expense, day, "main", 1000, "Оплачен")
    @Test fun monthlyAnchorDoesNotDriftAndPeriodIsInclusive() {
        val rows = forecastPayments(plan(), day, LocalDate.of(2024, 4, 30))
        assertEquals(listOf(day, LocalDate.of(2024, 2, 29), LocalDate.of(2024, 3, 31), LocalDate.of(2024, 4, 30)), rows.map { it.date })
        val clipped = CalendarPlan(listOf(payment().copy(interval = 2, last = LocalDate.of(2024, 5, 31))))
        assertEquals(listOf(LocalDate.of(2024, 3, 31), LocalDate.of(2024, 5, 31)), forecastPayments(clipped, day.plusDays(1), day.plusYears(1)).map { it.date })
        assertTrue(forecastPayments(plan(), day.minusYears(1), day.minusDays(1)).isEmpty())
    }
    @Test fun dailyWeeklyAndOnceUseFirstDateAndSkipElapsedIntervals() {
        val schedules = CalendarPlan(listOf(payment().copy(unit = RepeatUnit.Days, interval = 3), payment().copy(unit = RepeatUnit.Weeks, interval = 2), payment().copy(unit = RepeatUnit.Once)))
        val rows = forecastPayments(schedules, day.plusDays(10), day.plusDays(15))
        assertEquals(listOf(day.plusDays(12), day.plusDays(14), day.plusDays(15)), rows.map { it.date })
        assertEquals(listOf(0, 1, 0), rows.map { it.scheduleIndex })
        assertEquals(1, forecastPayments(CalendarPlan(listOf(payment().copy(unit = RepeatUnit.Once))), day, day).size)
    }
    @Test fun invalidRulesOversizedForecastAndCancellationAreRejected() {
        for (value in listOf(payment().copy(amount = 0), payment().copy(name = ""), payment().copy(interval = 0), payment().copy(interval = 1201), payment().copy(last = day.minusDays(1)), payment().copy(unit = RepeatUnit.Once, interval = 2))) {
            assertThrows(IllegalArgumentException::class.java) { value.validate() }
        }
        assertThrows(IllegalArgumentException::class.java) { CalendarPlan(emptyList()).validate() }
        val daily = CalendarPlan(listOf(payment().copy(unit = RepeatUnit.Days)))
        assertEquals(10000, forecastPayments(daily, day, day.plusDays(9999)).size)
        assertThrows(IllegalArgumentException::class.java) { forecastPayments(daily, day, day.plusDays(10000)) }
        assertThrows(java.util.concurrent.CancellationException::class.java) { forecastPayments(plan(), day, day.plusYears(1)) { throw java.util.concurrent.CancellationException() } }
        val edge = CalendarPlan(listOf(payment().copy(first = LocalDate.of(9999, 12, 31))))
        assertEquals(1, forecastPayments(edge, LocalDate.of(9999, 1, 1), LocalDate.of(9999, 12, 31)).size)
    }
    @Test fun matchingRemovesOnlyExpectedPaymentAndLeavesLedgerIntact() {
        val operations = listOf(opening, purchase)
        val first = forecastAccount(plan(), setOf("main"), operations, day, day)
        assertEquals(10000.toBigInteger(), first.opening)
        assertEquals(8000.toBigInteger(), first.closing)
        val match = PaymentMatch(0, purchase.id, purchase.fingerprint())
        val linked = forecastAccount(plan(), setOf("main"), operations, day, day, listOf(match))
        assertEquals(9000.toBigInteger(), linked.closing)
        assertEquals(0.toBigInteger(), linked.days.single().planned)
        assertEquals(listOf(purchase), linked.booked)
        assertEquals(operations, listOf(opening, purchase))
    }
    @Test fun staleDuplicateWrongDirectionAndMultipleAccountMatchesAreRejected() {
        val match = PaymentMatch(0, purchase.id, purchase.fingerprint())
        val operations = listOf(opening, purchase)
        for (matches in listOf(listOf(match.copy(expectedIndex = 1)), listOf(match.copy(version = "stale")), listOf(match, match))) {
            assertThrows(IllegalArgumentException::class.java) { forecastAccount(plan(), setOf("main"), operations, day, day, matches) }
        }
        assertThrows(IllegalArgumentException::class.java) { forecastAccount(plan(), setOf("main", "other"), operations, day, day, listOf(match)) }
        val income = CalendarPlan(listOf(payment().copy(income = true)))
        assertThrows(IllegalArgumentException::class.java) { forecastAccount(income, setOf("main"), operations, day, day, listOf(match)) }
        assertThrows(IllegalArgumentException::class.java) { forecastAccount(plan(), setOf("main"), operations + purchase, day, day) }
    }
    @Test fun internalTransferRetainsFeeAndLongTotalsRemainExact() {
        val transfer = Operation("transfer", OperationKind.Transfer, day, "main", 5000, "Перевод", destinationId = "other", feeMinor = 25)
        val both = forecastAccount(plan(), setOf("main", "other"), listOf(opening, transfer), day, day)
        assertEquals(8975.toBigInteger(), both.closing)
        assertEquals((-25).toBigInteger(), both.days.single().booked)
        val big = CalendarPlan(listOf(payment().copy(amount = Long.MAX_VALUE, income = true), payment().copy(amount = Long.MAX_VALUE, income = true)))
        assertEquals(Long.MAX_VALUE.toBigInteger() * 2.toBigInteger(), forecastAccount(big, setOf("main"), emptyList(), day, day).closing)
    }
    @Test fun reserveUsesFirstDayClosingRatherThanPreviousDayOpening() {
        val incoming = plan().copy(schedules = listOf(payment().copy(income = true)))
        val forecast = forecastAccount(incoming, setOf("main"), emptyList(), day, day.plusDays(1))
        val reserve = checkReserve(forecast, 500)
        assertEquals(1000.toBigInteger(), reserve.minimum)
        assertNull(reserve.firstBelow)
        assertEquals(0.toBigInteger(), reserve.shortfall)
        val missing = checkReserve(forecast, 1500)
        assertEquals(day, missing.firstBelow)
        assertEquals(500.toBigInteger(), missing.shortfall)
        assertThrows(IllegalArgumentException::class.java) { checkReserve(forecast, -1) }
    }
    @Test fun calendarConditionsAndEncryptedCopyRetainEveryVersion() {
        val conditions = CalendarPlan(listOf(payment(), payment().copy(name = "Доход", income = true, unit = RepeatUnit.Weeks, interval = 2, last = day.plusYears(1))))
        assertEquals(conditions, decodePlan(encodePlan(conditions)))
        val first = PlanningEvent(1, "calendar", PlanningKind.Calendar.wire, "Учебный календарь", encodePlan(conditions), false, 1)
        val second = first.copy(sequence = 2, payload = encodePlan(plan()), archived = true)
        val ledger = BackupLedger("Синтетика", emptyList(), emptyList(), emptyList(), planningEvents = listOf(first, second))
        assertEquals(ledger, PortableBackup.open(PortableBackup.seal(ledger, "copy-only".toCharArray()).inputStream(), "copy-only".toCharArray()))
        assertThrows(Exception::class.java) { decodePlan(encodePlan(conditions).dropLast(7)) }
    }
}
