package ru.crown.finance.ledger

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.withTransaction
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import net.zetetic.database.sqlcipher.SupportOpenHelperFactory
import java.io.File
import java.io.InputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.util.UUID

@Entity(tableName = "book_identity")
internal data class BookRow(@PrimaryKey val id: String, val name: String)

@Dao
internal interface BookIdentityDao {
    @Query("SELECT * FROM book_identity")
    suspend fun entries(): List<BookRow>

    @Insert
    suspend fun insert(book: BookRow)
}

@Database(
    entities = [
        BookRow::class, AccountRow::class, OperationRow::class, RevisionRow::class, CategoryRow::class,
        PeriodEvent::class, Reconciliation::class, StatementReceipt::class, Bank::class,
        TagEvent::class, TagAssignment::class, FilterEvent::class, CategoryRuleEvent::class, BonusProgramEvent::class, BonusEntryEvent::class,
    ],
    version = 10,
    exportSchema = true,
)
internal abstract class BookDatabase : RoomDatabase() {
    abstract fun identity(): BookIdentityDao
    abstract fun ledger(): LedgerDao
    abstract fun revisions(): RevisionDao
    abstract fun categories(): CategoryDao
    abstract fun control(): ControlDao
    abstract fun statements(): StatementDao
    abstract fun banks(): BankDao
    abstract fun organization(): OrganizationDao
    abstract fun classification(): ClassificationDao
    abstract fun bonuses(): BonusDao
}

internal val bookMigrations = arrayOf(historyMigration, categoriesMigration, detailsMigration, controlMigration, statementMigration, catalogsMigration, organizationMigration, classificationMigration, bonusMigration)

internal class BookRepository(
    context: Context,
    private val path: File = File(context.noBackupFilesDir, "mobile-book-v1"),
) {
    private val app = context.applicationContext
    private val keys = BookKeys()
    val exists get() = path.exists()

    suspend fun create(name: String, password: CharArray): Pair<BookSession, String> =
        create(name, password, null)

    suspend fun restore(
        newPassword: CharArray,
        backupPassword: CharArray,
        read: () -> InputStream,
    ): Pair<BookSession, String> {
        check(!exists)
        val backup = inspectBackup(backupPassword, read)
        return create(backup.title, newPassword, backup)
    }

    suspend fun inspectBackup(password: CharArray, read: () -> InputStream): BackupLedger =
        withContext(Dispatchers.IO) { read().use { PortableBackup.open(it, password) } }

    private suspend fun create(
        name: String,
        password: CharArray,
        backup: BackupLedger?,
    ): Pair<BookSession, String> = withContext(Dispatchers.IO) {
        check(!exists)
        require(name.trim().length in 1..100)
        val staging = Files.createTempDirectory(path.parentFile!!.toPath(), "book-stage-").toFile()
        try {
            val prepared = keys.create(password)
            try {
                publish(staging, name.trim(), prepared, backup)
                openDatabase(prepared.id, prepared.key) to prepared.recoveryCode
            } finally {
                prepared.key.fill(0)
            }
        } finally {
            if (staging.exists()) staging.deleteRecursively()
        }
    }

    private suspend fun publish(
        staging: File,
        title: String,
        prepared: BookKeys.Creation,
        backup: BackupLedger?,
    ) {
        val material = prepared.key.copyOf()
        val database = connect(File(staging, "ledger.db"), material)
        try {
            database.withTransaction {
                database.identity().insert(BookRow(prepared.id, title))
                if (backup != null) restoreLedger(database, backup)
            }
        } finally {
            try {
                database.close()
            } finally {
                material.fill(0)
            }
        }
        File(staging, "key-envelope.json").outputStream().use {
            it.write(prepared.metadata)
            it.fd.sync()
        }
        check(!exists)
        Files.move(staging.toPath(), path.toPath(), StandardCopyOption.ATOMIC_MOVE)
    }

    private suspend fun restoreLedger(database: BookDatabase, backup: BackupLedger) {
        for (bank in backup.banks) database.banks().insert(bank)
        for (event in backup.bonuses.programEvents) database.bonuses().insert(event)
        for (event in backup.bonuses.entryEvents) database.bonuses().insert(event)
        for (event in backup.ruleEvents) database.classification().insert(event)
        for (event in backup.organization.tagEvents) database.organization().insert(event)
        for (event in backup.organization.assignments) database.organization().insert(event)
        for (event in backup.organization.filterEvents) database.organization().insert(event)
        database.ledger().insertAccounts(backup.accounts.mapIndexed { index, account ->
            AccountRow(account.id, account.name, index, account.bankId, account.kind.wireName)
        })
        for (category in backup.categories) database.categories().insert(category.toRow())
        database.ledger().insertOperations(backup.operations.map(Operation::toRow))
        for (event in backup.periods) database.control().insert(event.copy(sequence = 0))
        for (result in backup.reconciliations) database.control().insert(result)
        for (receipt in backup.statementReceipts) database.statements().insert(receipt)
        for (revision in backup.revisions) {
            database.revisions().insert(
                RevisionRow(
                    recordedAt = requireNotNull(revision.recordedAt).toEpochMilli(),
                    cancelled = revision.cancelled,
                    value = revision.value.toRow(),
                ),
            )
        }
    }

    suspend fun open(credential: CharArray, kind: CredentialKind): BookSession = withContext(Dispatchers.IO) {
        val envelope = File(path, "key-envelope.json")
        check(envelope.isFile && envelope.length() in 1L..4096L)
        val unlocked = keys.unlock(envelope.readBytes(), credential, kind)
        try {
            openDatabase(unlocked.id, unlocked.key)
        } finally {
            unlocked.key.fill(0)
        }
    }

    private suspend fun openDatabase(id: String, input: ByteArray): BookSession {
        val file = File(path, "ledger.db")
        check(file.isFile)
        val material = input.copyOf()
        val db = connect(file, material)
        try {
            val identity = db.identity().entries().single()
            check(identity.id == id)
            return BookSession(identity, db, material)
        } catch (error: Exception) {
            db.close()
            material.fill(0)
            throw error
        }
    }

    private fun connect(file: File, material: ByteArray): BookDatabase {
        var database: BookDatabase? = null
        try {
            System.loadLibrary("sqlcipher")
            database = Room.databaseBuilder(app, BookDatabase::class.java, file.absolutePath)
                .openHelperFactory(SupportOpenHelperFactory(material))
                .addMigrations(*bookMigrations)
                .setJournalMode(RoomDatabase.JournalMode.TRUNCATE)
                .build()
            database.openHelper.writableDatabase.query("PRAGMA cipher_version").use { cursor ->
                check(cursor.moveToFirst() && !cursor.getString(0).isNullOrEmpty())
            }
            return database
        } catch (error: Throwable) {
            database?.close()
            material.fill(0)
            throw error
        }
    }
}

internal class BookSession(
    val identity: BookRow,
    private val db: BookDatabase,
    private val key: ByteArray,
) : AutoCloseable {
    private val dao get() = db.ledger()

    private suspend fun history() = assembleHistory(
        dao.operations().map(OperationRow::toOperation),
        db.revisions().all().map(RevisionRow::toRevision),
    )

    suspend fun snapshot(month: YearMonth): LedgerSnapshot = db.withTransaction {
        val history = history()
        val active = history.mapNotNull { item -> item.current.value.takeUnless { item.current.cancelled } }
        LedgerSnapshot.from(month, dao.accounts().map(AccountRow::toAccount), active).copy(
            history = history, categories = db.categories().all().map(CategoryRow::toCategory),
            periods = db.control().periods(), reconciliations = db.control().reconciliations(),
            statementReceipts = db.statements().all(), banks = db.banks().all(), organization = organization(), ruleEvents = db.classification().events(), bonuses = bonuses(),
        )
    }

    suspend fun backup(password: CharArray): ByteArray = withContext(Dispatchers.IO) {
        val ledger = db.withTransaction {
            BackupLedger(
                identity.name, dao.accounts().map(AccountRow::toAccount),
                dao.operations().map(OperationRow::toOperation),
                db.revisions().all().map(RevisionRow::toRevision),
                db.categories().all().map(CategoryRow::toCategory),
                db.control().periods(), db.control().reconciliations(), db.statements().all(), db.banks().all(), organization(), db.classification().events(), bonuses(),
            )
        }
        PortableBackup.seal(ledger, password)
    }

    suspend fun update(month: YearMonth, change: suspend () -> Unit): LedgerSnapshot = db.withTransaction {
        change()
        snapshot(month)
    }

    suspend fun addAccount(
        name: String, opening: Long, date: LocalDate,
        bankId: String? = null, kind: AccountKind = AccountKind.Other,
    ) = db.withTransaction {
        val title = name.trim()
        require(opening != Long.MIN_VALUE) { "Начальный остаток превышает допустимый диапазон" }
        require(title.length in 1..100) { "Введите название счёта" }
        if (opening != 0L) requireOpenDates(db.control().periods(), date)
        val accounts = dao.accounts()
        require(accounts.none { it.name.equals(title, ignoreCase = true) }) { "Счёт с таким названием уже есть" }
        require(bankId == null || db.banks().all().any { it.id == bankId }) { "Выберите банк книги" }
        val row = AccountRow(UUID.randomUUID().toString(), title, accounts.size, bankId, kind.wireName)
        dao.insertAccounts(listOf(row))
        if (opening != 0L) {
            dao.insertOperations(
                listOf(OperationRow(
                    UUID.randomUUID().toString(), OperationKind.Opening.wireName, date.toString(),
                    row.id, opening, "Начальный остаток", null, 0,
                )),
            )
        }
    }

    suspend fun addOperation(operation: Operation) = db.withTransaction {
        require(operation.kind != OperationKind.Opening)
        requireOpenDates(db.control().periods(), operation.date)
        validateCategory(operation)
        checkRefundSource(operation)
        dao.insertOperations(listOf(operation.toRow()))
        validateRefunds(history())
    }

    suspend fun reviseOperation(expected: Operation, replacement: Operation?) = db.withTransaction {
        val id = expected.id
        val original = requireNotNull(dao.original(id)) { "Операция не найдена" }
        val revision = db.revisions().latest(id)
        require(revision?.cancelled != true) { "Операция уже отменена" }
        val current = revision?.value?.toOperation() ?: original.toOperation()
        require(current == expected) { "Операция изменилась. Откройте её заново" }
        val next = replacement ?: current
        requireOpenDates(db.control().periods(), current.date, next.date)
        if (replacement != null) checkRefundSource(next)
        require(next.id == id && current.kind != OperationKind.Opening && next.kind != OperationKind.Opening)
        val ids = dao.accounts().map(AccountRow::id).toSet()
        require(next.accountId in ids && (next.destinationId == null || next.destinationId in ids)) {
            "Выберите счета из открытой книги"
        }
        validateCategory(next)
        require(replacement == null || replacement != current) { "Поля операции не изменились" }
        db.revisions().insert(
            RevisionRow(recordedAt = Instant.now().toEpochMilli(), cancelled = replacement == null, value = next.toRow()),
        )
        validateRefunds(history())
    }

    suspend fun importStatement(
        document: StatementDocument,
        accountId: String,
        choices: List<ImportChoice>,
    ) = db.withTransaction {
        require(document.rows.size <= MAX_STATEMENT_ROWS && choices.isNotEmpty())
        require(choices.map(ImportChoice::position).distinct().size == choices.size)
        val snapshot = snapshot(YearMonth.now())
        val candidates = previewStatement(document, snapshot, accountId).associateBy { it.row.position }
        for (choice in choices) {
            val candidate = requireNotNull(candidates[choice.position]) { "Строка выписки не найдена" }
            val row = candidate.row
            if (candidate.status == ImportStatus.Duplicate) continue
            require(candidate.status != ImportStatus.Conflict) { "Внешний ID уже записан с другими данными" }
            val operation = choice.operation
            if (choice.linkExisting) {
                require(candidate.matching.any { it == operation }) { "Выберите совпадающую текущую операцию" }
            } else {
                require(candidate.status != ImportStatus.PossibleDuplicate || choice.reviewed) {
                    "Для возможного повтора выберите существующую операцию или проверьте новую"
                }
                require(!row.needsReview || choice.reviewed || operation.kind == OperationKind.Transfer) {
                    "Проверьте вид перевода или возврата из выписки"
                }
                require(operation.kind != OperationKind.Opening && (operation.accountId == accountId || operation.destinationId == accountId))
                val movement = operation.movementFor(accountId)
                require((movement > 0) == (row.signedMinor > 0)) { "Движение по счёту не совпадает со знаком в выписке" }
                require(choice.reviewed || operation.matchesStatement(accountId, row)) {
                    "Исправленную строку нужно проверить перед записью"
                }
                addOperation(operation)
            }
            db.statements().insert(StatementReceipt(
                document.key(accountId, row), row.payloadHash, operation.id, operation.fingerprint(),
            ))
        }
    }

    suspend fun setPeriodClosed(month: YearMonth, closed: Boolean, reason: String) = db.withTransaction {
        require(month.year in 1900..9999)
        val events = db.control().periods()
        require((month in closedMonths(events)) != closed) { "Состояние периода уже изменилось" }
        require(reason.trim().length <= 200)
        require(closed || reason.isNotBlank()) { "Укажите причину открытия периода" }
        db.control().insert(PeriodEvent(
            month = month.toString(), closed = closed, reason = reason.trim(), recordedAt = Instant.now().toEpochMilli(),
        ))
    }

    suspend fun reconcile(preview: ReconciliationPreview, actualMinor: Long) = db.withTransaction {
        val current = previewReconciliation(history(), dao.accounts().map(AccountRow::toAccount), preview.accountId, preview.date)
        require(current == preview) { "Данные изменились. Выполните сверку заново" }
        require(actualMinor != Long.MIN_VALUE) { "Сумма превышает допустимый диапазон" }
        db.control().insert(Reconciliation(
            UUID.randomUUID().toString(), preview.accountId, preview.date.toString(), actualMinor,
            preview.calculatedMinor.toString(), preview.fingerprint, Instant.now().toEpochMilli(),
        ))
    }

    private suspend fun bonuses() = BonusState(db.bonuses().programs(), db.bonuses().entries())

    suspend fun saveBonusProgram(name: String, expected: BonusProgramEvent?, archived: Boolean) = db.withTransaction {
        val title = name.trim()
        require(title.length in 1..80) { "Введите название программы" }
        val current = bonuses().programs
        require(expected == null || current.firstOrNull { it.id == expected.id } == expected) { "Программа изменилась. Откройте её заново" }
        require(expected != null || (!archived && current.size < 1000))
        require(current.none { it.id != expected?.id && it.name.equals(title, true) }) { "Такая программа уже есть" }
        require(expected == null || title != expected.name || archived != expected.archived) { "Программа не изменилась" }
        db.bonuses().insert(BonusProgramEvent(id = expected?.id ?: UUID.randomUUID().toString(), name = title, archived = archived, recordedAt = Instant.now().toEpochMilli()))
    }

    suspend fun saveBonusEntry(value: BonusEntryEvent, expected: BonusEntryEvent?) = db.withTransaction {
        val state = bonuses()
        val previous = state.entries.firstOrNull { it.id == value.id }
        require(previous == expected) { "Запись изменилась. Откройте её заново" }
        require(previous?.cancelled != true) { "Запись уже отменена" }
        val event = value.copy(sequence = 0, recordedAt = Instant.now().toEpochMilli())
        event.validate()
        val program = requireNotNull(state.programs.firstOrNull { it.id == value.programId }) { "Программа не найдена" }
        require(!program.archived || (event.cancelled && previous?.programId == event.programId)) { "Программа в архиве" }
        if (event.cancelled) require(previous != null && event.sameFact(previous)) { "При отмене нельзя менять запись" }
        else require(previous == null || !event.sameFact(previous)) { "Запись не изменилась" }
        val validated = state.copy(entryEvents = state.entryEvents + event.copy(sequence = (state.entryEvents.lastOrNull()?.sequence ?: 0) + 1))
        validated.validate(db.categories().all().map(CategoryRow::toCategory), dao.operations().map(OperationRow::id).toSet())
        db.bonuses().insert(event)
    }

    suspend fun saveRule(rule: CategoryRule, expected: CategoryRuleEvent?) = db.withTransaction {
        val events = db.classification().events()
        val current = currentRules(events)
        val previous = current.firstOrNull { it.id == rule.id }
        require(previous == expected) { "Правило изменилось. Откройте его заново" }
        require(expected != null || current.size < 1000) { "В книге уже 1000 правил" }
        require(current.none { it.id != rule.id && it.name.equals(rule.name, true) }) { "Такое правило уже есть" }
        require(expected == null || expected.rule() != rule) { "Правило не изменилось" }
        require(db.categories().find(rule.categoryId)?.kind == rule.kind.wireName) { "Выберите категорию того же вида" }
        require(rule.accountId == null || dao.accounts().any { it.id == rule.accountId }) { "Счёт правила не найден" }
        db.classification().insert(rule.event(Instant.now().toEpochMilli()))
    }

    private suspend fun organization() = OrganizationState(
        db.organization().tags(), db.organization().assignments(), db.organization().filters(),
    )

    suspend fun saveTag(name: String, expected: TagEvent?, archived: Boolean) = db.withTransaction {
        val title = name.trim()
        require(title.length in 1..80) { "Введите название тега" }
        val current = organization().tags
        require(expected == null || current.firstOrNull { it.id == expected.id } == expected) { "Тег изменился. Откройте его заново" }
        require(expected != null || !archived)
        require(archived || current.none { !it.archived && it.id != expected?.id && it.name.equals(title, true) }) { "Такой тег уже есть" }
        require(expected == null || expected.name != title || expected.archived != archived) { "Тег не изменился" }
        db.organization().insert(TagEvent(
            id = expected?.id ?: UUID.randomUUID().toString(), name = title, archived = archived,
            recordedAt = Instant.now().toEpochMilli(),
        ))
    }

    suspend fun assignTags(operationId: String, expected: Set<String>, selected: Set<String>) = db.withTransaction {
        val state = organization()
        require(history().any { it.id == operationId && !it.current.cancelled }) { "Операция не найдена или отменена" }
        val previous = state.tagsByOperation()[operationId].orEmpty()
        require(previous == expected) { "Теги изменились. Откройте операцию заново" }
        require(selected.size <= 100)
        val tags = state.tags.associateBy(TagEvent::id)
        require(selected.all { tags[it]?.let { tag -> !tag.archived || it in previous } == true }) { "Выберите действующие теги книги" }
        require(selected != previous) { "Теги не изменились" }
        val timestamp = Instant.now().toEpochMilli()
        for (id in (previous + selected).sorted()) {
            if ((id in previous) != (id in selected)) {
                db.organization().insert(TagAssignment(
                    operationId = operationId, tagId = id, present = id in selected, recordedAt = timestamp,
                ))
            }
        }
    }

    suspend fun saveFilter(name: String, query: JournalQuery, expected: FilterEvent?, archived: Boolean) = db.withTransaction {
        val title = name.trim()
        require(title.length in 1..80) { "Введите название фильтра" }
        val state = organization()
        require(expected == null || state.filters.firstOrNull { it.id == expected.id } == expected) { "Фильтр изменился. Откройте его заново" }
        require(expected != null || !archived)
        require(archived || state.filters.none { !it.archived && it.id != expected?.id && it.name.equals(title, true) }) { "Такой фильтр уже есть" }
        require(query.accountId == null || dao.accounts().any { it.id == query.accountId }) { "Счёт фильтра не найден" }
        require(query.categoryId == null || db.categories().find(query.categoryId) != null) { "Категория фильтра не найдена" }
        require(query.tagId == null || state.tags.any { it.id == query.tagId }) { "Тег фильтра не найден" }
        require(expected == null || expected.name != title || expected.query() != query || expected.archived != archived) { "Фильтр не изменился" }
        db.organization().insert(FilterEvent(
            id = expected?.id ?: UUID.randomUUID().toString(), name = title, textQuery = query.text,
            kind = query.kind?.wireName, categoryId = query.categoryId, accountId = query.accountId, tagId = query.tagId,
            archived = archived, recordedAt = Instant.now().toEpochMilli(),
        ))
    }

    suspend fun addBank(name: String) = db.withTransaction {
        val title = name.trim()
        require(title.length in 1..100) { "Введите название банка" }
        require(db.banks().all().none { it.name.equals(title, ignoreCase = true) }) { "Такой банк уже есть" }
        db.banks().insert(Bank(UUID.randomUUID().toString(), title))
    }

    suspend fun addCategory(name: String, kind: OperationKind, parentId: String? = null) = db.withTransaction {
        val title = name.trim()
        require(title.length in 1..80) { "Введите название категории" }
        require(db.categories().all().none { it.kind == kind.wireName && it.name.equals(title, ignoreCase = true) }) {
            "Категория с таким названием уже есть"
        }
        require(parentId == null || db.categories().find(parentId)?.kind == kind.wireName) {
            "Выберите родительскую категорию того же вида"
        }
        db.categories().insert(Category(UUID.randomUUID().toString(), title, kind, parentId).toRow())
    }

    private suspend fun checkRefundSource(operation: Operation) {
        if (operation.kind != OperationKind.Refund) return
        val source = history().firstOrNull { it.id == operation.refundOf }?.current
        require(source != null && !source.cancelled && source.value.kind == OperationKind.Expense &&
            source.value.fingerprint() == operation.refundVersion) { "Покупка изменилась. Оформите возврат заново" }
    }

    private suspend fun validateCategory(operation: Operation) {
        val expectedKind = if (operation.kind == OperationKind.Refund) OperationKind.Expense else operation.kind
        val selected = operation.parts.map(Allocation::categoryId) + operation.categoryId
        for (id in selected.filterNotNull()) {
            require(db.categories().find(id)?.kind == expectedKind.wireName) {
                "Категория должна соответствовать виду операции"
            }
        }
    }

    override fun close() {
        try {
            db.close()
        } finally {
            key.fill(0)
        }
    }
}
