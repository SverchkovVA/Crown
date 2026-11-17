package ru.crown.finance.ledger

import org.json.JSONArray
import org.json.JSONObject
import java.math.BigDecimal

internal enum class JournalFormat { Csv, Json }

internal data class JournalExport(
    val snapshot: LedgerSnapshot,
    val includeNames: Boolean,
    val includeDescription: Boolean,
) {
    private val accounts = snapshot.balances.associate { it.account.id to it.account.name }
    private val categories = snapshot.categories.associate { it.id to it.name }
    val count get() = snapshot.operations.size

    fun encode(format: JournalFormat): ByteArray = when (format) {
        JournalFormat.Csv -> csv().toByteArray(Charsets.UTF_8)
        JournalFormat.Json -> json().toByteArray(Charsets.UTF_8)
    }

    private fun csv(): String = buildString {
        append('\uFEFF')
        val heading = mutableListOf("Дата", "Вид", "Сумма", "Валюта", "Комиссия")
        if (includeNames) heading.addAll(listOf("Счёт", "Второй счёт", "Категория"))
        if (includeDescription) heading.add("Описание")
        heading.addAll(
            listOf("Точность валюты", "Сумма, мин. ед.", "Комиссия, мин. ед.", "ID операции", "ID счёта", "ID второго счёта", "ID категории"),
        )
        csvLine(heading)
        for (operation in snapshot.operations) {
            val fields = mutableListOf(
                operation.date.toString(), operation.kind.russianTitle(), decimalRubles(operation.amountMinor),
                "RUB", decimalRubles(operation.feeMinor),
            )
            if (includeNames) {
                fields += csvText(accounts[operation.accountId])
                fields += csvText(accounts[operation.destinationId])
                fields += csvText(categories[operation.categoryId])
            }
            if (includeDescription) fields += csvText(operation.description)
            fields.addAll(
                listOf(
                    "2", operation.amountMinor.toString(), operation.feeMinor.toString(), operation.id,
                    operation.accountId, operation.destinationId.orEmpty(), operation.categoryId.orEmpty(),
                ),
            )
            csvLine(fields)
        }
    }

    private fun json(): String {
        val entries = JSONArray()
        for (operation in snapshot.operations) {
            val row = JSONObject(
                mapOf(
                    "operation_id" to operation.id,
                    "date" to operation.date.toString(),
                    "kind" to operation.kind.wireName,
                    "currency" to "RUB",
                    "currency_exponent" to 2,
                    "account_id" to operation.accountId,
                    "destination_account_id" to operation.destinationId,
                    "category_id" to operation.categoryId,
                    "amount_minor" to operation.amountMinor.toString(),
                    "fee_minor" to operation.feeMinor.toString(),
                ),
            )
            if (includeNames) {
                row.put("account_name", accounts[operation.accountId])
                row.put("destination_account_name", accounts[operation.destinationId])
                row.put("category_name", categories[operation.categoryId])
            }
            if (includeDescription) row.put("description", operation.description)
            entries.put(row)
        }
        return JSONObject()
            .put("format", "crown-android-journal-export")
            .put("version", 1)
            .put("money_encoding", "integer_minor_decimal_string")
            .put("month", snapshot.month.toString())
            .put("operations", entries)
            .toString(2) + "\n"
    }
}

private fun OperationKind.russianTitle() = when (this) {
    OperationKind.Opening -> "Начальный остаток"
    OperationKind.Income -> "Поступление"
    OperationKind.Expense -> "Расход"
    OperationKind.Transfer -> "Перевод"
}

private fun decimalRubles(minor: Long) = BigDecimal.valueOf(minor, 2).toPlainString().replace('.', ',')

private fun csvText(value: String?): String {
    val text = value.orEmpty()
    val leading = text.trimStart { it.isWhitespace() || it == '\uFEFF' }
    return if (leading.firstOrNull()?.let { it in "=+-@" } == true) "'$text" else text
}

private fun StringBuilder.csvLine(fields: List<String>) {
    fields.joinTo(this, separator = ";") { field ->
        if (field.any { it in ";\"\r\n" }) "\"" + field.replace("\"", "\"\"") + "\"" else field
    }
    append("\r\n")
}
