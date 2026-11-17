package ru.crown.finance.ledger

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.YearMonth

class JournalExportTest {
    private val month = YearMonth.of(2026, 10)
    private val account = Account("main", "  =SUM(1;2)")
    private val operation = Operation(
        "op", OperationKind.Income, LocalDate.of(2026, 10, 10), account.id, Long.MAX_VALUE,
        "@СИНТЕТИКА \"строка\"",
    )
    private val snapshot = LedgerSnapshot.from(month, listOf(account), listOf(operation))

    @Test
    fun csvHasExactAmountsAndQuotesUserText() {
        val csv = JournalExport(snapshot, includeNames = true, includeDescription = true)
            .encode(JournalFormat.Csv).toString(Charsets.UTF_8)

        assertEquals('\uFEFF', csv.first())
        assertTrue(csv.endsWith("\r\n"))
        assertTrue(csv.contains("92233720368547758,07;RUB;0,00"))
        assertTrue(csv.contains(";9223372036854775807;0;op;main;"))
        assertTrue(csv.contains("\"'  =SUM(1;2)\""))
        assertTrue(csv.contains("\"'@СИНТЕТИКА \"\"строка\"\"\""))
    }

    @Test
    fun csvCanExcludeNamesAndDescription() {
        val preview = JournalExport(snapshot, includeNames = false, includeDescription = false)
        val csv = preview.encode(JournalFormat.Csv).toString(Charsets.UTF_8)
        assertEquals(1, preview.count)
        assertFalse(csv.contains("SUM"))
        assertFalse(csv.contains("СИНТЕТИКА"))
        assertFalse(csv.contains("Описание"))
    }
}
