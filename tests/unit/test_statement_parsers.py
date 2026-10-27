from datetime import date

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.imports import ImportKind
from personal_finance.infrastructure.importing.statements import (
    CsvMapping,
    csv_columns,
    parse_csv,
    parse_ofx,
)

CSV = (
    "Дата;Сумма;Описание;ID;Валюта\n"
    "15.01.2026;-1 234,56;Проверочная покупка;test-1;RUB\n"
    "16.01.2026;200,00;Проверочный доход;test-2;RUB\n"
)
MAPPING = CsvMapping("Дата", "Сумма", "Описание", "ID", "Валюта")


def ofx(transactions=None, *, currency="RUB", pending="", extra=""):
    transactions = transactions if transactions is not None else trn()
    return (
        (
            '<?xml version="1.0" encoding="UTF-8"?><OFX><SIGNONMSGSRSV1>'
            "<SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY>"
            "</STATUS><FI><FID>test-bank</FID></FI></SONRS>"
            "</SIGNONMSGSRSV1><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>"
        )
        + currency
        + (
            "</CURDEF><BANKACCTFROM><BANKID>test-bank</BANKID><ACCTID>"
            "synthetic-account</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE>"
            "</BANKACCTFROM><BANKTRANLIST><DTSTART>20260101</DTSTART>"
            "<DTEND>20260131</DTEND>"
        )
        + transactions
        + pending
        + (
            "</BANKTRANLIST><LEDGERBAL><BALAMT>900.00</BALAMT><DTASOF>"
            "20260131</DTASOF></LEDGERBAL></STMTRS></STMTTRNRS>"
            "</BANKMSGSRSV1>"
        )
        + extra
        + "</OFX>"
    ).encode()


def trn(
    *,
    identifier="test-id",
    amount="-100.25",
    day="20260115235959.000[-8:PST]",
    extra="",
    kind="DEBIT",
):
    return (
        f"<STMTTRN><TRNTYPE>{kind}</TRNTYPE><DTPOSTED>{day}</DTPOSTED>"
        f"<TRNAMT>{amount}</TRNAMT><FITID>{identifier}</FITID>"
        "<NAME>Проверочное имя &amp; текст</NAME>"
        f"<MEMO>Проверочное примечание</MEMO>{extra}</STMTTRN>"
    )


def test_csv_exact_money_russian_bom_and_explicit_mapping():
    source = CSV.encode("utf-8-sig")
    assert csv_columns(source) == ("Дата", "Сумма", "Описание", "ID", "Валюта")
    document = parse_csv(source, MAPPING)
    assert document.kind == ImportKind.CSV and not document.issues
    assert [r.amount.minor for r in document.rows] == [-123456, 20000]
    assert document.rows[0].occurred_on == date(2026, 1, 15)
    assert document.rows[0].external_id == "test-1"
    assert len(document.scope_hash) == len(document.file_hash) == 64
    assert source.decode("utf-8-sig") == CSV


def test_csv_cp1251_quoted_newline_and_individual_invalid_rows():
    value = (
        'Дата;Сумма;Описание\r\n15.01.2026;-1,23;"две\nстроки"\r\n'
        "31.02.2026;-2,00;ошибка даты\r\n"
        "16.01.2026;2,001;ошибка суммы\r\n"
    )
    document = parse_csv(
        value.encode("cp1251"), CsvMapping("Дата", "Сумма", "Описание", encoding="cp1251")
    )
    assert document.rows[0].note == "две\nстроки"
    assert document.rows[0].amount.minor == -123
    assert [i.position for i in document.issues] == [2, 3]
    assert all("31.02.2026" not in i.message and "2,001" not in i.message for i in document.issues)


@pytest.mark.parametrize(
    "amount",
    ["NaN", "Infinity", "1e3", "0,00", "1,234", "1.20", "1 2 3,00", "92233720368547758,08"],
)
def test_csv_invalid_money_is_visible_issue(amount):
    document = parse_csv(
        ("Дата;Сумма\n15.01.2026;" + amount + "\n").encode(), CsvMapping("Дата", "Сумма")
    )
    assert not document.rows and len(document.issues) == 1


def test_csv_iso_dot_thousands_and_missing_optional_id():
    document = parse_csv(
        'date,amount,id\n2026-01-15,"-1\u202f234.50",\n'.encode(),
        CsvMapping(
            "date",
            "amount",
            id_column="id",
            delimiter=",",
            date_format="%Y-%m-%d",
            decimal_separator=".",
        ),
    )
    assert document.rows[0].amount.minor == -123450 and document.rows[0].external_id is None


@pytest.mark.parametrize(
    "contents",
    [b"", b"date;date\n1;2", b'a;b\n"broken', b"a\x00;b\n1;2", b"x" * (8 * 1024 * 1024 + 1)],
    ids=lambda value: f"file-{len(value)}",
)
def test_csv_rejects_empty_duplicate_header_malformed_or_excessive_file(contents):
    with pytest.raises(ValidationError):
        parse_csv(contents, CsvMapping("a", "b"))


def test_csv_rejects_too_many_rows():
    with pytest.raises(ValidationError, match="2000"):
        parse_csv(("a;b\n" + "15.01.2026;1,00\n" * 2001).encode(), CsvMapping("a", "b"))


def test_csv_different_currency_and_long_note_are_issues():
    source = CSV.replace(";RUB", ";USD", 1).replace("Проверочный доход", "Д" * 501)
    document = parse_csv(source.encode(), MAPPING)
    assert not document.rows and len(document.issues) == 2


def test_ofx_xml_calendar_signed_amount_and_source_scope():
    document = parse_ofx(ofx())
    assert document.kind == ImportKind.OFX and not document.issues
    row = document.rows[0]
    assert row.occurred_on == date(2026, 1, 15) and row.amount.minor == -10025
    assert row.note == "Проверочное имя & текст\nПроверочное примечание"
    assert row.external_id == "test-id" and document.warnings
    assert "synthetic-account" not in document.scope_hash
    reordered = parse_ofx(ofx(trn(identifier="other") + trn()))
    assert document.source_key(row) == reordered.source_key(reordered.rows[1])
    assert row.payload_hash == reordered.rows[1].payload_hash
    changed_account = parse_ofx(ofx().replace(b"synthetic-account", b"other-account"))
    assert changed_account.scope_hash != document.scope_hash


def test_ofx_sgml_scalar_closing_cp1251_and_ampersand():
    source = (
        "OFXHEADER:100\r\nDATA:OFXSGML\r\nVERSION:102\r\nSECURITY:NONE\r\n"
        "ENCODING:USASCII\r\nCHARSET:1251\r\n\r\n<OFX><BANKMSGSRSV1>"
        "<STMTTRNRS><STMTRS><CURDEF>RUB\n<BANKACCTFROM><BANKID>test\n"
        "<ACCTID>synthetic\n<ACCTTYPE>CHECKING\n</BANKACCTFROM>"
        "<BANKTRANLIST><DTSTART>20260101\n<DTEND>20260131\n<STMTTRN>"
        "<TRNTYPE>XFER\n<DTPOSTED>20260115120000[3:MSK]\n<TRNAMT>"
        "-123.45\n<FITID>sgml-1\n<NAME>Проверка & текст\n<MEMO>"
        "Примечание\n</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS>"
        "</BANKMSGSRSV1></OFX>"
    )
    document = parse_ofx(source.encode("cp1251"))
    assert not document.issues and document.rows[0].transfer_hint
    assert document.rows[0].amount.minor == -12345
    assert document.rows[0].note == "Проверка & текст\nПримечание"


@pytest.mark.parametrize(
    "extra",
    [
        "<CORRECTFITID>old-id</CORRECTFITID><CORRECTACTION>DELETE</CORRECTACTION>",
        "<CURRENCY><CURSYM>USD</CURSYM></CURRENCY>",
    ],
)
def test_ofx_corrections_and_foreign_currency_are_visible_issues(extra):
    document = parse_ofx(ofx(trn(extra=extra)))
    assert not document.rows and len(document.issues) == 1


@pytest.mark.parametrize(
    "day",
    [
        "20260230",
        "20260115250000",
        "20260115120000[99:BAD]",
        "2026-01-15",
        "20260115.123",
        "2026011512.123",
    ],
)
def test_ofx_invalid_dates_are_issues(day):
    document = parse_ofx(ofx(trn(day=day)))
    assert not document.rows and len(document.issues) == 1


@pytest.mark.parametrize(
    "contents",
    [
        b'<!DOCTYPE OFX [<!ENTITY x "test">]><OFX/>',
        b"<OFX><broken></OFX>",
        ofx(currency="USD"),
        ofx().replace(b"<CODE>0</CODE>", b"<CODE>2000</CODE>"),
        ofx(extra="<CCSTMTRS/>"),
    ],
)
def test_ofx_rejects_unsafe_wrong_currency_error_or_multiple_statements(contents):
    with pytest.raises(ValidationError):
        parse_ofx(contents)


def test_ofx_pending_transactions_never_become_facts():
    document = parse_ofx(
        ofx(pending="<STMTTRNP><TRNTYPE>HOLD</TRNTYPE><TRNAMT>100.00</TRNAMT></STMTTRNP>")
    )
    assert len(document.rows) == 1 and len(document.warnings) == 2


def test_source_key_without_id_keeps_identical_legitimate_rows_distinct():
    document = parse_csv(b"a;b\n15.01.2026;-1,00\n15.01.2026;-1,00\n", CsvMapping("a", "b"))
    assert document.rows[0].payload_hash == document.rows[1].payload_hash
    assert document.source_key(document.rows[0]) != document.source_key(document.rows[1])
