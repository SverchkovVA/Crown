package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth

class CalendarMatchesTest {
    private val day = LocalDate.of(2026, 11, 15)
    private val schedule = PaymentSchedule("Учебный платёж", 1000, false, day, RepeatUnit.Months)
    private val plan = PlanningEvent(1, "calendar", PlanningKind.Calendar.wire, "Календарь", encodePlan(CalendarPlan(listOf(schedule))), false, 1)
    private val purchase = Operation("buy", OperationKind.Expense, day, "main", 1000, "Учебная покупка")
    private val opening = Operation("opening", OperationKind.Opening, day.minusDays(1), "main", 10000, "Остаток")
    private val accounts = listOf(Account("main", "Основной"), Account("other", "Другой"))
    private fun expected(event: PlanningEvent = plan, start: LocalDate = day, end: LocalDate = day) =
        forecastPayments(event.conditions() as CalendarPlan, start, end)
    private fun link(payment: ExpectedPayment = expected().single(), operation: Operation = purchase, event: PlanningEvent = plan, account: String = "main") =
        CalendarMatchEvent(1, event.paymentKey(account, payment), event.sequence, operation.id, operation.fingerprint(), false, null, 1)
    private fun history(vararg operations: Operation) = operations.map { OperationHistory(listOf(Revision(it, null))) }
    private fun validate(events: List<CalendarMatchEvent>, plans: List<PlanningEvent> = listOf(plan), source: List<OperationHistory> = history(opening, purchase)) =
        validateCalendarMatches(events, plans, accounts.map { it.id }.toSet(), source)
    private fun rejects(block: () -> Unit) = assertThrows(IllegalArgumentException::class.java, block)

    @Test fun stablePaymentKeySurvivesChangedForecastStartAndExcludesDoubleCount() {
        val december = day.plusMonths(1)
        val second = purchase.copy(id = "december", date = december)
        val all = expected(start = day.withDayOfMonth(1), end = december.withDayOfMonth(31))
        val narrow = expected(start = december.withDayOfMonth(1), end = december.withDayOfMonth(31))
        val saved = link(all[1], second)
        validate(listOf(saved), source = history(opening, purchase, second))
        val wide = resolveCalendarMatches(plan, "main", all, listOf(opening, purchase, second), listOf(saved))
        val small = resolveCalendarMatches(plan, "main", narrow, listOf(opening, purchase, second), listOf(saved))
        assertEquals(1, wide.matches.single().expectedIndex)
        assertEquals(0, small.matches.single().expectedIndex)
        assertEquals(saved.key, small.eventFor(narrow.single())?.key)
        assertEquals(8000.toBigInteger(), forecastAccount(plan.conditions() as CalendarPlan, setOf("main"), listOf(opening, purchase, second),
            december.withDayOfMonth(1), december.withDayOfMonth(31), small.matches).closing)
    }

    @Test fun nameChangesKeepLinksButChangedConditionsAndOtherCalendarDoNotBorrowThem() {
        val saved = link()
        val renamed = plan.copy(sequence = 2, name = "Другое название", recordedAt = 2)
        val changed = renamed.copy(sequence = 3, payload = encodePlan(CalendarPlan(listOf(schedule.copy(amount = 2000)))))
        validate(listOf(saved), listOf(plan, renamed, changed))
        assertEquals(plan.conditionsHash(), renamed.conditionsHash())
        assertNotEquals(plan.conditionsHash(), changed.conditionsHash())
        assertEquals(1, resolveCalendarMatches(renamed, "main", expected(), listOf(purchase), listOf(saved)).matches.size)
        assertTrue(resolveCalendarMatches(changed, "main", expected(changed), listOf(purchase), listOf(saved)).active.isEmpty())
        assertTrue(resolveCalendarMatches(plan.copy(id = "other-calendar"), "main", expected(), listOf(purchase), listOf(saved)).active.isEmpty())
    }

    @Test fun changedOrCancelledSourceIsStaleEvenOutsideViewedPeriod() {
        val saved = link()
        val changed = purchase.copy(description = "Исправленная покупка")
        val result = resolveCalendarMatches(plan, "main", expected(), listOf(changed), listOf(saved))
        assertEquals(listOf(saved), result.stale)
        assertTrue(result.matches.isEmpty())
        val later = resolveCalendarMatches(plan, "main", expected(start = day.plusMonths(1), end = day.plusMonths(1)), emptyList(), listOf(saved))
        assertEquals(listOf(saved), later.stale)
        assertTrue(later.matches.isEmpty())
        val rawHistory = listOf(OperationHistory(listOf(Revision(purchase, null), Revision(changed, Instant.ofEpochMilli(1)), Revision(changed, Instant.ofEpochMilli(2), true))))
        validate(listOf(saved), source = rawHistory)
        assertEquals(8000.toBigInteger(), forecastAccount(plan.conditions() as CalendarPlan, setOf("main"), listOf(opening, changed), day, day, result.matches).closing)
    }

    @Test fun removalPreservesOldSourceAndExplicitRelinkUsesNewVersion() {
        val saved = link()
        val changed = purchase.copy(description = "Уточнение")
        val removed = saved.copy(sequence = 2, cancelled = true, previousSequence = 1, recordedAt = 2)
        val relinked = saved.copy(sequence = 3, operationVersion = changed.fingerprint(), previousSequence = 2, recordedAt = 3)
        val sources = listOf(OperationHistory(listOf(Revision(purchase, null), Revision(changed, Instant.ofEpochMilli(3)))))
        validate(listOf(saved, removed, relinked), source = sources)
        assertTrue(resolveCalendarMatches(plan, "main", expected(), listOf(changed), listOf(saved, removed)).active.isEmpty())
        val result = resolveCalendarMatches(plan, "main", expected(), listOf(changed), listOf(saved, removed, relinked))
        assertTrue(result.stale.isEmpty())
        assertEquals(changed.fingerprint(), result.matches.single().version)
        rejects { validate(listOf(saved, removed.copy(operationVersion = changed.fingerprint())), source = sources) }
        rejects { validate(listOf(saved, removed, removed.copy(sequence = 3, previousSequence = 2))) }
        rejects { validate(listOf(saved, saved.copy(sequence = 2, previousSequence = 1))) }
    }

    @Test fun oneOperationCannotCloseTwoExpectationsOfSameAccountAndConditions() {
        val multiple = plan.copy(payload = encodePlan(CalendarPlan(listOf(schedule, schedule.copy(name = "Второе ожидание")))))
        val rows = expected(multiple)
        val one = link(rows[0], event = multiple)
        val two = link(rows[1], event = multiple).copy(sequence = 2)
        rejects { validate(listOf(one, two), listOf(multiple)) }
        val removed = one.copy(sequence = 2, cancelled = true, previousSequence = 1)
        validate(listOf(one, removed, two.copy(sequence = 3)), listOf(multiple))
    }

    @Test fun transferSidesAndDifferentCalendarsRemainIndependent() {
        val transfer = Operation("transfer", OperationKind.Transfer, day, "main", 1000, "Себе", "other")
        val mixed = plan.copy(payload = encodePlan(CalendarPlan(listOf(schedule, schedule.copy(name = "Получение", income = true)))))
        val rows = expected(mixed)
        val outgoing = link(rows[0], transfer, mixed, "main")
        val incoming = link(rows[1], transfer, mixed, "other").copy(sequence = 2)
        validate(listOf(outgoing, incoming), listOf(mixed), history(transfer))
        val secondPlan = plan.copy(sequence = 2, id = "separate")
        validate(listOf(link(), link(event = secondPlan).copy(sequence = 2)), listOf(plan, secondPlan))
        assertEquals(1, resolveCalendarMatches(mixed, "other", rows, listOf(transfer), listOf(outgoing, incoming)).matches.single().expectedIndex)
    }

    @Test fun malformedSourceKeyAndChainAreRejectedBeforeRestore() {
        val saved = link()
        for (bad in listOf(saved.copy(sequence = 0), saved.copy(calendarSequence = 9),
            saved.copy(operationVersion = "0".repeat(64)), saved.copy(operationId = "missing"),
            saved.copy(previousSequence = 1), saved.copy(cancelled = true), saved.copy(recordedAt = -1),
            saved.copy(key = saved.key.copy(conditionsHash = "f".repeat(64))),
            saved.copy(key = saved.key.copy(scheduleIndex = 999)),
            saved.copy(key = saved.key.copy(date = day.plusDays(1).toString())),
            saved.copy(key = saved.key.copy(accountId = "other")))) {
            rejects { validate(listOf(bad)) }
        }
        rejects { validate(listOf(saved, saved)) }
        rejects { validate(listOf(saved), source = history(purchase.copy(amountMinor = 2000))) }
        rejects { calendarPayment(plan, saved.key.copy(date = "2026-11-31")) }
        rejects { calendarPayment(plan, saved.key.copy(date = "+12026-11-15")) }
    }

    @Test fun openingBalanceIsNeverAConductedPayment() {
        val receipt = link(operation = opening.copy(date = day, amountMinor = -1000))
        rejects { validate(listOf(receipt), source = history(opening.copy(date = day, amountMinor = -1000))) }
    }

    @Test fun lastScheduleOfMaximumCalendarKeepsItsOwnStableIndex() {
        val many = plan.copy(payload = encodePlan(CalendarPlan(List(1000) { schedule.copy(name = "Платёж " + it) })))
        val rows = expected(many)
        assertEquals(1000, rows.size)
        val saved = link(rows.last(), event = many)
        assertEquals(999, saved.key.scheduleIndex)
        assertEquals(rows.last(), calendarPayment(many, saved.key))
        validate(listOf(saved), listOf(many))
        val linked = resolveCalendarMatches(many, "main", rows, listOf(purchase), listOf(saved))
        assertEquals(999, linked.matches.single().expectedIndex)
    }

    @Test fun encryptedCopyKeepsAllSourceVersionsAndMatchingHistory() {
        val saved = link()
        val changed = purchase.copy(description = "Исправление")
        val removed = saved.copy(sequence = 2, previousSequence = 1, cancelled = true, recordedAt = 2)
        val fresh = saved.copy(sequence = 3, previousSequence = 2, operationVersion = changed.fingerprint(), recordedAt = 3)
        val data = BackupLedger("Синтетика", accounts, listOf(opening, purchase),
            listOf(Revision(changed, Instant.ofEpochMilli(1))), planningEvents = listOf(plan), calendarMatchEvents = listOf(saved, removed, fresh))
        val bytes = PortableBackup.seal(data, "copy-only".toCharArray())
        assertEquals(14, bytes["CROWN-ANDROID-BACKUP".length].toInt())
        val restored = PortableBackup.open(bytes.inputStream(), "copy-only".toCharArray())
        assertEquals(data, restored)
        assertEquals(3, restored.calendarMatchEvents.size)
        rejects { data.copy(planningEvents = emptyList()).validate() }
        rejects { data.copy(calendarMatchEvents = listOf(fresh)).validate() }
    }
}
