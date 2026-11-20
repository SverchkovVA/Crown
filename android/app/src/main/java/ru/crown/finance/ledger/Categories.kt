package ru.crown.finance.ledger

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import java.math.BigInteger

internal val categoryKinds = listOf(OperationKind.Expense, OperationKind.Income)

internal data class Category(
    val id: String,
    val name: String,
    val kind: OperationKind,
    val parentId: String? = null,
) {
    init {
        require(id.length in 1..64 && name.isNotBlank() && name.length <= 80 && kind in categoryKinds)
        require(parentId == null || (parentId != id && parentId.length in 1..64))
    }
}

@Entity(tableName = "ledger_categories")
internal data class CategoryRow(
    @PrimaryKey val id: String, val name: String, val kind: String, val parentId: String? = null,
) {
    fun toCategory() = Category(id, name, OperationKind.fromWireName(kind), parentId)
}

internal fun Category.toRow() = CategoryRow(id, name, kind.wireName, parentId)

@Dao
internal interface CategoryDao {
    @Query("SELECT * FROM ledger_categories ORDER BY name")
    suspend fun all(): List<CategoryRow>

    @Query("SELECT * FROM ledger_categories WHERE id = :id")
    suspend fun find(id: String): CategoryRow?

    @Insert
    suspend fun insert(category: CategoryRow)
}

internal val categoriesMigration = object : Migration(2, 3) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE ledger_categories (id TEXT NOT NULL PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL)")
        db.execSQL("ALTER TABLE ledger_operations ADD COLUMN categoryId TEXT")
        db.execSQL("ALTER TABLE ledger_revisions ADD COLUMN value_categoryId TEXT")
    }
}

internal data class CategoryTotal(val name: String, val kind: OperationKind, val minor: BigInteger)

internal fun categoryTotals(snapshot: LedgerSnapshot): List<CategoryTotal> {
    val names = snapshot.categories.associate { it.id to it.name }
    val totals = linkedMapOf<Pair<OperationKind, String?>, BigInteger>()
    for (operation in snapshot.operations) {
        if (operation.kind !in categoryKinds && operation.kind != OperationKind.Refund) continue
        val refund = operation.kind == OperationKind.Refund
        val kind = if (refund) OperationKind.Expense else operation.kind
        for (part in operation.allocations()) {
            val key = kind to part.categoryId
            val amount = part.amountMinor.toBigInteger().let { if (refund) -it else it }
            totals[key] = totals.getOrDefault(key, BigInteger.ZERO) + amount
        }
    }
    val rows = totals.map { (key, minor) ->
        CategoryTotal(names[key.second] ?: "Без категории", key.first, minor)
    }
    val fees = snapshot.operations.filter { it.kind == OperationKind.Transfer }
        .fold(BigInteger.ZERO) { total, row -> total + row.feeMinor.toBigInteger() }
    val extra = if (fees.signum() > 0) listOf(CategoryTotal("Комиссии переводов", OperationKind.Expense, fees)) else emptyList()
    return (rows + extra).sortedWith(compareBy(CategoryTotal::kind).thenByDescending(CategoryTotal::minor))
}
