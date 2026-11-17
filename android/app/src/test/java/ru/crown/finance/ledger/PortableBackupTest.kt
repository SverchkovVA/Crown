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
    private val changed = expense.copy(amountMinor = 995)
    private val ledger = BackupLedger(
        "Учебная книга",
        listOf(Account("one", "Учебный счёт")),
        listOf(expense),
        listOf(
            Revision(changed, Instant.parse("2026-10-16T09:15:00Z")),
            Revision(changed, Instant.parse("2026-10-17T10:05:00Z"), cancelled = true),
        ),
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
            ledger.copy(
                revisions = ledger.revisions + Revision(expense, Instant.parse("2026-10-18T11:00:00Z")),
            ).validate()
        }
    }
}
