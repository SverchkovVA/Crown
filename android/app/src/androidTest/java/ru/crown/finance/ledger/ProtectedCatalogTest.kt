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
class ProtectedCatalogTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val day = LocalDate.of(2026, 11, 15)
    private val month = YearMonth.from(day)

    @Test fun metadataChangesInClosedPeriodPreserveMoneyOriginalsAndProtectedRestore() = runBlocking {
        withBooks { first, second ->
            val (book, _) = BookRepository(context, first).create("Синтетика справочников", "test-only".toCharArray())
            var restored: BookSession? = null
            try {
                book.update(month) {
                    book.addBank("Учебный банк")
                    book.addAccount("Основной", 10000, day, book.snapshot(month).banks.single().id, AccountKind.DebitCard)
                    book.addCategory("Продукты", OperationKind.Expense)
                }
                val initial = book.snapshot(month)
                val account = initial.balances.single().account.id
                val category = initial.categories.single().id
                val purchase = Operation("buy", OperationKind.Expense, day, account, 345, "Учебная покупка", categoryId = category)
                book.update(month) { book.addOperation(purchase); book.setPeriodClosed(month, true, "") }
                val before = book.snapshot(month)
                for ((type, id, title) in listOf(
                    Triple(CatalogType.Account, account, "Счёт после изменения"),
                    Triple(CatalogType.Category, category, "Питание"),
                    Triple(CatalogType.Bank, initial.banks.single().id, "Банк после изменения"),
                )) {
                    val expected = book.snapshot(month).catalogVersion(type, id)
                    book.update(month) { book.saveCatalog(expected, expected.value.copy(name = title, archived = true)) }
                    val after = book.snapshot(month)
                    assertEquals(before.totalMinor, after.totalMinor)
                    assertEquals(before.operations, after.operations)
                    assertEquals(before.history, after.history)
                    assertEquals(before.periods, after.periods)
                    assertTrue(after.catalogArchived(type, id))
                }
                val encrypted = book.backup("copy-only".toCharArray())
                val raw = PortableBackup.open(encrypted.inputStream(), "copy-only".toCharArray())
                assertEquals("Основной", raw.accounts.single().name)
                assertEquals("Продукты", raw.categories.single().name)
                assertEquals("Учебный банк", raw.banks.single().name)
                assertEquals(6, raw.catalogEvents.size)
                val expected = book.snapshot(month)
                val (copy, _) = BookRepository(context, second).restore(
                    "restored-only".toCharArray(), "copy-only".toCharArray(), { encrypted.inputStream() })
                restored = copy
                assertEquals(expected, copy.snapshot(month))
                assertNotEquals(book.identity.id, copy.identity.id)
                copy.close()
                restored = null
                BookRepository(context, second).open("restored-only".toCharArray(), CredentialKind.Password).use {
                    assertEquals(expected, it.snapshot(month))
                }
                assertEquals(9655.toBigInteger(), expected.totalMinor)
                assertTrue(expected.selectableAccounts().isEmpty())
                assertTrue(expected.selectableCategories().isEmpty())
            } finally { restored?.close(); book.close() }
        }
    }

    @Test fun staleWritesNameReuseAndFailedCreationAreAtomic() = runBlocking {
        withBooks { first, _ ->
            val (book, _) = BookRepository(context, first).create("Синтетика", "test-only".toCharArray())
            try {
                book.update(month) { book.addBank("Банк"); book.addAccount("Счёт", 0, day) }
                val initial = book.snapshot(month)
                val id = initial.balances.single().account.id
                val old = initial.catalogVersion(CatalogType.Account, id)
                book.update(month) { book.saveCatalog(old, old.value.copy(name = "Изменённый")) }
                val changed = book.snapshot(month).catalogVersion(CatalogType.Account, id)
                book.update(month) { book.saveCatalog(changed, old.value) }
                val aba = book.snapshot(month)
                reject("изменился") { book.update(month) { book.saveCatalog(old, old.value.copy(name = "Устаревший")) } }
                assertEquals(aba, book.snapshot(month))
                val latest = aba.catalogVersion(CatalogType.Account, id)
                book.update(month) { book.saveCatalog(latest, latest.value.copy(archived = true)); book.addAccount("Счёт", 0, day) }
                val reused = book.snapshot(month)
                assertEquals(2, reused.balances.count { it.account.name == "Счёт" })
                val archived = reused.catalogVersion(CatalogType.Account, id)
                reject("название") { book.update(month) { book.saveCatalog(archived, archived.value.copy(archived = false)) } }
                val bank = reused.catalogVersion(CatalogType.Bank, reused.banks.single().id)
                book.update(month) { book.saveCatalog(bank, bank.value.copy(archived = true)) }
                val before = book.snapshot(month)
                reject("банк") { book.update(month) { book.addAccount("Не должен появиться", 500, day, bank.value.id) } }
                assertEquals(before, book.snapshot(month))
                val raw = PortableBackup.open(book.backup("copy-only".toCharArray()).inputStream(), "copy-only".toCharArray())
                assertEquals(2, raw.accounts.size)
                assertTrue(raw.operations.isEmpty())
                assertEquals(7, raw.catalogEvents.size)
            } finally { book.close() }
        }
    }

    @Test fun archivedReferencesAllowCorrectionsAndLinkedRefundButBlockNewAssignments() = runBlocking {
        withBooks { first, _ ->
            val (book, _) = BookRepository(context, first).create("Синтетика", "test-only".toCharArray())
            try {
                book.update(month) {
                    book.addAccount("Покупка", 10000, day); book.addAccount("Возврат", 0, day)
                    book.addCategory("Продукты", OperationKind.Expense); book.addCategory("Дом", OperationKind.Expense)
                }
                val initial = book.snapshot(month)
                val account = initial.balances.first().account.id
                val other = initial.balances.last().account.id
                val food = initial.categories.single { it.name == "Продукты" }.id
                val home = initial.categories.single { it.name == "Дом" }.id
                val purchase = Operation("buy", OperationKind.Expense, day, account, 1000, "Учебная покупка",
                    parts = listOf(Allocation(food, 600), Allocation(home, 400)))
                book.update(month) { book.addOperation(purchase) }
                for ((type, id) in listOf(CatalogType.Account to account, CatalogType.Category to food, CatalogType.Category to home)) {
                    val old = book.snapshot(month).catalogVersion(type, id)
                    book.update(month) { book.saveCatalog(old, old.value.copy(archived = true)) }
                }
                val before = book.snapshot(month)
                reject("архивный счёт") { book.update(month) { book.addOperation(purchase.copy(id = "new")) } }
                reject("архивную категорию") { book.update(month) { book.addOperation(purchase.copy(id = "new", accountId = other)) } }
                assertEquals(before, book.snapshot(month))
                val changed = purchase.copy(description = "Уточнённая покупка")
                book.update(month) { book.reviseOperation(purchase, changed) }
                val refund = Operation("refund", OperationKind.Refund, day, other, 200, "Возврат", categoryId = food,
                    refundOf = purchase.id, refundVersion = changed.fingerprint())
                book.update(month) { book.addOperation(refund) }
                val revised = refund.copy(accountId = account, categoryId = home)
                book.update(month) { book.reviseOperation(refund, revised) }
                assertEquals(9200.toBigInteger(), book.snapshot(month).totalMinor)
                assertEquals(listOf(purchase, changed), book.snapshot(month).history.single { it.id == purchase.id }.revisions.map { it.value })
                book.update(month) { book.reviseOperation(revised, null) }
                assertEquals(9000.toBigInteger(), book.snapshot(month).totalMinor)
                assertTrue(book.snapshot(month).catalogArchived(CatalogType.Account, account))
            } finally { book.close() }
        }
    }

    private suspend fun reject(part: String, action: suspend () -> Unit) {
        try { action(); fail("Ожидался отказ без изменения книги") }
        catch (error: IllegalArgumentException) { assertTrue(error.message.orEmpty(), error.message.orEmpty().contains(part, ignoreCase = true)) }
    }
    private suspend fun withBooks(action: suspend (File, File) -> Unit) {
        val directory = Files.createTempDirectory(context.noBackupFilesDir.toPath(), "catalog-instrumented-").toFile()
        try { action(File(directory, "first"), File(directory, "second")) }
        finally { check(directory.canonicalFile.parentFile == context.noBackupFilesDir.canonicalFile); directory.deleteRecursively() }
    }
}
