package ru.crown.finance.ledger

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.io.DataInputStream
import java.io.DataOutputStream

internal enum class CatalogType(val wire: String, val title: String) {
    Bank("bank", "Банк"), Account("account", "Счёт"), Category("category", "Категория");
    companion object { fun fromWire(value: String) = requireNotNull(entries.singleOrNull { it.wire == value }) { "Неизвестный справочник" } }
}
internal data class CatalogKey(val type: CatalogType, val id: String)
internal data class CatalogValue(
    val type: CatalogType, val id: String, val name: String,
    val bankId: String? = null, val parentId: String? = null, val kind: String? = null, val archived: Boolean = false,
) {
    val key get() = CatalogKey(type, id)
    fun validate() {
        require(id.length in 1..64 && name == name.trim() && name.isNotBlank() && name.length <= if (type == CatalogType.Category) 80 else 100) { "Проверьте название справочника" }
        require(bankId == null || bankId.length in 1..64)
        require(parentId == null || parentId.length in 1..64)
        when (type) {
            CatalogType.Bank -> require(bankId == null && parentId == null && kind == null)
            CatalogType.Account -> { require(parentId == null); AccountKind.fromWireName(requireNotNull(kind)) }
            CatalogType.Category -> { require(bankId == null && parentId != id); require(OperationKind.fromWireName(requireNotNull(kind)) in categoryKinds) }
        }
    }
    fun bank() = Bank(id, name).also { require(type == CatalogType.Bank) }
    fun account() = Account(id, name, bankId, AccountKind.fromWireName(requireNotNull(kind))).also { require(type == CatalogType.Account) }
    fun category() = Category(id, name, OperationKind.fromWireName(requireNotNull(kind)), parentId).also { require(type == CatalogType.Category) }
}
internal fun Bank.catalogValue() = CatalogValue(CatalogType.Bank, id, name)
internal fun Account.catalogValue() = CatalogValue(CatalogType.Account, id, name, bankId = bankId, kind = kind.wireName)
internal fun Category.catalogValue() = CatalogValue(CatalogType.Category, id, name, parentId = parentId, kind = kind.wireName)
internal data class CatalogVersion(val value: CatalogValue, val sequence: Long = 0, val recordedAt: Long = 0)

@Entity(tableName = "catalog_events", indices = [Index(value = ["entityType", "entityId", "sequence"])])
internal data class CatalogEvent(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val entityType: String, val entityId: String, val name: String,
    val bankId: String?, val parentId: String?, val kind: String?, val archived: Boolean,
    val created: Boolean, val previousSequence: Long?, val recordedAt: Long,
) {
    fun value() = CatalogValue(CatalogType.fromWire(entityType), entityId, name, bankId, parentId, kind, archived).also { it.validate() }
}
internal fun CatalogValue.event(previous: CatalogVersion?, time: Long) = CatalogEvent(
    entityType = type.wire, entityId = id, name = name, bankId = bankId, parentId = parentId, kind = kind,
    archived = archived, created = previous == null, previousSequence = previous?.sequence, recordedAt = time,
)
@Dao internal interface CatalogHistoryDao {
    @Query("SELECT * FROM catalog_events ORDER BY sequence") suspend fun events(): List<CatalogEvent>
    @Insert suspend fun insert(event: CatalogEvent): Long
}
internal class CatalogState private constructor(
    private val current: LinkedHashMap<CatalogKey, CatalogVersion>,
    val history: MutableList<CatalogVersion>,
) {
    private val names = mutableMapOf<Triple<CatalogType, String?, String>, CatalogKey>()
    private fun nameKey(value: CatalogValue) = Triple(value.type, value.kind.takeIf { value.type == CatalogType.Category }, value.name.lowercase())
    init {
        for (entry in current.values) {
            entry.value.validate()
            if (!entry.value.archived) require(names.put(nameKey(entry.value), entry.value.key) == null) { "Повтор активного названия справочника" }
        }
    }
    fun get(type: CatalogType, id: String): CatalogVersion = requireNotNull(current[CatalogKey(type, id)]) { "Запись справочника не найдена" }
    fun archived(type: CatalogType, id: String) = get(type, id).value.archived
    val accounts get() = current.values.filter { it.value.type == CatalogType.Account }.map { it.value.account() }
    val banks get() = current.values.filter { it.value.type == CatalogType.Bank }.map { it.value.bank() }.sortedBy { it.name.lowercase() }
    val categories get() = current.values.filter { it.value.type == CatalogType.Category }.map { it.value.category() }.sortedBy { it.name.lowercase() }
    fun check(value: CatalogValue, expected: CatalogVersion?) {
        value.validate()
        val previous = current[value.key]
        require(previous == expected) { "Справочник изменился. Откройте запись заново" }
        require(expected != null || !value.archived) { "Новая запись не может быть архивной" }
        require(expected == null || expected.value != value) { "Поля справочника не изменились" }
        if (value.type == CatalogType.Category && previous != null) require(previous.value.kind == value.kind) { "Вид существующей категории менять нельзя" }
        if (!value.archived) require(names[nameKey(value)].let { it == null || it == value.key }) { "Такое действующее название уже есть" }
        if (value.bankId != null) {
            val bank = get(CatalogType.Bank, value.bankId)
            require(!bank.value.archived || previous?.value?.bankId == value.bankId) { "Выберите действующий банк" }
        }
        if (value.parentId != null) {
            val parent = get(CatalogType.Category, value.parentId)
            require(parent.value.kind == value.kind) { "Родитель должен иметь тот же вид" }
            require(!parent.value.archived || previous?.value?.parentId == value.parentId) { "Выберите действующую родительскую категорию" }
            val visited = mutableSetOf(value.id)
            var next: CatalogVersion? = parent
            while (next != null) {
                require(visited.add(next.value.id)) { "В категориях обнаружен цикл" }
                next = next.value.parentId?.let { get(CatalogType.Category, it) }
            }
        }
    }
    private fun apply(event: CatalogEvent) {
        val value = event.value()
        val previous = current[value.key]
        require(if (event.created) previous == null && event.previousSequence == null else previous != null && event.previousSequence == previous.sequence) { "Нарушена цепочка версий справочника" }
        check(value, previous)
        if (previous != null && !previous.value.archived) names.remove(nameKey(previous.value))
        val version = CatalogVersion(value, event.sequence, event.recordedAt)
        current[value.key] = version
        if (!value.archived) names[nameKey(value)] = value.key
        history += version
    }
    fun financialReferences(operation: Operation, retained: Operation? = null, purchase: Operation? = null) {
        val retainedAccounts = listOfNotNull(retained, purchase).flatMap { listOfNotNull(it.accountId, it.destinationId) }.toSet()
        for (id in setOfNotNull(operation.accountId, operation.destinationId)) {
            require(!archived(CatalogType.Account, id) || id in retainedAccounts) { "Нельзя назначить архивный счёт новой операции" }
        }
        val retainedCategories = listOfNotNull(retained, purchase).flatMap { it.allocations().mapNotNull { part -> part.categoryId } }.toSet()
        val expected = if (operation.kind == OperationKind.Refund) OperationKind.Expense else operation.kind
        for (id in operation.allocations().mapNotNull { it.categoryId }) {
            val category = get(CatalogType.Category, id).value
            require(category.kind == expected.wireName) { "Категория должна соответствовать виду операции" }
            require(!category.archived || id in retainedCategories) { "Нельзя назначить архивную категорию новой операции" }
        }
    }
    companion object {
        fun from(accounts: List<Account>, categories: List<Category>, banks: List<Bank>, events: List<CatalogEvent>): CatalogState {
            val seeds = (banks.map(Bank::catalogValue) + accounts.map(Account::catalogValue) + categories.map(Category::catalogValue)).associateBy(CatalogValue::key)
            require(seeds.size == accounts.size + categories.size + banks.size)
            require(events.size <= 100000 && events.all { it.sequence > 0 && it.recordedAt >= 0 })
            require(events.zipWithNext().all { (a,b) -> a.sequence < b.sequence })
            val births = events.filter(CatalogEvent::created)
            require(births.map { it.value().key }.distinct().size == births.size)
            for (event in births) require(seeds[event.value().key] == event.value()) { "Исходные поля создания не совпали" }
            val newKeys = births.map { it.value().key }.toSet()
            val baseline = seeds.values.filter { it.key !in newKeys }.map { CatalogVersion(it) }
            val initial = LinkedHashMap(baseline.associateBy { it.value.key })
            val state = CatalogState(initial, baseline.toMutableList())
            validateCatalogs(state.accounts, state.categories, state.banks)
            for (event in events) {
                require(event.value().key in seeds) { "Нет исходной записи справочника" }
                state.apply(event)
            }
            require(state.current.size == seeds.size)
            validateCatalogs(state.accounts, state.categories, state.banks, checkNames = false)
            return state
        }
    }
}
internal fun LedgerSnapshot.catalogVersion(type: CatalogType, id: String): CatalogVersion =
    catalogHistory.lastOrNull { it.value.type == type && it.value.id == id } ?: CatalogVersion(when (type) {
        CatalogType.Bank -> banks.single { it.id == id }.catalogValue()
        CatalogType.Account -> balances.single { it.account.id == id }.account.catalogValue()
        CatalogType.Category -> categories.single { it.id == id }.catalogValue()
    })
internal fun LedgerSnapshot.catalogArchived(type: CatalogType, id: String) = catalogVersion(type, id).value.archived

internal fun LedgerSnapshot.usableRules() = currentRules(ruleEvents).map { it.rule() }.filter {
    !catalogArchived(CatalogType.Category, it.categoryId) && (it.accountId == null || !catalogArchived(CatalogType.Account, it.accountId))
}
internal fun LedgerSnapshot.selectableAccounts(retained: Operation? = null) = balances.filter {
    !catalogArchived(CatalogType.Account, it.account.id) || it.account.id == retained?.accountId || it.account.id == retained?.destinationId
}
internal fun LedgerSnapshot.selectableCategories(retained: Operation? = null): List<Category> {
    val used = retained?.allocations()?.mapNotNull { it.categoryId }?.toSet().orEmpty()
    return categories.filter { !catalogArchived(CatalogType.Category, it.id) || it.id in used }
}
internal fun DataOutputStream.catalogEvents(events: List<CatalogEvent>) {
    writeInt(events.size)
    events.forEach {
        writeLong(it.sequence); writeUTF(it.entityType); writeUTF(it.entityId); writeUTF(it.name)
        writeUTF(it.bankId.orEmpty()); writeUTF(it.parentId.orEmpty()); writeUTF(it.kind.orEmpty())
        writeBoolean(it.archived); writeBoolean(it.created); writeBoolean(it.previousSequence != null)
        it.previousSequence?.let(::writeLong); writeLong(it.recordedAt)
    }
}
internal fun DataInputStream.catalogEvents(): List<CatalogEvent> = List(readInt().also { require(it in 0..100000) }) {
    val sequence=readLong(); val type=readUTF(); val id=readUTF(); val name=readUTF()
    val bank=readUTF().ifEmpty { null }; val parent=readUTF().ifEmpty { null }; val kind=readUTF().ifEmpty { null }
    CatalogEvent(sequence, type, id, name, bank, parent, kind, readBoolean(), readBoolean(), if (readBoolean()) readLong() else null, readLong())
}
internal val catalogHistoryMigration = object : Migration(13,14) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE catalog_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, entityType TEXT NOT NULL, entityId TEXT NOT NULL, name TEXT NOT NULL, bankId TEXT, parentId TEXT, kind TEXT, archived INTEGER NOT NULL, created INTEGER NOT NULL, previousSequence INTEGER, recordedAt INTEGER NOT NULL)")
        db.execSQL("CREATE INDEX index_catalog_events_entityType_entityId_sequence ON catalog_events(entityType, entityId, sequence)")
    }
}
