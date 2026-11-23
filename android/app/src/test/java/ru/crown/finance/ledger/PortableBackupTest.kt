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
    fun banksKindsAndParentsRoundTripWithoutChangingFinancialAmounts() {
        val bank = Bank("bank", "Учебный банк")
        val parent = category.copy(id = "parent", name = "Родитель")
        val data = ledger.copy(
            accounts = listOf(ledger.accounts.single().copy(bankId = bank.id, kind = AccountKind.Deposit)),
            categories = listOf(parent, category.copy(parentId = parent.id)),
            banks = listOf(bank),
        )
        val bytes = PortableBackup.seal(data, correct.toCharArray())
        assertEquals(data, PortableBackup.open(bytes.inputStream(), correct.toCharArray()))
    }

    @Test
    fun organizationHistoryRoundTripsWithoutChangingFinancialFingerprints() {
        val tag = TagEvent(1, "tag", "Учебный тег", false, 1)
        val organization = OrganizationState(
            listOf(tag), listOf(TagAssignment(1, expense.id, tag.id, true, 2)),
            listOf(FilterEvent(1, "filter", "По тегу", "", null, null, "one", tag.id, false, 3)),
        )
        val data = ledger.copy(organization = organization)
        val copy = PortableBackup.open(PortableBackup.seal(data, correct.toCharArray()).inputStream(), correct.toCharArray())
        assertEquals(data, copy)
        assertEquals(expense.fingerprint(), copy.operations.single().fingerprint())
    }

    @Test
    fun stillReadsVersionsSixThroughTwelve() {
        for (version in 6..12) {
        val signature = "CROWN-ANDROID-BACKUP".toByteArray(Charsets.US_ASCII)
        val salt = ByteArray(16) { it.toByte() }
        val iv = ByteArray(12) { (it + 20).toByte() }
        val header = signature + byteArrayOf(version.toByte()) + salt + iv
        val body = java.io.ByteArrayOutputStream()
        java.io.DataOutputStream(body).use { out ->
            val name = "Прежняя копия".toByteArray()
            out.writeShort(name.size); out.write(name)
            repeat(if (version >= 11) 16 else if (version >= 10) 15 else if (version >= 9) 14 else if (version >= 8) 12 else if (version >= 7) 11 else 8) { out.writeInt(0) }
        }
        val key = derivePasswordKey(correct.toCharArray(), salt)
        val encrypted = try {
            javax.crypto.Cipher.getInstance("AES/GCM/NoPadding").run {
                init(javax.crypto.Cipher.ENCRYPT_MODE, javax.crypto.spec.SecretKeySpec(key, "AES"),
                    javax.crypto.spec.GCMParameterSpec(128, iv))
                updateAAD(header); doFinal(body.toByteArray())
            }
        } finally { key.fill(0) }
        val copy = PortableBackup.open((header + encrypted).inputStream(), correct.toCharArray())
        assertEquals("Прежняя копия", copy.title)
        assertEquals(OrganizationState(), copy.organization)
        assertEquals(emptyList<CategoryRuleEvent>(), copy.ruleEvents)
        }
    }

    @Test
    fun categoryRuleHistoryRoundTrips() {
        val rule = CategoryRule("rule", "Учебный магазин", OperationKind.Expense, "покупка", category.id)
        val events = listOf(rule.event(1).copy(sequence = 1), rule.copy(enabled = false).event(2).copy(sequence = 2))
        val data = ledger.copy(ruleEvents = events)
        val copy = PortableBackup.open(PortableBackup.seal(data, correct.toCharArray()).inputStream(), correct.toCharArray())
        assertEquals(data, copy)
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
