package ru.crown.finance.ledger

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test
import java.time.Instant
import java.time.LocalDate
import javax.crypto.AEADBadTagException

class PortableBackupTest {
    private val expense = Operation("op", OperationKind.Expense, LocalDate.of(2026, 10, 15), "one", 725, "Учебная покупка")
    private val correct = "УчебныйПароль"
    private val category = Category("food", "Учебная категория", OperationKind.Expense)
    private val changed = expense.copy(amountMinor = 995, categoryId = category.id)
    private val ledger = BackupLedger(
        "Учебная книга",
        listOf(Account("one", "Учебный счёт")),
        listOf(expense),
        listOf(
            Revision(changed, Instant.parse("2026-10-16T09:15:00Z")),
            Revision(changed, Instant.parse("2026-10-17T10:05:00Z"), cancelled = true),
        ),
        listOf(category),
    )

    @Test
    fun copyRetainsOriginalsAndCorrections() {
        val first = PortableBackup.seal(ledger, correct.toCharArray())
        val second = PortableBackup.seal(ledger, correct.toCharArray())
        assertFalse(first.contentEquals(second))
        assertFalse(first.toString(Charsets.UTF_8).contains(ledger.title))
        assertEquals(ledger, PortableBackup.open(first.inputStream(), correct.toCharArray()))
    }

    @Test
    fun splitAndLinkedRefundRoundTripWithImmutableSourceVersion() {
        val purchase = expense.copy(
            categoryId = null,
            parts = listOf(Allocation(category.id, 500), Allocation(null, 225)),
        )
        val returned = Operation(
            "return", OperationKind.Refund, purchase.date.plusDays(1), "one", 200, "Возврат",
            categoryId = category.id, refundOf = purchase.id, refundVersion = purchase.fingerprint(),
        )
        val data = ledger.copy(operations = listOf(purchase, returned), revisions = emptyList())
        val copy = PortableBackup.seal(data, correct.toCharArray())
        assertEquals(data, PortableBackup.open(copy.inputStream(), correct.toCharArray()))
    }

    @Test
    fun periodsAndReconciliationRemainPartOfProtectedCopy() {
        val checked = Reconciliation(
            "check", "one", "2026-10-18", 500, "510", "a".repeat(64), 17,
        )
        val data = ledger.copy(
            periods = listOf(PeriodEvent(1, "2026-10", true, "", 18)),
            reconciliations = listOf(checked),
        )
        val bytes = PortableBackup.seal(data, correct.toCharArray())
        assertEquals(data, PortableBackup.open(bytes.inputStream(), correct.toCharArray()))
    }

    @Test
    fun authenticationRejectsWrongPasswordAndAlteredBytes() {
        val copy = PortableBackup.seal(ledger, correct.toCharArray())
        assertThrows(AEADBadTagException::class.java) {
            PortableBackup.open(copy.inputStream(), "ОшибочныйПароль".toCharArray())
        }
        copy[copy.lastIndex] = (copy.last().toInt() xor 1).toByte()
        assertThrows(AEADBadTagException::class.java) {
            PortableBackup.open(copy.inputStream(), correct.toCharArray())
        }
    }

    @Test
    fun rejectsUnknownAccountsAndRevisionAfterCancellation() {
        assertThrows(IllegalArgumentException::class.java) {
            ledger.copy(accounts = emptyList()).validate()
        }
        assertThrows(IllegalArgumentException::class.java) {
            ledger.copy(categories = emptyList()).validate()
        }
        assertThrows(IllegalArgumentException::class.java) {
            ledger.copy(
                revisions = ledger.revisions + Revision(expense, Instant.parse("2026-10-18T11:00:00Z")),
            ).validate()
        }
    }
}
