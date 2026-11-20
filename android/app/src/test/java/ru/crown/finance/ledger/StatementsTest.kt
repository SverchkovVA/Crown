package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.YearMonth

class StatementsTest {
    private val sample = "Дата;Сумма;Описание;ID\n20.11.2026;-200,00;Покупка;one\n20.11.2026;120,00;Поступление;two\n31.11.2026;50,00;Неверная дата;bad"
    private val account = Account("main", "Основной")
    private val empty = LedgerSnapshot.from(YearMonth.of(2026, 11), listOf(account), emptyList())

    @Test
    fun csvRetainsExactRowsAndSeparatesInvalidDate() {
        val document = parseStatement(sample.toByteArray())
        assertEquals(listOf(-20_000L, 12_000L), document.rows.map(StatementRow::signedMinor))
        assertEquals(3, document.issues.single().position)
        assertEquals(2, previewStatement(document, empty, account.id).count { it.status == ImportStatus.New })
    }

    @Test
    fun csvSupportsQuotedLinesAndWindows1251() {
        val text = "\uFEFFdate,amount,description\r\n2026-11-20,-2.55,\"Строка, с \"\"кавычкой\"\"\nпродолжение\"\r\n"
        val row = parseStatement(text.toByteArray()).rows.single()
        assertEquals(-255L, row.signedMinor)
        assertEquals("Строка, с \"кавычкой\"\nпродолжение", row.description)
        val legacy = parseStatement(sample.toByteArray(java.nio.charset.Charset.forName("windows-1251")))
        assertEquals("Покупка", legacy.rows.first().description)
    }

    @Test
    fun malformedCsvAndOwnExportCannotEnterAsBankMovements() {
        assertThrows(IllegalArgumentException::class.java) { parseStatement("Дата;Сумма\n\"ошибка".toByteArray()) }
        assertThrows(IllegalArgumentException::class.java) {
            parseStatement("Дата;Вид;Сумма;\"Сумма, мин. ед.\"\n2026-11-20;Расход;2,55;255".toByteArray())
        }
        assertThrows(IllegalArgumentException::class.java) {
            parseStatement("date;дата;amount\n2026-11-20;2026-11-20;2".toByteArray())
        }
        val invalid = parseStatement("date;amount;currency\n2026-11-20;0;RUB\n2026-11-20;5;USD".toByteArray())
        assertEquals(2, invalid.issues.size)
        assertTrue(invalid.rows.isEmpty())
    }

    @Test
    fun duplicateProtectionDistinguishesRepeatedFileChangedIdAndPossibleMatch() {
        val document = parseStatement(sample.toByteArray())
        val row = document.rows.first()
        val key = document.key(account.id, row)
        val value = row.proposal(account.id, key)
        val existing = empty.copy(history = listOf(OperationHistory(listOf(Revision(value, null)))))
        assertEquals(ImportStatus.PossibleDuplicate, previewStatement(document, existing, account.id).first().status)
        val receipt = StatementReceipt(key, row.payloadHash, value.id, value.fingerprint())
        val recorded = existing.copy(statementReceipts = listOf(receipt))
        assertEquals(ImportStatus.Duplicate, previewStatement(document, recorded, account.id).first().status)
        val changed = parseStatement(sample.replace("-200,00", "-201,00").toByteArray())
        assertEquals(ImportStatus.Conflict, previewStatement(changed, recorded, account.id).first().status)
        val sameId = parseStatement("date;amount;id\n2026-11-20;10;one\n2026-11-20;20;one".toByteArray())
        assertTrue(previewStatement(sameId, empty, account.id).all { it.status == ImportStatus.Conflict })
    }

    @Test
    fun missingIdUsesFileIdentityAndRequiresChoiceAcrossDifferentFiles() {
        val document = parseStatement("date;amount\n2026-11-20;-5.00".toByteArray())
        val row = document.rows.single()
        val value = row.proposal(account.id, document.key(account.id, row))
        val recorded = empty.copy(
            history = listOf(OperationHistory(listOf(Revision(value, null)))),
            statementReceipts = listOf(StatementReceipt(document.key(account.id, row), row.payloadHash, value.id, value.fingerprint())),
        )
        assertEquals(ImportStatus.Duplicate, previewStatement(document, recorded, account.id).single().status)
        val another = parseStatement("date;amount\n2026-11-20;-5.00\n".toByteArray())
        assertEquals(ImportStatus.PossibleDuplicate, previewStatement(another, recorded, account.id).single().status)
    }

    @Test
    fun ofxAcceptsXmlAndSgmlDatesInStatementCalendar() {
        val sgml = """
            OFXHEADER:100
            DATA:OFXSGML
            <OFX><CURDEF>RUB
            <BANKACCTFROM><BANKID>SYNTHETIC<ACCTID>ONLY-TEST</BANKACCTFROM>
            <BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261120235959[-3:BRT]
            <TRNAMT>-25.50<FITID>one<NAME>Синтетика</STMTTRN></BANKTRANLIST></OFX>
        """.trimIndent()
        val one = parseStatement(sgml.toByteArray())
        assertEquals(LocalDate.of(2026, 11, 20), one.rows.single().date)
        assertEquals(-2550L, one.rows.single().signedMinor)
        val xml = """
            <?xml version="1.0"?><OFX><CURDEF>RUB</CURDEF>
            <BANKACCTFROM><BANKID>SYNTHETIC</BANKID><ACCTID>ONLY-TEST</ACCTID></BANKACCTFROM>
            <BANKTRANLIST><STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20261120</DTPOSTED>
            <TRNAMT>10.00</TRNAMT><FITID>two</FITID><MEMO>Себе &amp; резерв</MEMO></STMTTRN></BANKTRANLIST></OFX>
        """.trimIndent()
        assertEquals(1000L, parseStatement(xml.toByteArray()).rows.single().signedMinor)
        assertEquals(one.scope, parseStatement(xml.toByteArray()).scope)
    }

    @Test
    fun ofxRejectsDtdSeveralAccountsAndIsolatesBankCorrections() {
        val source = "<OFX><CURDEF>RUB<BANKACCTFROM><ACCTID>test</BANKACCTFROM><BANKTRANLIST>" +
            "<STMTTRN><DTPOSTED>20261120<TRNAMT>10<FITID>a<CORRECTFITID>old</STMTTRN></BANKTRANLIST></OFX>"
        assertEquals(1, parseStatement(source.toByteArray()).issues.size)
        assertThrows(IllegalArgumentException::class.java) { parseStatement(("<!DOCTYPE OFX [<!ENTITY x SYSTEM 'file:///test'>]>" + source).toByteArray()) }
        assertThrows(IllegalArgumentException::class.java) { parseStatement(source.replace("</OFX>", "<BANKACCTFROM></BANKACCTFROM></OFX>").toByteArray()) }
        assertThrows(IllegalArgumentException::class.java) { parseStatement(source.replace("</STMTTRN>", "").toByteArray()) }
    }
}
