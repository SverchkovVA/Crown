package ru.crown.finance.ledger

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.json.JSONObject
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
    fun jsonRetainsSplitRefundSourceAndExactMinorStrings() {
        val purchase = operation.copy(
            amountMinor = 1000, kind = OperationKind.Expense,
            parts = listOf(Allocation("food", 600), Allocation(null, 400)),
        )
        val refund = operation.copy(
            id = "return", amountMinor = 225, kind = OperationKind.Refund,
            refundOf = purchase.id, refundVersion = purchase.fingerprint(), categoryId = "food",
        )
        val state = LedgerSnapshot.from(month, listOf(account), listOf(purchase, refund))
        val data = JSONObject(JournalExport(state, false, false).encode(JournalFormat.Json).toString(Charsets.UTF_8))
            .getJSONArray("operations")
        val rows = (0 until data.length()).map(data::getJSONObject)
        val returned = rows.single { it.getString("kind") == "refund" }
        assertEquals("225", returned.get("amount_minor"))
        assertEquals(purchase.fingerprint(), returned.getString("refund_version"))
        assertEquals("600", rows.single { it.getString("kind") == "expense" }.getJSONArray("parts")
            .getJSONObject(0).get("amount_minor"))
        assertFalse(returned.has("description"))
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
