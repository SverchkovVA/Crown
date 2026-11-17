package ru.crown.finance.ledger

import java.math.BigDecimal

private val moneyInput = Regex("[+-]?(?:[0-9]+|[0-9]{1,3}(?:[ \\u00A0\\u202F][0-9]{3})+)(?:[.,][0-9]{1,2})?")
private val groupSpaces = Regex("[ \\u00A0\\u202F]")

internal fun parseMinor(text: String, signed: Boolean = false): Long? {
    val value = text.trim().replace('−', '-')
    if (!moneyInput.matches(value) || (!signed && value.first() in "+-")) return null
    return runCatching {
        BigDecimal(value.replace(groupSpaces, "").replace(',', '.')).movePointRight(2).longValueExact()
    }.getOrNull()?.takeUnless { it == Long.MIN_VALUE }
}
