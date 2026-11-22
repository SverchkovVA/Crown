package ru.crown.finance.ledger

import android.content.Context
import androidx.room.Room
import androidx.room.withTransaction
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import net.zetetic.database.sqlcipher.SupportOpenHelperFactory
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.file.Files
import java.time.LocalDate
import java.time.YearMonth

@RunWith(AndroidJUnit4::class)
class ProtectedLedgerTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val day = LocalDate.of(2026, 11, 15)
    private val month = YearMonth.from(day)

    @Test
    fun rejectedRefundAndPurchaseChangeRollBackWithHistory() = runBlocking {
        withBooks { first, second ->
            val repository = BookRepository(context, first)
            val (book, _) = repository.create("Синтетическая книга", "test-only".toCharArray())
            var restored: BookSession? = null
            try {
                book.update(month) {
                    book.addAccount("Основной", 100_000, day)
                    book.addCategory("Продукты", OperationKind.Expense)
                    book.addCategory("Дом", OperationKind.Expense)
                }
                val initial = book.snapshot(month)
                val account = initial.balances.single().account.id
                val categories = initial.categories
                val purchase = Operation(
                    "synthetic-buy", OperationKind.Expense, day, account, 1_000, "Покупка",
                    parts = listOf(Allocation(categories[0].id, 600), Allocation(categories[1].id, 400)),
                )
                book.update(month) { book.addOperation(purchase) }
                val refund = Operation(
                    "synthetic-return", OperationKind.Refund, day.plusDays(1), account, 400, "Возврат",
                    categoryId = categories[0].id, refundOf = purchase.id, refundVersion = purchase.fingerprint(),
                )
                val accepted = book.update(month) { book.addOperation(refund) }
                assertEquals(99_400.toBigInteger(), accepted.totalMinor)
                reject { book.update(month) { book.addOperation(refund.copy(id = "over-limit", amountMinor = 201)) } }
                reject { book.update(month) { book.reviseOperation(purchase, null) } }
                val reduced = purchase.copy(
                    amountMinor = 700, parts = listOf(Allocation(categories[0].id, 300), Allocation(categories[1].id, 400)),
                )
                reject { book.update(month) { book.reviseOperation(purchase, reduced) } }
                assertEquals(accepted, book.snapshot(month))
                val changed = purchase.copy(description = "Уточнённая покупка")
                book.update(month) { book.reviseOperation(purchase, changed) }
                reject { book.update(month) { book.reviseOperation(purchase, purchase.copy(description = "Устаревший ввод")) } }
                val bytes = book.backup("copy-only".toCharArray())
                val (copy, _) = BookRepository(context, second).restore(
                    "restored-only".toCharArray(), "copy-only".toCharArray(), { bytes.inputStream() },
                )
                restored = copy
                val after = copy.snapshot(month)
                assertEquals(book.snapshot(month).operations, after.operations)
                assertEquals(book.snapshot(month).categories, after.categories)
                assertEquals(book.snapshot(month).history, after.history)
                assertEquals(99_400.toBigInteger(), after.totalMinor)
                val freed = book.update(month) { book.reviseOperation(refund, null) }
                assertEquals(99_000.toBigInteger(), freed.totalMinor)
            } finally {
                restored?.close()
                book.close()
            }
        }
    }

    @Test
    fun periodsBlockChangesAndReconciliationDoesNotInventMoney() = runBlocking {
        withBooks { first, second ->
            val (book, _) = BookRepository(context, first).create("Синтетика", "test-only".toCharArray())
            var restored: BookSession? = null
            try {
                book.update(month) { book.addAccount("Основной", 100_000, day) }
                val initial = book.snapshot(month)
                val accounts = initial.balances.map { it.account }
                val account = accounts.single().id
                val preview = previewReconciliation(initial.history, accounts, account, day)
                val purchase = Operation("buy", OperationKind.Expense, day.plusDays(1), account, 1_000, "Покупка")
                book.update(month) { book.addOperation(purchase) }
                book.update(month) { book.reconcile(preview, 99_995) }
                val checked = book.snapshot(month)
                assertEquals((-5).toBigInteger(), checked.reconciliations.single().difference)
                assertEquals(99_000.toBigInteger(), checked.totalMinor)
                val changed = purchase.copy(date = day)
                book.update(month) { book.reviseOperation(purchase, changed) }
                reject { book.update(month) { book.reconcile(preview, 100_000) } }
                book.update(month) { book.setPeriodClosed(month, true, "") }
                val locked = book.snapshot(month)
                reject { book.update(month) { book.addOperation(changed.copy(id = "extra")) } }
                reject { book.update(month) { book.reviseOperation(changed, changed.copy(date = day.plusMonths(1))) } }
                reject { book.update(month) { book.reviseOperation(changed, null) } }
                reject { book.update(month) { book.setPeriodClosed(month, false, " ") } }
                assertEquals(locked, book.snapshot(month))
                val returned = Operation(
                    "return", OperationKind.Refund, month.plusMonths(1).atDay(1), account, 100, "Возврат",
                    refundOf = changed.id, refundVersion = changed.fingerprint(),
                )
                book.update(month.plusMonths(1)) { book.addOperation(returned) }
                val copyBytes = book.backup("copy-only".toCharArray())
                val (copy, _) = BookRepository(context, second).restore(
                    "new-only".toCharArray(), "copy-only".toCharArray(), { copyBytes.inputStream() },
                )
                restored = copy
                assertEquals(book.snapshot(month).periods, copy.snapshot(month).periods)
                assertEquals(book.snapshot(month).reconciliations, copy.snapshot(month).reconciliations)
                reject { copy.update(month) { copy.addOperation(changed.copy(id = "locked-copy")) } }
                book.update(month) { book.setPeriodClosed(month, false, "Уточнение данных") }
                assertTrue(closedMonths(book.snapshot(month).periods).isEmpty())
            } finally {
                restored?.close()
                book.close()
            }
        }
    }

    @Test
    fun statementIsAtomicRepeatSafeAndLinksBothSidesOfTransfer() = runBlocking {
        withBooks { first, second ->
            val (book, _) = BookRepository(context, first).create("Синтетика", "test-only".toCharArray())
            var restored: BookSession? = null
            try {
                book.update(month) {
                    book.addAccount("Основной", 0, day)
                    book.addAccount("Резерв", 0, day)
                }
                val accounts = book.snapshot(month).balances.map { it.account }
                val main = accounts.first().id
                val reserve = accounts.last().id
                val document = parseStatement(
                    "date;amount;id\n2026-11-20;120.00;one\n2026-11-20;-200.00;two\n2026-11-31;2.00;bad"
                        .toByteArray(),
                )
                val choices = document.rows.map { row -> ImportChoice(row.position, row.proposal(main, document.key(main, row))) }
                val accepted = book.update(month) { book.importStatement(document, main, choices) }
                assertEquals((-8_000).toBigInteger(), accepted.totalMinor)
                assertEquals(2, accepted.statementReceipts.size)
                book.update(month) { book.importStatement(document, main, choices) }
                assertEquals(accepted, book.snapshot(month))
                val changed = parseStatement(
                    "date;amount;id\n2026-11-20;130.00;one".toByteArray(),
                )
                reject {
                    book.update(month) { book.importStatement(changed, main, changed.rows.map {
                        ImportChoice(it.position, it.proposal(main, changed.key(main, it)))
                    }) }
                }
                book.update(month) { book.setPeriodClosed(month, true, "") }
                val locked = book.snapshot(month)
                val mixed = parseStatement(
                    "date;amount;id\n2026-12-20;10.00;future\n2026-11-20;10.00;locked".toByteArray(),
                )
                reject {
                    book.update(month) { book.importStatement(mixed, main, mixed.rows.map {
                        ImportChoice(it.position, it.proposal(main, mixed.key(main, it)))
                    }) }
                }
                assertEquals(locked, book.snapshot(month))
                book.update(month) { book.setPeriodClosed(month, false, "Проверка связей перевода") }
                val transfer = Operation("transfer", OperationKind.Transfer, day, main, 2500, "Между счетами", reserve)
                book.update(month) { book.addOperation(transfer) }
                for ((account, amount) in listOf(main to "-25.00", reserve to "25.00")) {
                    val incoming = parseStatement(
                        ("date;amount;id\n2026-11-15;" + amount + ";transfer-side").toByteArray(),
                    )
                    val row = incoming.rows.single()
                    book.update(month) { book.importStatement(
                        incoming, account, listOf(ImportChoice(row.position, transfer, linkExisting = true)),
                    ) }
                }
                val linked = book.snapshot(month)
                assertEquals(4, linked.statementReceipts.size)
                assertEquals(3, linked.history.size)
                assertEquals((-8_000).toBigInteger(), linked.totalMinor)
                val bytes = book.backup("copy-only".toCharArray())
                val (copy, _) = BookRepository(context, second).restore(
                    "new-only".toCharArray(), "copy-only".toCharArray(), { bytes.inputStream() },
                )
                restored = copy
                assertEquals(linked.statementReceipts.toSet(), copy.snapshot(month).statementReceipts.toSet())
                copy.update(month) { copy.importStatement(document, main, choices) }
                assertEquals(linked.operations, copy.snapshot(month).operations)
            } finally {
                restored?.close()
                book.close()
            }
        }
    }

    @Test
    fun banksAccountKindsAndSubcategoriesSurviveProtectedRestore() = runBlocking {
        withBooks { first, second ->
            val (book, _) = BookRepository(context, first).create("Синтетика", "test-only".toCharArray())
            var restored: BookSession? = null
            try {
                assertTrue(book.snapshot(month).banks.isEmpty())
                book.update(month) {
                    book.addBank("Учебный банк")
                    book.addCategory("Продукты", OperationKind.Expense)
                }
                val catalog = book.snapshot(month)
                val bank = catalog.banks.single()
                val parent = catalog.categories.single()
                book.update(month) {
                    book.addCategory("Кафе", OperationKind.Expense, parent.id)
                    book.addAccount("Карта", 15_000, day, bank.id, AccountKind.DebitCard)
                }
                val actual = book.snapshot(month)
                reject { book.update(month) { book.addBank("учебный БАНК") } }
                reject { book.update(month) { book.addAccount("Ошибка", 100, day, "missing-bank") } }
                reject { book.update(month) { book.addCategory("Ошибка", OperationKind.Income, parent.id) } }
                assertEquals(actual, book.snapshot(month))
                val bytes = book.backup("copy-only".toCharArray())
                val (copy, _) = BookRepository(context, second).restore(
                    "new-only".toCharArray(), "copy-only".toCharArray(), { bytes.inputStream() },
                )
                restored = copy
                val after = copy.snapshot(month)
                assertEquals(actual.banks, after.banks)
                assertEquals(actual.categories, after.categories)
                assertEquals(actual.balances, after.balances)
                assertEquals(AccountKind.DebitCard, after.balances.single().account.kind)
                assertEquals(bank.id, after.balances.single().account.bankId)
                assertEquals(parent.id, after.categories.first { it.name == "Кафе" }.parentId)
            } finally {
                restored?.close()
                book.close()
            }
        }
    }

    @Test
    fun tagsAndSavedFiltersRestoreWithoutFinancialRevisions() = runBlocking {
        withBooks { first, second ->
            val (book, _) = BookRepository(context, first).create("Синтетика тегов", "test-only".toCharArray())
            var restored: BookSession? = null
            try {
                book.update(month) { book.addAccount("Основной", 0, day) }
                val account = book.snapshot(month).balances.single().account.id
                val purchase = Operation("tagged-purchase", OperationKind.Expense, day, account, 725, "Учебная покупка")
                book.update(month) { book.addOperation(purchase) }
                val before = book.snapshot(month)
                book.update(month) { book.saveTag("Поездка", null, false) }
                val tag = book.snapshot(month).organization.tags.single()
                book.update(month) { book.assignTags(purchase.id, emptySet(), setOf(tag.id)) }
                reject { book.update(month) { book.assignTags(purchase.id, emptySet(), emptySet()) } }
                reject { book.update(month) { book.assignTags(purchase.id, setOf(tag.id), setOf("foreign")) } }
                book.update(month) { book.saveFilter("Мои покупки", JournalQuery(accountId = account, tagId = tag.id), null, false) }
                val filter = book.snapshot(month).organization.filters.single()
                book.update(month) { book.setPeriodClosed(month, true, "") }
                book.update(month) { book.saveTag("Отпуск", tag, true) }
                reject { book.update(month) { book.saveTag("Устарело", tag, false) } }
                book.update(month) { book.saveFilter("Архив поездки", filter.query(), filter, true) }
                reject { book.update(month) { book.saveFilter("Устарело", filter.query(), filter, false) } }
                val tagged = book.snapshot(month)
                assertEquals(before.operations, tagged.operations)
                assertEquals(before.history, tagged.history)
                assertEquals(before.totalMinor, tagged.totalMinor)
                assertEquals(purchase.fingerprint(), tagged.operations.single().fingerprint())
                assertEquals(setOf(tag.id), tagged.organization.tagsByOperation()[purchase.id])
                val found = filter.query().apply(tagged.operations, tagged.balances.map { it.account }, tagged.categories, tagged.organization.tagsByOperation())
                assertEquals(listOf(purchase), found)
                val bytes = book.backup("copy-only".toCharArray())
                val (copy, _) = BookRepository(context, second).restore("restored-only".toCharArray(), "copy-only".toCharArray(), { bytes.inputStream() })
                restored = copy
                assertEquals(tagged.organization, copy.snapshot(month).organization)
                assertEquals(tagged.operations, copy.snapshot(month).operations)
                val current = copy.snapshot(month).organization.tags.single()
                copy.update(month) { copy.saveTag(current.name, current, false) }
                assertEquals(3, copy.snapshot(month).organization.tagEvents.size)
            } finally { restored?.close(); book.close() }
        }
    }

    @Test
    fun ruleVersionsRestoreWithoutReclassifyingExistingOperations() = runBlocking {
        withBooks { first, second ->
            val (book, _) = BookRepository(context, first).create("Синтетика правил", "test-only".toCharArray())
            var restored: BookSession? = null
            try {
                book.update(month) {
                    book.addAccount("Основной", 0, day)
                    book.addCategory("Продукты", OperationKind.Expense)
                }
                val initial = book.snapshot(month)
                val account = initial.balances.single().account.id
                val category = initial.categories.single()
                val purchase = Operation("rule-purchase", OperationKind.Expense, day, account, 725, "Магазин")
                book.update(month) { book.addOperation(purchase) }
                val before = book.snapshot(month)
                val rule = CategoryRule("shop-rule", "Магазин", OperationKind.Expense, "магазин", category.id, accountId = account)
                book.update(month) { book.saveRule(rule, null) }
                val firstVersion = book.snapshot(month).ruleEvents.single()
                book.update(month) { book.saveRule(rule.copy(priority = 20, enabled = false), firstVersion) }
                reject { book.update(month) { book.saveRule(rule.copy(priority = 30), firstVersion) } }
                reject { book.update(month) { book.saveRule(rule.copy(id = "foreign", categoryId = "missing"), null) } }
                val after = book.snapshot(month)
                assertEquals(before.operations, after.operations)
                assertEquals(before.history, after.history)
                assertEquals(before.totalMinor, after.totalMinor)
                val bytes = book.backup("copy-only".toCharArray())
                val (copy, _) = BookRepository(context, second).restore("restored-only".toCharArray(), "copy-only".toCharArray(), { bytes.inputStream() })
                restored = copy
                assertEquals(after.ruleEvents, copy.snapshot(month).ruleEvents)
                val expected = currentRules(copy.snapshot(month).ruleEvents).single()
                copy.update(month) { copy.saveRule(expected.rule().copy(enabled = true), expected) }
                assertEquals(3, copy.snapshot(month).ruleEvents.size)
            } finally { restored?.close(); book.close() }
        }
    }

    @Test
    fun bonusesStaySeparateAndSurviveRestoreWithStaleWriteProtection() = runBlocking {
        withBooks { first, second ->
            val (book, _) = BookRepository(context, first).create("Бонусы · синтетика", "test-only".toCharArray())
            var restored: BookSession? = null
            try {
                book.update(month) { book.addAccount("Основной", 10000, day); book.addCategory("Покупки", OperationKind.Expense); book.saveBonusProgram("Магазин", null, false) }
                val initial = book.snapshot(month)
                val program = initial.bonuses.programs.single()
                val entry = BonusEntryEvent(id = "bonus", programId = program.id, date = day.toString(), kind = BonusKind.Accrual.name,
                    units = 125, categoryId = initial.categories.single().id, operationId = null, note = "Учебная запись", cancelled = false, recordedAt = 0)
                book.update(month) { book.saveBonusEntry(entry, null) }
                val saved = book.snapshot(month).bonuses.entries.single()
                book.update(month) { book.saveBonusEntry(saved.copy(units = 250), saved) }
                val before = book.snapshot(month)
                reject { book.update(month) { book.saveBonusEntry(saved.copy(units = 999), saved) } }
                assertEquals(before, book.snapshot(month))
                book.update(month) { book.saveBonusProgram(program.name, program, true) }
                reject { book.update(month) { book.saveBonusEntry(entry.copy(id = "archived"), null) } }
                val bytes = book.backup("copy-only".toCharArray())
                val (copy, _) = BookRepository(context, second).restore("new-only".toCharArray(), "copy-only".toCharArray(), { bytes.inputStream() })
                restored = copy
                assertEquals(book.snapshot(month).bonuses, copy.snapshot(month).bonuses)
                assertEquals(initial.totalMinor, copy.snapshot(month).totalMinor)
                assertEquals(initial.incomeMinor, copy.snapshot(month).incomeMinor)
                val current = copy.snapshot(month).bonuses.entries.single()
                copy.update(month) { copy.saveBonusEntry(current.copy(cancelled = true), current) }
                assertEquals(0.toBigInteger(), copy.snapshot(month).bonuses.report(program.id, month).balance)
                reject { copy.update(month) { copy.saveBonusEntry(current.copy(units = 300), current) } }
            } finally { restored?.close(); book.close() }
        }
    }

    @Test
    fun passwordChangeKeepsDatabaseAndRecoveryAndRejectsWrongCredential() = runBlocking {
        withBooks { first, _ ->
            val repository = BookRepository(context, first)
            val (book, recovery) = repository.create("Доступ · синтетика", "old-only".toCharArray())
            book.update(month) { book.addAccount("Основной", 12345, day) }
            val expected = book.snapshot(month)
            val id = book.identity.id
            book.close()
            val keyFile = File(first, "key-envelope.json")
            val before = keyFile.readBytes()
            val database = File(first, "ledger.db").readBytes()
            try {
                repository.changePassword(id, "wrong-only".toCharArray(), CredentialKind.Password, "new-only".toCharArray())
                fail("Неверный пароль не должен менять доступ")
            } catch (_: javax.crypto.AEADBadTagException) { }
            assertArrayEquals(before, keyFile.readBytes())
            reject { repository.changePassword(id, "old-only".toCharArray(), CredentialKind.Password, charArrayOf()) }
            assertArrayEquals(before, keyFile.readBytes())
            repository.changePassword(id, "old-only".toCharArray(), CredentialKind.Password, "new-only".toCharArray())
            assertArrayEquals(database, File(first, "ledger.db").readBytes())
            assertFalse(before.contentEquals(keyFile.readBytes()))
            try {
                repository.open("old-only".toCharArray(), CredentialKind.Password).use { fail("Старый пароль должен быть отклонён") }
            } catch (_: javax.crypto.AEADBadTagException) { }
            repository.open("new-only".toCharArray(), CredentialKind.Password).use { assertEquals(expected, it.snapshot(month)) }
            repository.open(recovery.toCharArray(), CredentialKind.Recovery).use { assertEquals(expected, it.snapshot(month)) }
            repository.changePassword(id, recovery.toCharArray(), CredentialKind.Recovery, "final-only".toCharArray())
            repository.open("final-only".toCharArray(), CredentialKind.Password).use { assertEquals(expected, it.snapshot(month)) }
            repository.open(recovery.toCharArray(), CredentialKind.Recovery).use { assertEquals(expected, it.snapshot(month)) }
            assertFalse(first.listFiles()!!.any { it.name.startsWith("key-change-") })
        }
    }

    @Test
    fun everyPublishedSchemaMigratesInsideSqlCipher() = runBlocking {
        withBooks { directory, _ ->
            System.loadLibrary("sqlcipher")
            for (version in 1..9) {
                val path = File(directory.parentFile, "migration-$version.db")
                val key = ByteArray(32) { (it + version).toByte() }
                val schema = InstrumentationRegistry.getInstrumentation().context.assets
                    .open("ru.crown.finance.ledger.BookDatabase/$version.json").bufferedReader().use {
                        JSONObject(it.readText()).getJSONObject("database")
                    }
                val helper = SupportOpenHelperFactory(key).create(
                    SupportSQLiteOpenHelper.Configuration.builder(context).name(path.absolutePath)
                        .callback(object : SupportSQLiteOpenHelper.Callback(version) {
                            override fun onCreate(db: SupportSQLiteDatabase) {
                                val entities = schema.getJSONArray("entities")
                                for (index in 0 until entities.length()) {
                                    val entity = entities.getJSONObject(index)
                                    db.execSQL(entity.getString("createSql").replace("\${TABLE_NAME}", entity.getString("tableName")))
                                    val indices = entity.optJSONArray("indices") ?: org.json.JSONArray()
                                    for (i in 0 until indices.length()) {
                                        db.execSQL(indices.getJSONObject(i).getString("createSql").replace("\${TABLE_NAME}", entity.getString("tableName")))
                                    }
                                }
                                val setup = schema.getJSONArray("setupQueries")
                                for (index in 0 until setup.length()) db.execSQL(setup.getString(index))
                                db.execSQL("INSERT INTO book_identity(id,name) VALUES('migration','Синтетика')")
                                db.execSQL("INSERT INTO ledger_accounts(id,name,sortOrder) VALUES('main','Основной',0)")
                                db.execSQL("INSERT INTO ledger_operations(id,kind,date,accountId,amountMinor,description,destinationId,feeMinor) VALUES('opening','opening','2026-11-01','main',10000,'Начальный остаток',NULL,0)")
                                db.execSQL("INSERT INTO ledger_operations(id,kind,date,accountId,amountMinor,description,destinationId,feeMinor) VALUES('expense','expense','2026-11-15','main',345,'Учебная покупка',NULL,0)")
                                if (version >= 2) db.execSQL("INSERT INTO ledger_revisions(recordedAt,cancelled,value_id,value_kind,value_date,value_accountId,value_amountMinor,value_description,value_destinationId,value_feeMinor) VALUES(1,0,'expense','expense','2026-11-15','main',400,'Уточнённая покупка',NULL,0)")
                                if (version >= 3) {
                                    db.execSQL("INSERT INTO ledger_categories(id,name,kind) VALUES('food','Продукты','expense')")
                                    db.execSQL("UPDATE ledger_operations SET categoryId='food' WHERE id='expense'")
                                    db.execSQL("UPDATE ledger_revisions SET value_categoryId='food' WHERE value_id='expense'")
                                }
                            }
                            override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) = error("Test setup")
                        }).build(),
                )
                helper.writableDatabase
                helper.close()
                val room = Room.databaseBuilder(context, BookDatabase::class.java, path.absolutePath)
                    .openHelperFactory(SupportOpenHelperFactory(key))
                    .addMigrations(*bookMigrations)
                    .build()
                try {
                    assertEquals("migration", room.identity().entries().single().id)
                    assertEquals("main", room.ledger().accounts().single().id)
                    assertEquals(AccountKind.Other, room.ledger().accounts().single().toAccount().kind)
                    assertNull(room.ledger().accounts().single().bankId)
                    val originals = room.ledger().operations().map(OperationRow::toOperation)
                    assertEquals(345L, originals.first { it.id == "expense" }.amountMinor)
                    val revisions = room.revisions().all().map(RevisionRow::toRevision)
                    val history = assembleHistory(originals, revisions)
                    val current = history.map { it.current.value }
                    val snapshot = LedgerSnapshot.from(month, room.ledger().accounts().map(AccountRow::toAccount), current)
                    assertEquals((if (version >= 2) 9600 else 9655).toBigInteger(), snapshot.totalMinor)
                    assertEquals(if (version >= 2) 2 else 1, history.first { it.id == "expense" }.revisions.size)
                    assertEquals(if (version >= 3) "food" else null, current.first { it.id == "expense" }.categoryId)
                    assertEquals(if (version >= 3) 1 else 0, room.categories().all().size)
                    val header = ByteArray(16)
                    java.io.DataInputStream(path.inputStream()).use { it.readFully(header) }
                    assertFalse(header.contentEquals("SQLite format 3\u0000".toByteArray()))
                } finally {
                    room.close()
                    key.fill(0)
                }
            }
        }
    }

    private suspend fun reject(action: suspend () -> Unit) {
        try {
            action()
            fail("Ожидался отказ без изменения книги")
        } catch (_: IllegalArgumentException) {
            // Проверяем прикладной отказ и затем сравниваем полный снимок.
        }
    }

    private suspend fun withBooks(action: suspend (File, File) -> Unit) {
        val directory = Files.createTempDirectory(context.noBackupFilesDir.toPath(), "instrumented-").toFile()
        try {
            action(File(directory, "first"), File(directory, "second"))
        } finally {
            check(directory.canonicalFile.parentFile == context.noBackupFilesDir.canonicalFile)
            directory.deleteRecursively()
        }
    }
}
