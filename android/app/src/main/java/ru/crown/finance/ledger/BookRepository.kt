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
    entities = [BookRow::class, AccountRow::class, OperationRow::class, RevisionRow::class, CategoryRow::class],
    version = 3,
    exportSchema = true,
)
internal abstract class BookDatabase : RoomDatabase() {
    abstract fun identity(): BookIdentityDao
    abstract fun ledger(): LedgerDao
    abstract fun revisions(): RevisionDao
    abstract fun categories(): CategoryDao
}

internal class BookRepository(context: Context) {
    private val app = context.applicationContext
    private val path = File(app.noBackupFilesDir, "mobile-book-v1")
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
        database.ledger().insertAccounts(backup.accounts.mapIndexed { index, account ->
            AccountRow(account.id, account.name, index)
        })
        for (category in backup.categories) database.categories().insert(category.toRow())
        database.ledger().insertOperations(backup.operations.map(Operation::toRow))
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
                .addMigrations(historyMigration, categoriesMigration)
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

    suspend fun snapshot(month: YearMonth): LedgerSnapshot = db.withTransaction {
        val history = assembleHistory(
            dao.operations().map(OperationRow::toOperation),
            db.revisions().all().map(RevisionRow::toRevision),
        )
        val active = history.mapNotNull { item -> item.current.value.takeUnless { item.current.cancelled } }
        LedgerSnapshot.from(month, dao.accounts().map(AccountRow::toAccount), active).copy(
            history = history, categories = db.categories().all().map(CategoryRow::toCategory),
        )
    }

    suspend fun backup(password: CharArray): ByteArray = withContext(Dispatchers.IO) {
        val ledger = db.withTransaction {
            BackupLedger(
                identity.name, dao.accounts().map(AccountRow::toAccount),
                dao.operations().map(OperationRow::toOperation),
                db.revisions().all().map(RevisionRow::toRevision),
                db.categories().all().map(CategoryRow::toCategory),
            )
        }
        PortableBackup.seal(ledger, password)
    }

    suspend fun update(month: YearMonth, change: suspend () -> Unit): LedgerSnapshot = db.withTransaction {
        change()
        snapshot(month)
    }

    suspend fun addAccount(name: String, opening: Long, date: LocalDate) = db.withTransaction {
        val title = name.trim()
        require(opening != Long.MIN_VALUE) { "Начальный остаток превышает допустимый диапазон" }
        require(title.length in 1..100) { "Введите название счёта" }
        val accounts = dao.accounts()
        require(accounts.none { it.name.equals(title, ignoreCase = true) }) { "Счёт с таким названием уже есть" }
        val row = AccountRow(UUID.randomUUID().toString(), title, accounts.size)
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
        validateCategory(operation)
        dao.insertOperations(listOf(operation.toRow()))
    }

    suspend fun reviseOperation(id: String, replacement: Operation?) = db.withTransaction {
        val original = requireNotNull(dao.original(id)) { "Операция не найдена" }
        val revision = db.revisions().latest(id)
        require(revision?.cancelled != true) { "Операция уже отменена" }
        val current = revision?.value?.toOperation() ?: original.toOperation()
        val next = replacement ?: current
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
    }

    suspend fun addCategory(name: String, kind: OperationKind) = db.withTransaction {
        val title = name.trim()
        require(title.length in 1..80) { "Введите название категории" }
        require(db.categories().all().none { it.kind == kind.wireName && it.name.equals(title, ignoreCase = true) }) {
            "Категория с таким названием уже есть"
        }
        db.categories().insert(Category(UUID.randomUUID().toString(), title, kind).toRow())
    }

    private suspend fun validateCategory(operation: Operation) {
        val id = operation.categoryId ?: return
        require(db.categories().find(id)?.kind == operation.kind.wireName) {
            "Категория должна соответствовать виду операции"
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
