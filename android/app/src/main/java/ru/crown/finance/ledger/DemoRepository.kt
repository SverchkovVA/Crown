package ru.crown.finance.ledger

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.withTransaction
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.YearMonth

@Entity(tableName = "ledger_accounts")
internal data class AccountRow(@PrimaryKey val id: String, val name: String, val sortOrder: Int) {
    fun toAccount() = Account(id, name)
}

@Entity(
    tableName = "ledger_operations",
    foreignKeys = [
        ForeignKey(entity = AccountRow::class, parentColumns = ["id"], childColumns = ["accountId"]),
        ForeignKey(entity = AccountRow::class, parentColumns = ["id"], childColumns = ["destinationId"]),
    ],
    indices = [Index("accountId"), Index("destinationId")],
)
internal data class OperationRow(
    @PrimaryKey val id: String,
    val kind: String,
    val date: String,
    val accountId: String,
    val amountMinor: Long,
    val description: String,
    val destinationId: String?,
    val feeMinor: Long,
) {
    fun toOperation() = Operation(
        id, OperationKind.fromWireName(kind), LocalDate.parse(date), accountId, amountMinor,
        description, destinationId, feeMinor,
    )
}

@Dao
internal interface LedgerDao {
    @Query("SELECT * FROM ledger_accounts ORDER BY sortOrder")
    suspend fun accounts(): List<AccountRow>

    @Query("SELECT * FROM ledger_operations")
    suspend fun operations(): List<OperationRow>

    @Insert
    suspend fun insertAccounts(accounts: List<AccountRow>)

    @Insert
    suspend fun insertOperations(operations: List<OperationRow>)
}

@Database(entities = [AccountRow::class, OperationRow::class], version = 1, exportSchema = false)
internal abstract class DemoDatabase : RoomDatabase() {
    abstract fun dao(): LedgerDao
}

internal class DemoRepository(context: Context) : AutoCloseable {
    private val assets = context.applicationContext.assets
    private val database = Room.inMemoryDatabaseBuilder(context.applicationContext, DemoDatabase::class.java)
        .build()

    suspend fun snapshot(): LedgerSnapshot = withContext(Dispatchers.IO) {
        val document = assets.open("ledger-v1-sample.json").bufferedReader().use {
            JSONObject(it.readText())
        }
        require(document.getInt("format") == 1 && document.getString("currency") == "RUB")
        val month = YearMonth.parse(document.getString("month"))
        database.withTransaction {
            val dao = database.dao()
            if (dao.accounts().isEmpty()) {
                dao.insertAccounts(document.getJSONArray("accounts").mapObjects { index, account ->
                    AccountRow(account.getString("id"), account.getString("name"), index)
                })
                dao.insertOperations(document.getJSONArray("operations").mapObjects { _, operation ->
                    OperationRow(
                        id = operation.getString("id"),
                        kind = operation.getString("kind"),
                        date = operation.getString("date"),
                        accountId = operation.getString("account_id"),
                        amountMinor = (operation.get("amount_minor") as String).toLong(),
                        description = operation.getString("description"),
                        destinationId = if (operation.has("destination_id")) operation.getString("destination_id") else null,
                        feeMinor = if (operation.has("fee_minor")) (operation.get("fee_minor") as String).toLong() else 0,
                    )
                })
            }
            LedgerSnapshot.from(
                month,
                dao.accounts().map(AccountRow::toAccount),
                dao.operations().map(OperationRow::toOperation),
            )
        }
    }

    override fun close() = database.close()
}

private inline fun <T> JSONArray.mapObjects(transform: (Int, JSONObject) -> T): List<T> =
    List(length()) { index -> transform(index, getJSONObject(index)) }
