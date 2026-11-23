package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.YearMonth

class CatalogHistoryTest {
    private val bank = Bank("bank", "Учебный банк")
    private val account = Account("main", "Основной", bank.id, AccountKind.DebitCard)
    private val other = Account("other", "Другой")
    private val food = Category("food", "Продукты", OperationKind.Expense)
    private val home = Category("home", "Дом", OperationKind.Expense)
    private val income = Category("salary", "Зарплата", OperationKind.Income)
    private val day = LocalDate.of(2026, 11, 15)
    private val purchase = Operation("buy", OperationKind.Expense, day, account.id, 1000, "Покупка", categoryId = food.id)
    private inner class Fixture {
        val banks = mutableListOf(bank)
        val accounts = mutableListOf(account, other)
        val categories = mutableListOf(food, home, income)
        val events = mutableListOf<CatalogEvent>()
        fun state() = CatalogState.from(accounts, categories, banks, events)
        fun change(value: CatalogValue): CatalogVersion {
            val state = state()
            val expected = state.get(value.type, value.id)
            state.check(value, expected)
            events += value.event(expected, 100 + events.size.toLong()).copy(sequence = events.size + 1L)
            return state().get(value.type, value.id)
        }
        fun create(value: CatalogValue) {
            state().check(value, null)
            when (value.type) {
                CatalogType.Bank -> banks += value.bank()
                CatalogType.Account -> accounts += value.account()
                CatalogType.Category -> categories += value.category()
            }
            events += value.event(null, 100 + events.size.toLong()).copy(sequence = events.size + 1L)
        }
        fun snapshot() = LedgerSnapshot.from(YearMonth.from(day), state().accounts, listOf(purchase))
            .copy(categories = state().categories, banks = state().banks, catalogHistory = state().history)
    }
    private fun rejects(block: () -> Unit) = assertThrows(IllegalArgumentException::class.java, block)

    @Test fun legacyEntitiesHaveExplicitUnknownDateAndStableIdentity() {
        val state = Fixture().state()
        assertEquals(6, state.history.size)
        assertTrue(state.history.all { it.sequence == 0L && it.recordedAt == 0L })
        assertEquals(account, state.accounts.first { it.id == account.id })
        assertEquals(bank, state.banks.single())
    }

    @Test fun renameRetainsOriginalFieldsAndAbaRejectsStaleVersion() {
        val f = Fixture()
        val original = f.state().get(CatalogType.Account, account.id)
        val changed = f.change(original.value.copy(name = "Переименованный", kind = AccountKind.Deposit.wireName))
        rejects { f.state().check(original.value.copy(name = "Устаревший"), original) }
        val again = f.change(original.value)
        assertEquals(original.value, again.value)
        assertNotEquals(original.sequence, again.sequence)
        rejects { f.state().check(original.value.copy(name = "Потеря изменения"), original) }
        assertEquals(listOf(original, changed, again), f.state().history.filter { it.value.id == account.id })
        assertEquals(account, f.accounts.first())
        rejects { f.state().check(again.value, again) }
    }

    @Test fun freedNamesCanBeReusedByNewEntitiesWithoutRewritingSeeds() {
        val f = Fixture()
        f.change(account.catalogValue().copy(name = "Новое имя"))
        f.create(Account("new", account.name).catalogValue())
        f.change(food.catalogValue().copy(name = "Питание"))
        f.create(Category("new-food", food.name, food.kind).catalogValue())
        f.change(bank.catalogValue().copy(name = "Другой банк"))
        f.create(Bank("new-bank", bank.name).catalogValue())
        assertEquals(listOf(account.name, account.name), f.accounts.filter { it.id != other.id }.map { it.name })
        assertEquals(setOf("Новое имя", "Основной", "Другой"), f.state().accounts.map { it.name }.toSet())
        assertEquals(3, f.state().history.count { it.sequence == 0L && it.value.type != CatalogType.Category })
        assertEquals(3, f.events.count { it.created })
    }

    @Test fun archiveDoesNotRemoveEntityOrCascadeAndActiveNamesStayUnique() {
        val f = Fixture()
        f.change(bank.catalogValue().copy(archived = true))
        f.change(food.catalogValue().copy(archived = true))
        f.create(Bank("replacement", bank.name).catalogValue())
        f.create(Category("replacement-food", food.name, food.kind).catalogValue())
        val state = f.state()
        assertTrue(state.archived(CatalogType.Bank, bank.id))
        assertFalse(state.archived(CatalogType.Account, account.id))
        assertEquals(4, state.categories.size)
        rejects { f.change(bank.catalogValue()) }
        rejects { f.change(food.catalogValue()) }
        f.change(account.catalogValue().copy(name = "С прежним банком"))
        rejects { f.create(Account("new", "Новый", bank.id).catalogValue()) }
        rejects { f.create(Bank("bad", bank.name.uppercase()).catalogValue()) }
    }

    @Test fun categoryParentChangesRejectCyclesDifferentKindsAndArchivedAssignments() {
        val f = Fixture()
        f.change(home.catalogValue().copy(parentId = food.id))
        rejects { f.change(food.catalogValue().copy(parentId = home.id)) }
        rejects { f.change(home.catalogValue().copy(parentId = income.id)) }
        rejects { f.change(food.catalogValue().copy(kind = OperationKind.Income.wireName)) }
        f.change(food.catalogValue().copy(archived = true))
        f.change(home.catalogValue().copy(parentId = food.id, name = "Дом и быт"))
        rejects { f.create(Category("child", "Новый потомок", food.kind, food.id).catalogValue()) }
        f.change(home.catalogValue().copy(name = "Дом и быт", parentId = null))
        assertNull(f.state().get(CatalogType.Category, home.id).value.parentId)
        assertTrue(f.state().archived(CatalogType.Category, food.id))
        f.create(Category("income-food", food.name, OperationKind.Income).catalogValue())
    }

    @Test fun creationEventsAreOrderedAgainstReferencesAndOriginalSeed() {
        val f = Fixture()
        f.create(Bank("new-bank", "Новый банк").catalogValue())
        f.create(Account("new", "Новый", "new-bank").catalogValue())
        assertEquals("new-bank", f.state().get(CatalogType.Account, "new").value.bankId)
        rejects { CatalogState.from(f.accounts, f.categories, f.banks, f.events.asReversed()) }
        rejects { CatalogState.from(f.accounts, f.categories, f.banks, f.events.mapIndexed { i, e -> if (i == 0) e.copy(name = "Подмена") else e }) }
        rejects { CatalogState.from(f.accounts, f.categories, f.banks, f.events + f.events.first().copy(sequence = 3)) }
        rejects { f.create(Account("missing", "Нет банка", "unknown").catalogValue()) }
    }

    @Test fun malformedChainsAndReferencesAreRejectedBeforeRestore() {
        val f = Fixture()
        f.change(bank.catalogValue().copy(name = "После изменения"))
        val event = f.events.single()
        for (bad in listOf(event.copy(sequence = 0), event.copy(previousSequence = 8),
            event.copy(created = true), event.copy(recordedAt = -1), event.copy(entityId = "missing"),
            event.copy(entityType = "unknown"), event.copy(name = " Неверное "))) {
            rejects { CatalogState.from(f.accounts, f.categories, f.banks, listOf(bad)) }
        }
        rejects { CatalogState.from(f.accounts + account, f.categories, f.banks, emptyList()) }
        rejects { f.change(account.catalogValue().copy(parentId = food.id)) }
        rejects { f.change(bank.catalogValue().copy(kind = AccountKind.DebitCard.wireName)) }
        rejects { f.change(home.catalogValue().copy(parentId = "unknown")) }
        rejects { f.create(Bank("archived", "Архив").catalogValue().copy(archived = true)) }
    }

    @Test fun archivedReferencesAreRetainedOnlyByThePriorOperationOrPurchase() {
        val f = Fixture()
        f.change(account.catalogValue().copy(archived = true))
        f.change(food.catalogValue().copy(archived = true))
        val state = f.state()
        rejects { state.financialReferences(purchase) }
        state.financialReferences(purchase.copy(description = "Исправление"), purchase)
        val refund = Operation("return", OperationKind.Refund, day, other.id, 200, "Возврат", categoryId = food.id,
            refundOf = purchase.id, refundVersion = purchase.fingerprint())
        state.financialReferences(refund, purchase = purchase)
        val priorRefund = refund.copy(categoryId = null)
        state.financialReferences(refund.copy(accountId = account.id), priorRefund, purchase)
        rejects { state.financialReferences(refund.copy(accountId = "missing"), purchase = purchase) }
        rejects { state.financialReferences(purchase.copy(categoryId = income.id), purchase) }
        rejects { state.financialReferences(purchase.copy(accountId = other.id), priorRefund) }
        assertEquals(purchase, f.snapshot().operations.single())
        assertEquals((-1000).toBigInteger(), f.snapshot().totalMinor)
    }

    @Test fun activeSelectorsKeepHistoricalChoicesOnlyForCorrectionsAndIgnoreArchivedRules() {
        val f = Fixture()
        f.change(account.catalogValue().copy(archived = true))
        f.change(food.catalogValue().copy(archived = true))
        val snapshot = f.snapshot()
        assertEquals(listOf(other.id), snapshot.selectableAccounts().map { it.account.id })
        assertEquals(setOf(other.id, account.id), snapshot.selectableAccounts(purchase).map { it.account.id }.toSet())
        assertFalse(snapshot.selectableCategories().any { it.id == food.id })
        assertTrue(snapshot.selectableCategories(purchase).any { it.id == food.id })
        assertEquals("Продукты", snapshot.catalogVersion(CatalogType.Category, food.id).value.name)
        val archivedRule = CategoryRule("archived-rule", "Архивная категория", OperationKind.Expense, "магазин", food.id).event(1).copy(sequence = 1)
        val activeRule = CategoryRule("active-rule", "Действующая категория", OperationKind.Expense, "дом", home.id).event(2).copy(sequence = 2)
        assertEquals(listOf("active-rule"), snapshot.copy(ruleEvents = listOf(archivedRule, activeRule)).usableRules().map { it.id })
    }

    @Test fun encryptedBackupPreservesSeedsEventsArchivedNamesAndFinancialFingerprints() {
        val f = Fixture()
        f.change(account.catalogValue().copy(name = "Счёт после переименования", archived = true))
        f.create(Account("new", account.name).catalogValue())
        f.change(food.catalogValue().copy(archived = true))
        f.create(Category("new-food", food.name, food.kind).catalogValue())
        val data = BackupLedger("Синтетика", f.accounts, listOf(purchase), emptyList(), f.categories,
            banks = f.banks, catalogEvents = f.events)
        val encrypted = PortableBackup.seal(data, "copy-only".toCharArray())
        assertEquals(14, encrypted["CROWN-ANDROID-BACKUP".length].toInt())
        val restored = PortableBackup.open(encrypted.inputStream(), "copy-only".toCharArray())
        assertEquals(data, restored)
        val state = CatalogState.from(restored.accounts, restored.categories, restored.banks, restored.catalogEvents)
        assertEquals(f.state().history, state.history)
        assertEquals(purchase.fingerprint(), restored.operations.single().fingerprint())
        rejects { data.copy(catalogEvents = f.events.drop(1)).validate() }
    }
}
