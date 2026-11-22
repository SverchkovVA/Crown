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

internal data class CategoryRule(
    val id: String, val name: String, val kind: OperationKind, val contains: String,
    val categoryId: String, val priority: Int = 0, val accountId: String? = null, val enabled: Boolean = true,
) {
    init {
        require(id.length in 1..64 && categoryId.length in 1..64)
        require(name == name.trim() && name.length in 1..80)
        require(kind in categoryKinds)
        require(contains == contains.trim() && contains.length in 1..200 && contains.none(Char::isISOControl))
        require(priority in 0..999 && (accountId == null || accountId.length in 1..64))
    }
}

internal enum class SuggestionStatus { Suggested, Manual, Conflict, NoMatch, Unsupported }
internal data class CategorySuggestion(
    val status: SuggestionStatus, val categoryId: String? = null,
    val ruleIds: List<String> = emptyList(), val priority: Int? = null,
)

internal fun suggestCategory(
    kind: OperationKind, accountId: String, text: String, rules: List<CategoryRule>,
    categories: List<Category>, manualCategory: String? = null, manualParts: Boolean = false,
): CategorySuggestion {
    if (manualCategory != null || manualParts) return CategorySuggestion(SuggestionStatus.Manual, manualCategory)
    if (kind !in categoryKinds) return CategorySuggestion(SuggestionStatus.Unsupported)
    require(rules.size <= 1000 && rules.map(CategoryRule::id).distinct().size == rules.size)
    val kinds = categories.associate { it.id to it.kind }
    val matches = rules.filter {
        it.enabled && it.kind == kind && (it.accountId == null || it.accountId == accountId) &&
            text.contains(it.contains, ignoreCase = true)
    }
    for (match in matches) require(kinds[match.categoryId] == kind) { "Категория правила не найдена или имеет другой вид" }
    val priority = matches.maxOfOrNull(CategoryRule::priority) ?: return CategorySuggestion(SuggestionStatus.NoMatch)
    val winners = matches.filter { it.priority == priority }.sortedBy(CategoryRule::id)
    val conflict = winners.map(CategoryRule::categoryId).distinct().size != 1
    return CategorySuggestion(
        if (conflict) SuggestionStatus.Conflict else SuggestionStatus.Suggested,
        winners.first().categoryId.takeUnless { conflict }, winners.map(CategoryRule::id), priority,
    )
}

@Entity(tableName = "category_rule_events")
internal data class CategoryRuleEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val id: String, val name: String, val kind: String, val containsText: String,
    val categoryId: String, val priority: Int, val accountId: String?, val enabled: Boolean, val recordedAt: Long,
) {
    fun rule() = CategoryRule(
        id, name, requireNotNull(OperationKind.entries.singleOrNull { it.wireName == kind }),
        containsText, categoryId, priority, accountId, enabled,
    )
}

internal fun currentRules(events: List<CategoryRuleEvent>) = events.associateBy(CategoryRuleEvent::id).values.toList()
internal fun CategoryRule.event(recordedAt: Long) = CategoryRuleEvent(
    id = id, name = name, kind = kind.wireName, containsText = contains, categoryId = categoryId,
    priority = priority, accountId = accountId, enabled = enabled, recordedAt = recordedAt,
)

internal fun validateRuleEvents(events: List<CategoryRuleEvent>, accounts: Set<String>, categories: List<Category>) {
    require(events.size <= 100_000 && events.all { it.sequence > 0 && it.recordedAt >= 0 })
    require(events.zipWithNext().all { (first, second) -> first.sequence < second.sequence })
    val kinds = categories.associate { it.id to it.kind }
    for (event in events) {
        val rule = event.rule()
        require(rule.accountId == null || rule.accountId in accounts)
        require(kinds[rule.categoryId] == rule.kind)
    }
    val current = currentRules(events)
    require(current.size <= 1000 && current.map { it.name.lowercase() }.distinct().size == current.size)
}

@Dao
internal interface ClassificationDao {
    @Query("SELECT * FROM category_rule_events ORDER BY sequence")
    suspend fun events(): List<CategoryRuleEvent>
    @Insert suspend fun insert(event: CategoryRuleEvent): Long
}

internal fun DataOutputStream.ruleEvents(events: List<CategoryRuleEvent>) {
    writeInt(events.size)
    for (event in events) {
        writeLong(event.sequence); writeUTF(event.id); writeUTF(event.name); writeUTF(event.kind)
        writeUTF(event.containsText); writeUTF(event.categoryId); writeInt(event.priority)
        writeUTF(event.accountId.orEmpty()); writeBoolean(event.enabled); writeLong(event.recordedAt)
    }
}

internal fun DataInputStream.ruleEvents(): List<CategoryRuleEvent> {
    val count = readInt()
    require(count in 0..100_000)
    return List(count) {
        CategoryRuleEvent(readLong(), readUTF(), readUTF(), readUTF(), readUTF(), readUTF(), readInt(),
            readUTF().ifEmpty { null }, readBoolean(), readLong())
    }
}

internal data class StatementCategories(val choices: List<ImportChoice>, val conflicts: Set<Int>, val suggested: Int)

internal fun classifyStatement(
    choices: List<ImportChoice>, rules: List<CategoryRule>, categories: List<Category>,
): StatementCategories {
    val conflicts = mutableSetOf<Int>()
    var suggested = 0
    val prepared = choices.map { choice ->
        val operation = choice.operation
        if (choice.linkExisting || choice.reviewed) choice else {
            val result = suggestCategory(
                operation.kind, operation.accountId, operation.description, rules, categories,
                operation.categoryId, operation.parts.isNotEmpty(),
            )
            when (result.status) {
                SuggestionStatus.Suggested -> {
                    suggested++
                    choice.copy(operation = operation.copy(categoryId = result.categoryId))
                }
                SuggestionStatus.Conflict -> { conflicts += choice.position; choice }
                else -> choice
            }
        }
    }
    return StatementCategories(prepared, conflicts, suggested)
}

internal val classificationMigration = object : Migration(8, 9) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE category_rule_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, containsText TEXT NOT NULL, categoryId TEXT NOT NULL, priority INTEGER NOT NULL, accountId TEXT, enabled INTEGER NOT NULL, recordedAt INTEGER NOT NULL)")
    }
}
