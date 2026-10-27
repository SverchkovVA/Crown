"""Ограниченные CSV/OFX-парсеры. Только байты локального файла, без сети и записи."""

import csv
import hashlib
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from html import unescape
from xml.etree import ElementTree as ET

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.imports import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_ROWS,
    ImportDocument,
    ImportIssue,
    ImportKind,
    ImportRow,
    digest_json,
)
from personal_finance.domain.models import Money


@dataclass(frozen=True, slots=True)
class CsvMapping:
    date_column: str
    amount_column: str
    note_column: str | None = None
    id_column: str | None = None
    currency_column: str | None = None
    delimiter: str = ";"
    encoding: str = "utf-8-sig"
    date_format: str = "%d.%m.%Y"
    decimal_separator: str = ","

    def __post_init__(self) -> None:
        if (
            self.delimiter not in (";", ",", "\t")
            or self.encoding not in ("utf-8-sig", "cp1251")
            or self.date_format not in ("%d.%m.%Y", "%Y-%m-%d")
            or self.decimal_separator not in (",", ".")
        ):
            raise ValidationError("Неподдерживаемые параметры CSV.")
        required = (self.date_column, self.amount_column)
        columns = required + tuple(
            v for v in (self.note_column, self.id_column, self.currency_column) if v is not None
        )
        if any(not isinstance(c, str) or not c or len(c) > 120 for c in columns) or len(
            set(columns)
        ) != len(columns):
            raise ValidationError(
                "Выберите разные столбцы CSV для даты, суммы и дополнительных полей."
            )


def _check_bytes(contents: bytes) -> None:
    if not isinstance(contents, bytes) or not contents or len(contents) > MAX_IMPORT_BYTES:
        raise ValidationError("Выберите непустой файл размером не более 8 МиБ.")
    if b"\x00" in contents:
        raise ValidationError(
            "Файл содержит нулевые байты; поддерживаются UTF-8 и однобайтовые кодировки."
        )


def _csv_reader(contents: bytes, delimiter: str, encoding: str) -> Iterator[list[str]]:
    _check_bytes(contents)
    if delimiter not in (";", ",", "\t") or encoding not in ("utf-8-sig", "cp1251"):
        raise ValidationError("Неподдерживаемая кодировка или разделитель CSV.")
    try:
        decoded = contents.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValidationError("Не удалось прочитать CSV. Выберите подходящую кодировку.") from exc
    return csv.reader(io.StringIO(decoded, newline=""), delimiter=delimiter, strict=True)


def _header(reader: Iterator[list[str]]) -> list[str]:
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ValidationError("Не найден заголовок CSV.") from exc
    if (
        not 2 <= len(header) <= 100
        or len(set(header)) != len(header)
        or any(not c.strip() or len(c) > 120 or any(ord(s) < 32 for s in c) for c in header)
    ):
        raise ValidationError("CSV требует 2–100 непустых уникальных заголовков.")
    journal_signatures = (
        {"ID операции", "ID версии", "Сумма, мин. ед.", "Дробных знаков валюты"},
        {"operation_id", "revision_id", "amount_minor", "currency_exponent"},
    )
    if any(signature.issubset(header) for signature in journal_signatures):
        raise ValidationError(
            "Формат экспорта журнала Crown не поддерживается импортом выписки. "
            "Для переноса книги используйте проверенную резервную копию."
        )
    return header


def csv_columns(
    contents: bytes, delimiter: str = ";", encoding: str = "utf-8-sig"
) -> tuple[str, ...]:
    return tuple(_header(_csv_reader(contents, delimiter, encoding)))


def _amount(value: str, separator: str) -> Money:
    value = value.strip()
    pattern = (
        r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+)(?:"
        + re.escape(separator)
        + r"[0-9]{1,2})?"
    )
    if len(value) > 128 or re.fullmatch(pattern, value) is None:
        raise ValidationError(
            "Неверная сумма: нужен знак, цифры и не более двух десятичных знаков."
        )
    return Money.parse(value.replace("\u00a0", "").replace("\u202f", ""))


def parse_csv(contents: bytes, mapping: CsvMapping) -> ImportDocument:
    reader = _csv_reader(contents, mapping.delimiter, mapping.encoding)
    header = _header(reader)
    columns = (
        mapping.date_column,
        mapping.amount_column,
        mapping.note_column,
        mapping.id_column,
        mapping.currency_column,
    )
    if any(c is not None and c not in header for c in columns):
        raise ValidationError("Выбранный столбец отсутствует в CSV.")
    rows: list[ImportRow] = []
    issues: list[ImportIssue] = []
    try:
        for position, cells in enumerate(reader, 1):
            if position > MAX_IMPORT_ROWS:
                raise ValidationError("CSV содержит более 2000 строк. Разделите выписку.")
            try:
                if len(cells) != len(header) or any(len(c) > 4096 for c in cells):
                    raise ValidationError(
                        "Число полей не совпадает с заголовком или поле слишком длинное."
                    )
                data = dict(zip(header, cells, strict=True))
                if (
                    mapping.currency_column is not None
                    and data[mapping.currency_column].strip() != "RUB"
                ):
                    raise ValidationError(
                        "Импорт поддерживает RUB; конвертация валют не выполняется."
                    )
                raw_date = data[mapping.date_column].strip()
                expected = (
                    r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}"
                    if mapping.date_format == "%d.%m.%Y"
                    else r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
                )
                if re.fullmatch(expected, raw_date) is None:
                    raise ValidationError("Дата не соответствует выбранному формату.")
                occurred_on = datetime.strptime(raw_date, mapping.date_format).date()
                identifier = data[mapping.id_column].strip() or None if mapping.id_column else None
                rows.append(
                    ImportRow(
                        position,
                        occurred_on,
                        _amount(data[mapping.amount_column], mapping.decimal_separator),
                        data[mapping.note_column].strip() if mapping.note_column else "",
                        identifier,
                    )
                )
            except (ValidationError, ValueError) as exc:
                message = (
                    str(exc)
                    if isinstance(exc, ValidationError)
                    else "Недопустимая календарная дата."
                )
                issues.append(ImportIssue(position, message))
    except csv.Error as exc:
        raise ValidationError(
            "Нарушена структура CSV: проверьте кавычки, разделитель и длину полей."
        ) from exc
    return ImportDocument(
        ImportKind.CSV,
        hashlib.sha256(contents).hexdigest(),
        digest_json("csv-v1"),
        tuple(rows),
        tuple(issues),
    )


def _ofx_text(contents: bytes) -> str:
    _check_bytes(contents)
    head = contents[:2048].decode("ascii", errors="ignore")
    encoding = "utf-8-sig"
    declaration = re.search(r'encoding=["\']([^"\']+)["\']', head, re.IGNORECASE)
    if declaration:
        alias = declaration[1].lower()
        allowed = {
            "utf-8": "utf-8-sig",
            "us-ascii": "ascii",
            "windows-1251": "cp1251",
            "cp1251": "cp1251",
            "windows-1252": "cp1252",
        }
        if alias not in allowed:
            raise ValidationError("Кодировка OFX не поддерживается.")
        encoding = allowed[alias]
    elif re.search(r"(?im)^OFXHEADER:100\s*$", head):
        charset = re.search(r"(?im)^CHARSET:([^\r\n]+)", head)
        if charset:
            alias = charset[1].strip().upper()
            allowed_sgml = {
                "1251": "cp1251",
                "1252": "cp1252",
                "UTF-8": "utf-8-sig",
                "NONE": "ascii",
            }
            if alias not in allowed_sgml:
                raise ValidationError("Кодировка OFX 1.x не поддерживается.")
            encoding = allowed_sgml[alias]
    try:
        return contents.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValidationError("Содержимое OFX не соответствует объявленной кодировке.") from exc


def _sgml_tree(value: str) -> ET.Element:
    start = re.search(r"<OFX\s*>", value, re.IGNORECASE)
    if start is None:
        raise ValidationError("Корневой элемент OFX не найден.")
    value = value[start.start() :]
    root: ET.Element | None = None
    stack: list[ET.Element] = []
    token = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9_]*)\s*>|([^<]+)")
    scalar = {
        "DTSTART",
        "DTEND",
        "DTPOSTED",
        "DTUSER",
        "DTAVAIL",
        "TRNAMT",
        "TRNTYPE",
        "FITID",
        "CORRECTFITID",
        "CORRECTACTION",
        "NAME",
        "EXTDNAME",
        "MEMO",
        "CODE",
        "SEVERITY",
        "MESSAGE",
        "CURDEF",
        "BANKID",
        "BRANCHID",
        "ACCTID",
        "ACCTTYPE",
        "FID",
        "ORG",
        "BALAMT",
        "DTASOF",
    }
    position = 0
    nodes = 0
    for match in token.finditer(value):
        if match.start() != position:
            raise ValidationError("Нарушена структура OFX 1.x.")
        position = match.end()
        if match[3] is not None:
            text_value = match[3].strip()
            if text_value:
                if not stack or len(text_value) > 4096:
                    raise ValidationError("Некорректный текст OFX 1.x.")
                stack[-1].text = (stack[-1].text or "") + unescape(text_value)
            continue
        name = match[2].upper()
        closing = bool(match[1])
        if (
            stack
            and stack[-1].tag != name
            and (stack[-1].tag in scalar or (stack[-1].text or "").strip())
        ):
            stack.pop()
        if closing:
            if not stack or stack[-1].tag != name:
                raise ValidationError("Нарушен порядок закрытия элементов OFX 1.x.")
            stack.pop()
        else:
            element = ET.Element(name)
            if stack:
                stack[-1].append(element)
            elif root is None:
                root = element
            else:
                raise ValidationError("В файле OFX несколько корневых элементов.")
            stack.append(element)
            nodes += 1
            if len(stack) > 32 or nodes > 100000:
                raise ValidationError("Слишком сложный файл OFX.")
    if position != len(value) or stack or root is None:
        raise ValidationError("Файл OFX 1.x не завершён.")
    return root


def _field(element: ET.Element, name: str, *, required: bool = True) -> str:
    values = element.findall(name)
    if len(values) > 1 or (required and len(values) != 1) or any(len(v) for v in values):
        raise ValidationError("Отсутствует или повторяется обязательное поле OFX.")
    result = (values[0].text or "").strip() if values else ""
    if required and not result:
        raise ValidationError("Обязательное поле OFX пустое.")
    return result


def _ofx_date(value: str) -> date:
    match = re.fullmatch(
        r"([0-9]{8})([0-9]{2}(?:[0-9]{2})?(?:[0-9]{2})?)?(?:\.[0-9]{1,6})?(?:\[([+-]?[0-9]{1,2}(?:\.[0-9]+)?)(?::[A-Za-z0-9_+-]{1,16})?\])?",
        value,
    )
    if match is None or (match[3] is not None and abs(Decimal(match[3])) > 24):
        raise ValidationError("Неверная дата OFX.")
    if "." in value.split("[")[0] and (match[2] is None or len(match[2]) != 6):
        raise ValidationError("Доли секунды OFX требуют полного времени.")
    day = datetime.strptime(match[1], "%Y%m%d").date()
    if match[2]:
        time = match[2].ljust(6, "0")
        datetime(day.year, day.month, day.day, int(time[:2]), int(time[2:4]), int(time[4:6]))
    return day


def parse_ofx(contents: bytes) -> ImportDocument:
    value = _ofx_text(contents)
    if re.search(r"<!DOCTYPE|<!ENTITY", value, re.IGNORECASE):
        raise ValidationError("DTD и объявления сущностей в OFX запрещены.")
    try:
        if re.search(r"(?im)^OFXHEADER:100\s*$", value):
            root = _sgml_tree(value)
        else:
            root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise ValidationError("Нарушена структура OFX XML.") from exc
    if root.tag != "OFX":
        raise ValidationError("Ожидается документ OFX без пространств имён.")
    pending: list[tuple[ET.Element, int]] = [(root, 1)]
    count = 0
    while pending:
        element, depth = pending.pop()
        count += 1
        if depth > 32 or count > 100000 or len(element.text or "") > 4096:
            raise ValidationError("Слишком сложный файл OFX или слишком длинное поле.")
        pending.extend((child, depth + 1) for child in element)
    for status in root.iter("STATUS"):
        if _field(status, "CODE") != "0":
            raise ValidationError("OFX содержит ответ банка с ошибкой.")
    statements = list(root.iter("STMTRS")) + list(root.iter("CCSTMTRS"))
    if len(statements) != 1:
        raise ValidationError("Нужна выписка ровно по одному банковскому или карточному счёту.")
    statement = statements[0]
    if _field(statement, "CURDEF") != "RUB":
        raise ValidationError("Импорт поддерживает RUB; конвертация валют не выполняется.")
    accounts = statement.findall("BANKACCTFROM") + statement.findall("CCACCTFROM")
    lists = statement.findall("BANKTRANLIST")
    if len(accounts) != 1 or len(lists) != 1:
        raise ValidationError("OFX требует один счёт и один список проведённых операций.")
    account = accounts[0]
    scope = {
        name: _field(account, name, required=name == "ACCTID")
        for name in ("BANKID", "BRANCHID", "ACCTID", "ACCTTYPE")
    }
    scope["statement_kind"] = statement.tag
    scope["fid"] = ";".join(_field(fi, "FID", required=False) for fi in root.iter("FI"))
    transactions = lists[0].findall("STMTTRN")
    if len(transactions) != len(list(root.iter("STMTTRN"))) or len(transactions) > MAX_IMPORT_ROWS:
        raise ValidationError("Операции OFX расположены неверно или их больше 2000.")
    rows: list[ImportRow] = []
    issues: list[ImportIssue] = []
    for position, entry in enumerate(transactions, 1):
        try:
            if entry.find("CORRECTFITID") is not None or entry.find("CORRECTACTION") is not None:
                raise ValidationError(
                    "Банковская корректировка требует ручной проверки; "
                    "автоматически не импортируется."
                )
            if entry.find("CURRENCY") is not None or entry.find("ORIGCURRENCY") is not None:
                raise ValidationError("Строки с дополнительной валютой требуют ручной проверки.")
            kind = _field(entry, "TRNTYPE")
            note = "\n".join(
                s
                for s in (
                    _field(entry, "NAME", required=False),
                    _field(entry, "MEMO", required=False),
                )
                if s
            )
            rows.append(
                ImportRow(
                    position,
                    _ofx_date(_field(entry, "DTPOSTED")),
                    _amount(_field(entry, "TRNAMT"), "."),
                    note,
                    _field(entry, "FITID"),
                    kind == "XFER",
                )
            )
        except (ValidationError, ValueError) as exc:
            issues.append(
                ImportIssue(
                    position,
                    str(exc)
                    if isinstance(exc, ValidationError)
                    else "Недопустимая календарная дата или время OFX.",
                )
            )
    warnings = [
        "Дата операции берётся в календаре выписки, без пересчёта в часовой пояс компьютера. "
        "Начальные остатки из OFX не создаются."
    ]
    if list(root.iter("STMTTRNP")):
        warnings.append(
            "Ожидающие операции STMTTRNP не импортируются: это ещё не проведённые движения."
        )
    return ImportDocument(
        ImportKind.OFX,
        hashlib.sha256(contents).hexdigest(),
        digest_json(scope),
        tuple(rows),
        tuple(issues),
        tuple(warnings),
    )
