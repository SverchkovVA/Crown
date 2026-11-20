package ru.crown.finance.ledger

import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.Charset
import java.security.MessageDigest
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.ResolverStyle
import java.util.Locale

internal const val MAX_STATEMENT_BYTES = 8 * 1024 * 1024
internal const val MAX_STATEMENT_ROWS = 2000

internal data class StatementRow(
    val position: Int,
    val date: LocalDate,
    val signedMinor: Long,
    val description: String,
    val externalId: String?,
    val transferHint: Boolean = false,
) {
    init {
        require(position > 0 && date.year in 1900..9999)
        require(signedMinor != 0L && signedMinor != Long.MIN_VALUE)
        require(description.length <= 200 && externalId?.length?.let { it in 1..255 } != false)
    }
    val payloadHash: String get() = digest(
        listOf(date.toString(), signedMinor.toString(), description, externalId.orEmpty(), transferHint.toString()).joinToString("\u0000"),
    )
}

internal data class StatementIssue(val position: Int, val message: String)

internal data class StatementDocument(
    val kind: String,
    val fileHash: String,
    val scope: String,
    val rows: List<StatementRow>,
    val issues: List<StatementIssue>,
)

internal fun digest(text: String): String = digest(text.toByteArray(Charsets.UTF_8))
internal fun digest(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes)
    .joinToString("") { "%02x".format(it) }

internal fun readStatement(stream: InputStream): StatementDocument {
    val output = java.io.ByteArrayOutputStream()
    val block = ByteArray(8192)
    while (true) {
        val count = stream.read(block)
        if (count == -1) break
        require(count > 0 && output.size() + count <= MAX_STATEMENT_BYTES) { "Файл должен быть не больше 8 МиБ" }
        output.write(block, 0, count)
    }
    return parseStatement(output.toByteArray())
}

internal fun parseStatement(bytes: ByteArray): StatementDocument {
    require(bytes.isNotEmpty() && bytes.size <= MAX_STATEMENT_BYTES) { "Файл пуст или больше 8 МиБ" }
    val text = try {
        Charsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString()
    } catch (_: CharacterCodingException) {
        Charset.forName("windows-1251").newDecoder().decode(ByteBuffer.wrap(bytes)).toString()
    }.removePrefix("\uFEFF")
    require('\u0000' !in text) { "Неподдерживаемая кодировка выписки" }
    return if (text.contains(Regex("<OFX\\s*>", RegexOption.IGNORE_CASE))) {
        parseOfx(text, digest(bytes))
    } else {
        parseCsv(text, digest(bytes))
    }
}

private fun parseCsv(text: String, hash: String): StatementDocument {
    val header = text.lineSequence().first()
    val separator = listOf(';', '\t', ',').maxBy { delimiter ->
        header.count { it == delimiter }
    }
    val records = csvRecords(text, separator).filterNot { row -> row.all(String::isBlank) }
    require(records.size in 2..MAX_STATEMENT_ROWS + 1) { "Нужен заголовок и от 1 до 2000 строк выписки" }
    val names = records.first().map { it.trim().lowercase(Locale.ROOT) }
    require("сумма, мин. ед." !in names && "id покупки возврата" !in names) { "Это экспорт журнала Crown. Выберите банковскую выписку" }
    val aliases = mapOf(
        "date" to setOf("date", "дата", "дата операции"),
        "amount" to setOf("amount", "сумма", "сумма операции"),
        "description" to setOf("description", "описание", "назначение", "назначение платежа", "memo", "details"),
        "id" to setOf("id", "id операции", "external_id", "fitid", "идентификатор"),
        "currency" to setOf("currency", "валюта"),
    )
    val columns = aliases.mapValues { (_, options) ->
        val found = names.indices.filter { names[it] in options }
        require(found.size <= 1) { "В CSV повторяются столбцы одного назначения" }
        found.singleOrNull()
    }
    val dateColumn = requireNotNull(columns["date"]) { "Не найден столбец «Дата»" }
    val amountColumn = requireNotNull(columns["amount"]) { "Не найден столбец «Сумма»" }
    val rows = mutableListOf<StatementRow>()
    val issues = mutableListOf<StatementIssue>()
    for ((index, record) in records.drop(1).withIndex()) {
        val position = index + 1
        fun cell(key: String) = columns[key]?.let { record.getOrNull(it)?.trim() }.orEmpty()
        try {
            require(record.size == names.size) { "Число столбцов отличается от заголовка" }
            val date = parseStatementDate(record[dateColumn].trim())
            val amount = requireNotNull(parseMinor(record[amountColumn], signed = true)) { "Проверьте сумму со знаком" }
            require(amount != 0L) { "Нулевая сумма исключена" }
            require(cell("currency").uppercase(Locale.ROOT) in setOf("", "RUB", "RUR")) { "В этом срезе импорт поддерживает RUB" }
            val description = cell("description").ifBlank { "Операция из выписки" }
            rows += StatementRow(position, date, amount, description, cell("id").ifEmpty { null }, ownTransferHint(description))
        } catch (error: IllegalArgumentException) {
            issues += StatementIssue(position, error.message ?: "Проверьте поля строки")
        }
    }
    return StatementDocument("CSV", hash, "csv", rows, issues)
}

private fun parseStatementDate(text: String): LocalDate {
    val formats = listOf(DateTimeFormatter.ISO_LOCAL_DATE, DateTimeFormatter.ofPattern("dd.MM.uuuu").withResolverStyle(ResolverStyle.STRICT))
    return formats.firstNotNullOfOrNull { format -> runCatching { LocalDate.parse(text, format) }.getOrNull() }
        ?.takeIf { it.year in 1900..9999 } ?: throw IllegalArgumentException("Проверьте дату с годом")
}

private fun csvRecords(text: String, separator: Char): List<List<String>> {
    val result = mutableListOf<List<String>>()
    var row = mutableListOf<String>()
    val field = StringBuilder()
    var quoted = false
    var closedQuote = false
    var index = 0
    fun addField() {
        require(row.size < 128) { "В CSV слишком много столбцов" }
        row += field.toString()
        field.setLength(0)
        closedQuote = false
    }
    fun addRow() {
        addField()
        result += row
        require(result.size <= MAX_STATEMENT_ROWS + 2) { "В выписке больше 2000 строк" }
        row = mutableListOf()
    }
    while (index < text.length) {
        val char = text[index]
        if (quoted) {
            if (char == '"') {
                if (index + 1 < text.length && text[index + 1] == '"') {
                    field.append('"')
                    index++
                } else {
                    quoted = false
                    closedQuote = true
                }
            } else field.append(char)
        } else when {
            char == separator -> addField()
            char == '\r' || char == '\n' -> {
                addRow()
                if (char == '\r' && index + 1 < text.length && text[index + 1] == '\n') index++
            }
            char == '"' && field.isEmpty() && !closedQuote -> quoted = true
            closedQuote && char.isWhitespace() -> Unit
            closedQuote || char == '"' -> throw IllegalArgumentException("Нарушено экранирование CSV")
            else -> field.append(char)
        }
        require(field.length <= 4000) { "Поле CSV слишком длинное" }
        index++
    }
    require(!quoted) { "В CSV не закрыта кавычка" }
    if (field.isNotEmpty() || row.isNotEmpty() || closedQuote) addRow()
    return result
}

private fun parseOfx(text: String, hash: String): StatementDocument {
    require(!text.contains(Regex("<!\\s*(DOCTYPE|ENTITY)", RegexOption.IGNORE_CASE))) { "OFX с DTD или ENTITY отклонён" }
    val accountCount = Regex("<(?:BANKACCTFROM|CCACCTFROM)\\s*>", RegexOption.IGNORE_CASE).findAll(text).count()
    require(accountCount == 1) { "Выберите OFX с одним банковским счётом" }
    val currencies = Regex("<CURDEF>\\s*([^<\\r\\n]+)", RegexOption.IGNORE_CASE).findAll(text)
        .map { it.groupValues[1].trim().uppercase(Locale.ROOT) }.toSet()
    require(currencies == setOf("RUB") || currencies == setOf("RUR")) { "OFX должен содержать выписку одного счёта RUB" }
    val openings = Regex("<STMTTRN\\s*>", RegexOption.IGNORE_CASE).findAll(text).toList()
    require(openings.size in 1..MAX_STATEMENT_ROWS) { "Нужны от 1 до 2000 полных проведённых строк OFX" }
    val closing = Regex("</STMTTRN\\s*>", RegexOption.IGNORE_CASE)
    val blocks = openings.mapIndexed { index, start ->
        val end = openings.getOrNull(index + 1)?.range?.first ?: text.length
        val section = text.substring(start.range.last + 1, end)
        val close = requireNotNull(closing.find(section)) { "Не закрыта строка OFX" }
        section.substring(0, close.range.first)
    }
    val rows = mutableListOf<StatementRow>()
    val issues = mutableListOf<StatementIssue>()
    for ((index, block) in blocks.withIndex()) {
        val position = index + 1
        try {
            require(!block.contains(Regex("<(?:CORRECTFITID|CORRECTACTION)>", RegexOption.IGNORE_CASE))) { "Банковская корректировка требует отдельного ввода" }
            val rawDate = ofxField(block, "DTPOSTED")
            require(rawDate.matches(Regex("[0-9]{8}(?:[0-9]{6}(?:\\.[0-9]+)?(?:\\[[^\\]]+\\])?)?"))) { "Проверьте дату проведения OFX" }
            val date = runCatching { LocalDate.parse(rawDate.take(8), DateTimeFormatter.BASIC_ISO_DATE) }
                .getOrNull() ?: throw IllegalArgumentException("Проверьте дату проведения OFX")
            val rawAmount = ofxField(block, "TRNAMT")
            val amount = requireNotNull(parseMinor(rawAmount, signed = true)) { "Проверьте сумму OFX" }
            require(amount != 0L) { "Нулевая сумма исключена" }
            val id = ofxField(block, "FITID").takeIf { it.isNotBlank() }
            val name = ofxField(block, "NAME")
            val memo = ofxField(block, "MEMO")
            val description = listOf(name, memo).filter(String::isNotBlank).distinct().joinToString(" · ").ifBlank { "Операция из выписки" }
            rows += StatementRow(position, date, amount, description, id, ofxField(block, "TRNTYPE").equals("XFER", true) || ownTransferHint(description))
        } catch (error: IllegalArgumentException) {
            issues += StatementIssue(position, error.message ?: "Проверьте поля OFX")
        }
    }
    val scope = digest(listOf("ofx", ofxField(text, "BANKID"), ofxField(text, "ACCTID"), ofxField(text, "ACCTTYPE")).joinToString("\u0000"))
    return StatementDocument("OFX", hash, scope, rows, issues)
}

private fun ofxField(text: String, name: String): String {
    val values = Regex("<$name\\s*>\\s*([^<\\r\\n]*)", RegexOption.IGNORE_CASE).findAll(text).toList()
    require(values.size <= 1) { "Поле $name повторяется" }
    val value = values.singleOrNull()?.groupValues?.get(1)?.trim().orEmpty()
    require(value.length <= 1000 && !value.contains(Regex("&(?!amp;|lt;|gt;|quot;|apos;)"))) { "Неподдерживаемое поле OFX" }
    return value.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&apos;", "'").replace("&amp;", "&")
}

private fun ownTransferHint(text: String): Boolean =
    Regex("перевод\\s+себе|между\\s+(своими\\s+)?счетами", RegexOption.IGNORE_CASE).containsMatchIn(text)
