package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.YearMonth

class BonusesTest {
    private val food = Category("food", "Продукты", OperationKind.Expense)
    private val program = BonusProgramEvent(1, "one", "Магазин", false, 1)
    private fun entry(sequence: Long, id: String, kind: BonusKind, units: Long, date: String = "2026-11-15") =
        BonusEntryEvent(sequence, id, "one", date, kind.name, units, "food", null, "", false, sequence)

    @Test fun reportKeepsProgramsPeriodsAndMoneySeparate() {
        val state = BonusState(listOf(program, program.copy(sequence = 2, id = "two", name = "Другой")), listOf(
            entry(1, "open", BonusKind.Opening, 1000, "2026-10-01"),
            entry(2, "get", BonusKind.Accrual, 125),
            entry(3, "use", BonusKind.Spending, -50),
            entry(4, "expire", BonusKind.Expiry, -20),
            entry(5, "adjust", BonusKind.Adjustment, -5),
            entry(6, "future", BonusKind.Accrual, 10000, "2026-12-01"),
            entry(7, "another", BonusKind.Accrual, 999).copy(programId = "two"),
        ))
        state.validate(listOf(food), emptySet())
        val report = state.report("one", YearMonth.of(2026, 11))
        assertEquals(1050.toBigInteger(), report.balance)
        assertEquals(125.toBigInteger(), report.accrued)
        assertEquals(50.toBigInteger(), report.spent)
        assertEquals(20.toBigInteger(), report.expired)
        assertEquals((-5).toBigInteger(), report.adjustment)
        assertEquals(mapOf("food" to 125.toBigInteger()), report.categories)
    }

    @Test fun correctionAndCancellationPreserveFacts() {
        val first = entry(1, "one-entry", BonusKind.Accrual, 100)
        val changed = first.copy(sequence = 2, units = 250)
        val cancelled = changed.copy(sequence = 3, cancelled = true)
        val state = BonusState(listOf(program), listOf(first, changed, cancelled))
        state.validate(listOf(food), emptySet())
        assertEquals(0.toBigInteger(), state.report("one", YearMonth.of(2026, 11)).balance)
        assertThrows(IllegalArgumentException::class.java) { state.copy(entryEvents = listOf(first, cancelled)).validate(listOf(food), emptySet()) }
        assertThrows(IllegalArgumentException::class.java) { state.copy(entryEvents = state.entryEvents + changed.copy(sequence = 4)).validate(listOf(food), emptySet()) }
    }

    @Test fun signsLinksAndBoundsAreChecked() {
        for (kind in BonusKind.entries) {
            val sign = if (kind == BonusKind.Spending || kind == BonusKind.Expiry) -1 else 1
            entry(1, "row", kind, sign.toLong()).validate()
            if (kind != BonusKind.Adjustment) assertThrows(IllegalArgumentException::class.java) {
                entry(1, "row", kind, -sign.toLong()).validate()
            }
        }
        val valid = BonusState(listOf(program), listOf(entry(1, "row", BonusKind.Accrual, 1)))
        for (invalid in listOf(0L, Long.MIN_VALUE)) assertThrows(IllegalArgumentException::class.java) {
            entry(1, "row", BonusKind.Adjustment, invalid).validate()
        }
        assertThrows(IllegalArgumentException::class.java) { valid.copy(entryEvents = valid.entryEvents.map { it.copy(operationId = "absent") }).validate(listOf(food), emptySet()) }
        assertThrows(IllegalArgumentException::class.java) { valid.validate(listOf(food.copy(kind = OperationKind.Income)), emptySet()) }
    }

    @Test fun totalsDoNotOverflowIndividualAmountRange() {
        val state = BonusState(listOf(program), listOf(
            entry(1, "first", BonusKind.Accrual, Long.MAX_VALUE),
            entry(2, "second", BonusKind.Accrual, Long.MAX_VALUE),
        ))
        assertEquals(Long.MAX_VALUE.toBigInteger() * 2.toBigInteger(), state.report("one", YearMonth.of(2026, 11)).balance)
    }

    @Test fun encryptedBackupRetainsAllVersionsAndReferences() {
        val first = entry(1, "entry", BonusKind.Accrual, 125).copy(operationId = "buy")
        val bonus = BonusState(listOf(program, program.copy(sequence = 2, archived = true)), listOf(first, first.copy(sequence = 2, cancelled = true)))
        val operation = Operation("buy", OperationKind.Expense, java.time.LocalDate.of(2026, 11, 15), "account", 100, "Покупка")
        val ledger = BackupLedger("Синтетика", listOf(Account("account", "Основной")), listOf(operation), emptyList(), listOf(food), bonuses = bonus)
        val bytes = PortableBackup.seal(ledger, "synthetic-only".toCharArray())
        assertEquals(ledger, PortableBackup.open(bytes.inputStream(), "synthetic-only".toCharArray()))
    }
}
