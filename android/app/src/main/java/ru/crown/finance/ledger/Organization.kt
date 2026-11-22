package ru.crown.finance.ledger

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.io.DataInputStream
import java.io.DataOutputStream

@Entity(tableName = "tag_events")
internal data class TagEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val id: String, val name: String, val archived: Boolean, val recordedAt: Long,
)

@Entity(tableName = "tag_assignments")
internal data class TagAssignment(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val operationId: String, val tagId: String, val present: Boolean, val recordedAt: Long,
)

@Entity(tableName = "filter_events")
internal data class FilterEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val id: String, val name: String, val textQuery: String, val kind: String?,
    val categoryId: String?, val accountId: String?, val tagId: String?,
    val archived: Boolean, val recordedAt: Long,
) {
    fun query(): JournalQuery {
        val operationKind = kind?.let { raw ->
            requireNotNull(OperationKind.entries.singleOrNull { it.wireName == raw }) { "Неизвестный вид операции фильтра" }
        }
        return JournalQuery(textQuery, operationKind, categoryId, accountId, tagId)
    }
}

@Dao
internal interface OrganizationDao {
    @Query("SELECT * FROM tag_events ORDER BY sequence")
    suspend fun tags(): List<TagEvent>
    @Query("SELECT * FROM tag_assignments ORDER BY sequence")
    suspend fun assignments(): List<TagAssignment>
    @Query("SELECT * FROM filter_events ORDER BY sequence")
    suspend fun filters(): List<FilterEvent>
    @Insert suspend fun insert(event: TagEvent): Long
    @Insert suspend fun insert(event: TagAssignment): Long
    @Insert suspend fun insert(event: FilterEvent): Long
}

internal data class OrganizationState(
    val tagEvents: List<TagEvent> = emptyList(),
    val assignments: List<TagAssignment> = emptyList(),
    val filterEvents: List<FilterEvent> = emptyList(),
) {
    val tags: List<TagEvent> get() = tagEvents.associateBy(TagEvent::id).values.sortedBy { it.name.lowercase() }
    val filters: List<FilterEvent> get() = filterEvents.associateBy(FilterEvent::id).values.sortedBy { it.name.lowercase() }
    fun tagsByOperation(): Map<String, Set<String>> {
        val active = assignments.associateBy { it.operationId to it.tagId }.values.filter(TagAssignment::present)
        return active.groupBy(TagAssignment::operationId).mapValues { (_, rows) -> rows.map(TagAssignment::tagId).toSet() }
    }

    fun validate(accounts: Set<String>, categories: Set<String>, operations: Set<String>) {
        fun validId(id: String) = id.length in 1..64
        fun validName(name: String) = name == name.trim() && name.length in 1..80
        fun sequence(values: List<Long>) {
            require(values.all { it > 0 } && values.zipWithNext().all { (a, b) -> a < b })
        }
        require(tagEvents.size <= 100_000 && assignments.size <= 100_000 && filterEvents.size <= 100_000)
        sequence(tagEvents.map(TagEvent::sequence))
        sequence(assignments.map(TagAssignment::sequence))
        sequence(filterEvents.map(FilterEvent::sequence))
        require(tagEvents.all { validId(it.id) && validName(it.name) && it.recordedAt >= 0 })
        require(tags.filterNot(TagEvent::archived).map { it.name.lowercase() }.distinct().size == tags.count { !it.archived })
        val tagIds = tags.map(TagEvent::id).toSet()
        require(assignments.all { it.operationId in operations && it.tagId in tagIds && it.recordedAt >= 0 })
        for (event in filterEvents) {
            require(validId(event.id) && validName(event.name) && event.recordedAt >= 0)
            val query = event.query()
            require(query.accountId == null || query.accountId in accounts)
            require(query.categoryId == null || query.categoryId in categories)
            require(query.tagId == null || query.tagId in tagIds)
        }
        require(filters.filterNot(FilterEvent::archived).map { it.name.lowercase() }.distinct().size == filters.count { !it.archived })
    }
}

internal fun DataOutputStream.organization(state: OrganizationState) {
    writeInt(state.tagEvents.size)
    for (event in state.tagEvents) {
        writeLong(event.sequence); writeUTF(event.id); writeUTF(event.name)
        writeBoolean(event.archived); writeLong(event.recordedAt)
    }
    writeInt(state.assignments.size)
    for (event in state.assignments) {
        writeLong(event.sequence); writeUTF(event.operationId); writeUTF(event.tagId)
        writeBoolean(event.present); writeLong(event.recordedAt)
    }
    writeInt(state.filterEvents.size)
    for (event in state.filterEvents) {
        writeLong(event.sequence); writeUTF(event.id); writeUTF(event.name); writeUTF(event.textQuery)
        writeUTF(event.kind.orEmpty()); writeUTF(event.categoryId.orEmpty())
        writeUTF(event.accountId.orEmpty()); writeUTF(event.tagId.orEmpty())
        writeBoolean(event.archived); writeLong(event.recordedAt)
    }
}

internal fun DataInputStream.organization(): OrganizationState {
    fun count() = readInt().also { require(it in 0..100_000) }
    val tags = List(count()) { TagEvent(readLong(), readUTF(), readUTF(), readBoolean(), readLong()) }
    val assignments = List(count()) { TagAssignment(readLong(), readUTF(), readUTF(), readBoolean(), readLong()) }
    val filters = List(count()) {
        FilterEvent(readLong(), readUTF(), readUTF(), readUTF(), readUTF().ifEmpty { null },
            readUTF().ifEmpty { null }, readUTF().ifEmpty { null }, readUTF().ifEmpty { null }, readBoolean(), readLong())
    }
    return OrganizationState(tags, assignments, filters)
}

internal val organizationMigration = object : Migration(7, 8) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE tag_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, archived INTEGER NOT NULL, recordedAt INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE tag_assignments (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, operationId TEXT NOT NULL, tagId TEXT NOT NULL, present INTEGER NOT NULL, recordedAt INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE filter_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, textQuery TEXT NOT NULL, kind TEXT, categoryId TEXT, accountId TEXT, tagId TEXT, archived INTEGER NOT NULL, recordedAt INTEGER NOT NULL)")
    }
}
