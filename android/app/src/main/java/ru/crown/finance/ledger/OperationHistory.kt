package ru.crown.finance.ledger

import androidx.room.Dao
import androidx.room.Embedded
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.time.Instant

internal data class Revision(
    val value: Operation,
    val recordedAt: Instant?,
    val cancelled: Boolean = false,
)

internal data class OperationHistory(val revisions: List<Revision>) {
    val current get() = revisions.last()
    val id get() = current.value.id
}

@Entity(
    tableName = "ledger_revisions",
    foreignKeys = [ForeignKey(entity = OperationRow::class, parentColumns = ["id"], childColumns = ["value_id"])],
    indices = [Index("value_id")],
)
internal data class RevisionRow(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val recordedAt: Long,
    val cancelled: Boolean,
    @Embedded(prefix = "value_") val value: OperationRow,
) {
    fun toRevision() = Revision(value.toOperation(), Instant.ofEpochMilli(recordedAt), cancelled)
}

@Dao
internal interface RevisionDao {
    @Query("SELECT * FROM ledger_revisions ORDER BY sequence")
    suspend fun all(): List<RevisionRow>

    @Query("SELECT * FROM ledger_revisions WHERE value_id = :id ORDER BY sequence DESC LIMIT 1")
    suspend fun latest(id: String): RevisionRow?

    @Insert
    suspend fun insert(revision: RevisionRow)
}

internal fun assembleHistory(
    originals: List<Operation>,
    changes: List<Revision>,
): List<OperationHistory> {
    val byId = changes.groupBy { it.value.id }
    return originals.map { original ->
        OperationHistory(listOf(Revision(original, null)) + byId[original.id].orEmpty())
    }
}

internal val historyMigration = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE ledger_revisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                recordedAt INTEGER NOT NULL,
                cancelled INTEGER NOT NULL,
                value_id TEXT NOT NULL,
                value_kind TEXT NOT NULL,
                value_date TEXT NOT NULL,
                value_accountId TEXT NOT NULL,
                value_amountMinor INTEGER NOT NULL,
                value_description TEXT NOT NULL,
                value_destinationId TEXT,
                value_feeMinor INTEGER NOT NULL,
                FOREIGN KEY(value_id) REFERENCES ledger_operations(id) ON UPDATE NO ACTION ON DELETE NO ACTION
            )
            """.trimIndent(),
        )
        db.execSQL("CREATE INDEX index_ledger_revisions_value_id ON ledger_revisions (value_id)")
    }
}
