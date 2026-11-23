package ru.crown.finance.ledger

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

internal enum class AccountKind(val wireName: String, val title: String) {
    Other("other", "Другой счёт"),
    DebitCard("debit_card", "Дебетовая карта"),
    CreditCard("credit_card", "Кредитная карта"),
    Cash("cash", "Наличные"),
    Saving("saving", "Накопительный счёт"),
    Deposit("deposit", "Вклад"),
    Brokerage("brokerage", "Брокерский счёт");

    companion object {
        fun fromWireName(value: String) = entries.single { it.wireName == value }
    }
}

@Entity(tableName = "ledger_banks")
internal data class Bank(@PrimaryKey val id: String, val name: String) {
    init { require(id.length in 1..64 && name.isNotBlank() && name.length <= 100) }
}

@Dao
internal interface BankDao {
    @Query("SELECT * FROM ledger_banks ORDER BY name")
    suspend fun all(): List<Bank>

    @Insert
    suspend fun insert(bank: Bank)
}

internal fun validateCatalogs(accounts: List<Account>, categories: List<Category>, banks: List<Bank>, checkNames: Boolean = true) {
    val bankIds = banks.map(Bank::id).toSet()
    require(bankIds.size == banks.size)
    if (checkNames) require(banks.map { it.name.lowercase() }.distinct().size == banks.size)
    require(accounts.all { it.bankId == null || it.bankId in bankIds })
    val indexed = categories.associateBy(Category::id)
    require(indexed.size == categories.size)
    val done = mutableSetOf<String>()
    for (category in categories) {
        val path = mutableSetOf<String>()
        var current: Category? = category
        while (current != null && current.id !in done) {
            require(path.add(current.id)) { "В категориях обнаружен цикл" }
            val parent = current.parentId
            if (parent == null) break
            val next = requireNotNull(indexed[parent]) { "Родительская категория не найдена" }
            require(next.kind == current.kind) { "Подкатегория должна иметь тот же вид" }
            current = next
        }
        done.addAll(path)
    }
}

internal val catalogsMigration = object : Migration(6, 7) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE ledger_banks (id TEXT NOT NULL PRIMARY KEY, name TEXT NOT NULL)")
        db.execSQL("ALTER TABLE ledger_accounts ADD COLUMN bankId TEXT")
        db.execSQL("ALTER TABLE ledger_accounts ADD COLUMN kind TEXT NOT NULL DEFAULT 'other'")
        db.execSQL("ALTER TABLE ledger_categories ADD COLUMN parentId TEXT")
    }
}
