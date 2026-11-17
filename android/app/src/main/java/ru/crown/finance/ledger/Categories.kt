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

internal data class Category(val id: String, val name: String, val kind: OperationKind) {
    init {
        require(id.length in 1..64 && name.isNotBlank() && name.length <= 80 && kind in categoryKinds)
    }
}

@Entity(tableName = "ledger_categories")
internal data class CategoryRow(@PrimaryKey val id: String, val name: String, val kind: String) {
    fun toCategory() = Category(id, name, OperationKind.fromWireName(kind))
}

internal fun Category.toRow() = CategoryRow(id, name, kind.wireName)

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
    val grouped = snapshot.operations.filter { it.kind in categoryKinds }
        .groupBy { it.kind to it.categoryId }
    val rows = grouped.map { (key, operations) ->
        val (kind, id) = key
        val name = names[id] ?: "Без категории"
        CategoryTotal(name, kind, operations.fold(BigInteger.ZERO) { sum, row -> sum + row.amountMinor.toBigInteger() })
    }
    val fees = snapshot.operations.filter { it.kind == OperationKind.Transfer }
        .fold(BigInteger.ZERO) { total, row -> total + row.feeMinor.toBigInteger() }
    val extra = if (fees.signum() > 0) listOf(CategoryTotal("Комиссии переводов", OperationKind.Expense, fees)) else emptyList()
    return (rows + extra).sortedWith(compareBy(CategoryTotal::kind).thenByDescending(CategoryTotal::minor))
}
