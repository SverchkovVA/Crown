package ru.crown.finance.ledger

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.file.Files
import java.time.LocalDate
import java.time.YearMonth

@RunWith(AndroidJUnit4::class)
class ProtectedCalendarMatchesTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val day = LocalDate.of(2026, 11, 15)
    private val month = YearMonth.from(day)

    @Test fun savedMatchesReopenRenameBecomeStaleAndRestoreWithoutChangingMoney() = runBlocking {
        withBooks { first, second ->
            var book = BookRepository(context, first).create("Синтетика связей", "test-only".toCharArray()).first
            var copy: BookSession? = null
            try {
                book.update(month) { book.addAccount("Основной", 10000, day) }
                val account = book.snapshot(month).balances.single().account.id
                val purchase = Operation("buy", OperationKind.Expense, day, account, 345, "Учебная покупка")
                val conditions = CalendarPlan(listOf(PaymentSchedule("Учебный платёж", 345, false, day, RepeatUnit.Months)))
                book.update(month) { book.addOperation(purchase); book.savePlan("calendar", "Календарь", conditions, null, false) }
                val plan = book.snapshot(month).planningEvents.single()
                val expected = forecastPayments(conditions, day, day)
                val key = plan.paymentKey(account, expected.single())
                book.update(month) { book.saveCalendarMatch(plan, key, null, purchase); book.setPeriodClosed(month, true, "") }
                val initial = book.snapshot(month)
                assertEquals(9655.toBigInteger(), initial.totalMinor)
                assertEquals(1, initial.calendarMatchEvents.size)
                val paid = resolveCalendarMatches(plan, account, expected, initial.currentOperations(), initial.calendarMatchEvents)
                assertEquals(9655.toBigInteger(), forecastAccount(conditions, setOf(account), initial.currentOperations(), day, day, paid.matches).closing)
                book.close()
                book = BookRepository(context, first).open("test-only".toCharArray(), CredentialKind.Password)
                assertEquals(initial, book.snapshot(month))
                book.update(month) { book.savePlan(plan.id, "Переименованный календарь", conditions, plan, false) }
                val renamed = currentPlans(book.snapshot(month).planningEvents).single()
                val afterName = book.snapshot(month)
                assertEquals(initial.history, afterName.history)
                assertEquals(initial.totalMinor, afterName.totalMinor)
                assertEquals(1, resolveCalendarMatches(renamed, account, expected, afterName.currentOperations(), afterName.calendarMatchEvents).matches.size)
                reject("Календарь изменился") { book.update(month) { book.saveCalendarMatch(plan, key, initial.calendarMatchEvents.single(), purchase) } }
                assertEquals(afterName, book.snapshot(month))
                book.update(month) { book.setPeriodClosed(month, false, "Учебная сверка") }
                val changed = purchase.copy(description = "Исправленная покупка")
                book.update(month) { book.reviseOperation(purchase, changed) }
                val stale = book.snapshot(month)
                assertEquals(1, resolveCalendarMatches(renamed, account, expected, stale.currentOperations(), stale.calendarMatchEvents).stale.size)
                assertEquals(9310.toBigInteger(), forecastAccount(conditions, setOf(account), stale.currentOperations(), day, day).closing)
                book.update(month) { book.saveCalendarMatch(renamed, key, stale.calendarMatchEvents.single(), changed) }
                val updated = book.snapshot(month)
                assertEquals(stale.history, updated.history)
                assertEquals(stale.totalMinor, updated.totalMinor)
                assertEquals(2, updated.calendarMatchEvents.size)
                assertEquals(1, resolveCalendarMatches(renamed, account, expected, updated.currentOperations(), updated.calendarMatchEvents).matches.size)
                val bytes = book.backup("copy-only".toCharArray())
                val data = PortableBackup.open(bytes.inputStream(), "copy-only".toCharArray())
                assertEquals(2, data.calendarMatchEvents.size)
                val (restored, _) = BookRepository(context, second).restore("restored-only".toCharArray(), "copy-only".toCharArray(), { bytes.inputStream() })
                copy = restored
                assertEquals(updated, restored.snapshot(month))
                assertNotEquals(book.identity.id, restored.identity.id)
                restored.close()
                copy = null
                BookRepository(context, second).open("restored-only".toCharArray(), CredentialKind.Password).use {
                    assertEquals(updated, it.snapshot(month))
                }
                val latest = updated.calendarMatchEvents.last()
                book.update(month) { book.saveCalendarMatch(renamed, key, latest, null) }
                val removed = book.snapshot(month)
                assertEquals(3, removed.calendarMatchEvents.size)
                assertTrue(removed.calendarMatchEvents.last().cancelled)
                assertEquals(latest.operationVersion, removed.calendarMatchEvents.last().operationVersion)
                assertEquals(updated.history, removed.history)
                assertEquals(updated.totalMinor, removed.totalMinor)
            } finally { copy?.close(); book.close() }
        }
    }

    @Test fun duplicateExpectationsStaleCommandsAndAbaAreAtomic() = runBlocking {
        withBooks { first, _ ->
            val (book, _) = BookRepository(context, first).create("Синтетика", "test-only".toCharArray())
            try {
                book.update(month) { book.addAccount("Основной", 10000, day) }
                val account = book.snapshot(month).balances.single().account.id
                val purchase = Operation("buy", OperationKind.Expense, day, account, 1000, "Учебная покупка")
                val conditions = CalendarPlan(listOf(PaymentSchedule("Один", 1000, false, day), PaymentSchedule("Два", 1000, false, day)))
                book.update(month) { book.addOperation(purchase); book.savePlan("calendar", "Календарь", conditions, null, false) }
                val plan = book.snapshot(month).planningEvents.single()
                val rows = forecastPayments(conditions, day, day)
                val key = plan.paymentKey(account, rows[0])
                book.update(month) { book.saveCalendarMatch(plan, key, null, purchase) }
                val original = book.snapshot(month)
                val firstLink = original.calendarMatchEvents.single()
                reject("уже сопоставлена") { book.update(month) { book.saveCalendarMatch(plan, plan.paymentKey(account, rows[1]), null, purchase) } }
                reject("Операция изменилась") { book.update(month) { book.saveCalendarMatch(plan, key, firstLink, purchase.copy(amountMinor = 2000)) } }
                assertEquals(original, book.snapshot(month))
                book.update(month) { book.saveCalendarMatch(plan, key, firstLink, null) }
                val removed = book.snapshot(month).calendarMatchEvents.last()
                book.update(month) { book.saveCalendarMatch(plan, key, removed, purchase) }
                val aba = book.snapshot(month)
                reject("Сопоставление изменилось") { book.update(month) { book.saveCalendarMatch(plan, key, firstLink, null) } }
                assertEquals(aba, book.snapshot(month))
                val revised = conditions.copy(schedules = conditions.schedules.map { it.copy(amount = 2000) })
                book.update(month) { book.savePlan(plan.id, "Новые условия", revised, plan, false) }
                val changed = book.snapshot(month)
                val newPlan = currentPlans(changed.planningEvents).single()
                assertTrue(resolveCalendarMatches(newPlan, account, forecastPayments(revised, day, day), changed.currentOperations(), changed.calendarMatchEvents).active.isEmpty())
                assertEquals(1, resolveCalendarMatches(plan, account, rows, changed.currentOperations(), changed.calendarMatchEvents).matches.size)
                reject("Календарь изменился") { book.update(month) { book.saveCalendarMatch(plan, key, aba.calendarMatchEvents.last(), purchase) } }
                assertEquals(changed, book.snapshot(month))
                book.update(month) { book.saveCalendarMatch(plan, key, aba.calendarMatchEvents.last(), null) }
                val final = book.snapshot(month)
                assertEquals(4, final.calendarMatchEvents.size)
                assertTrue(final.calendarMatchEvents.last().cancelled)
                assertEquals(original.history, final.history)
                assertEquals(original.totalMinor, final.totalMinor)
            } finally { book.close() }
        }
    }

    private suspend fun reject(part: String, action: suspend () -> Unit) {
        try { action(); fail("Ожидался отказ без изменения книги") }
        catch (error: IllegalArgumentException) { assertTrue(error.message.orEmpty(), error.message.orEmpty().contains(part, true)) }
    }
    private suspend fun withBooks(action: suspend (File, File) -> Unit) {
        val directory = Files.createTempDirectory(context.noBackupFilesDir.toPath(), "calendar-matches-").toFile()
        try { action(File(directory, "first"), File(directory, "second")) }
        finally { check(directory.canonicalFile.parentFile == context.noBackupFilesDir.canonicalFile); directory.deleteRecursively() }
    }
}
