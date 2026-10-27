import csv
import io
import json
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.exporting import (
    ExportFormat,
    ExportLabels,
    ExportPreview,
    ExportSettings,
    prepare_journal_export,
    serialize_journal_export,
)
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import MAX_MINOR, Money
from personal_finance.domain.query import OperationQuery


def uid():
    return str(uuid4())


def operation(*, minor=100, note="Проверочное описание", kind=OperationKind.EXPENSE):
    return Operation(
        uid(),
        uid(),
        OperationDraft(
            kind,
            date(2026, 1, 15),
            uid(),
            Money(minor),
            uid() if kind != OperationKind.OPENING else None,
            note=note,
        ),
    )


def test_default_export_excludes_notes_tags_and_filter_text_even_when_used_for_selection():
    secret = "ПРОВЕРОЧНОЕ_ПРИМЕЧАНИЕ"
    value = operation(note=secret)
    value = replace(value, draft=replace(value.draft, tag_ids=(uid(),)))
    preview = prepare_journal_export((value,), ExportSettings(OperationQuery(text=secret)))
    for format in ExportFormat:
        contents = serialize_journal_export(preview, format)
        assert secret.encode() not in contents
        assert value.draft.tag_ids[0].encode() not in contents
        assert (
            b"password" not in contents and b"PF1-" not in contents and b"key.json" not in contents
        )
    assert preview.operations == (value,)


@pytest.mark.parametrize("minor", [0, 1, -1, MAX_MINOR, -MAX_MINOR])
def test_json_money_uses_exact_decimal_integer_strings(minor):
    value = operation(minor=minor, kind=OperationKind.OPENING)
    preview = prepare_journal_export((value,), ExportSettings())
    exported = json.loads(serialize_journal_export(preview, ExportFormat.JSON))
    row = exported["operations"][0]
    assert row["amount_minor"] == str(minor) and int(row["amount_minor"]) == minor
    assert row["currency_exponent"] == 2 and row["currency"] == "RUB"
    assert exported["money_encoding"] == "integer_minor_decimal_string"
    assert value.draft.amount == Money(minor)


@pytest.mark.parametrize(
    "note", ["=1+2", "+SUM(A1:A2)", "-SUM(A1:A2)", "@test", "  =1+2", "\ufeff=1+2", "\n=1+2"]
)
def test_csv_text_cannot_become_formula_but_json_keeps_original(note):
    preview = prepare_journal_export((operation(note=note),), ExportSettings(include_notes=True))
    raw = serialize_journal_export(preview, ExportFormat.CSV)
    assert raw.startswith(b"\xef\xbb\xbf")
    row = next(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
    assert row["Примечание"] == "'" + note and row["Сумма, мин. ед."] == "100"
    assert (
        json.loads(serialize_journal_export(preview, ExportFormat.JSON))["operations"][0]["note"]
        == note
    )


def test_csv_quotes_multiline_and_preserves_parts_tags_and_exact_values():
    value = operation(note='Первая;строка\nВторая "строка"')
    value = replace(
        value,
        draft=replace(
            value.draft,
            category_id=None,
            parts=(OperationPart(uid(), Money(40)), OperationPart(uid(), Money(60))),
            tag_ids=(uid(),),
        ),
    )
    preview = prepare_journal_export(
        (value,), ExportSettings(include_notes=True, include_tags=True)
    )
    row = next(
        csv.DictReader(
            io.StringIO(serialize_journal_export(preview, ExportFormat.CSV).decode("utf-8-sig")),
            delimiter=";",
        )
    )
    assert row["Примечание"] == value.draft.note
    assert [part["amount_minor"] for part in json.loads(row["Части JSON"])] == ["40", "60"]
    assert json.loads(row["ID тегов JSON"]) == list(value.draft.tag_ids)
    assert preview.rows()[0]["parts"][0]["amount_minor"] == "40"


def test_filters_preserve_whole_transfer_and_do_not_modify_input():
    account, destination, bank = uid(), uid(), uid()
    transfer = Operation(
        uid(),
        uid(),
        OperationDraft(
            OperationKind.TRANSFER,
            date(2026, 1, 15),
            account,
            Money(100),
            destination_account_id=destination,
        ),
    )
    value = operation()
    source = (value, transfer)
    settings = ExportSettings(OperationQuery(account_ids=(destination,), institution_ids=(bank,)))
    with pytest.raises(ValidationError):
        prepare_journal_export(source, settings)
    preview = prepare_journal_export(source, settings, {destination: bank})
    assert preview.operations == (transfer,)
    assert preview.rows()[0]["account_id"] == account
    assert source == (value, transfer)


def test_deleted_rows_require_explicit_filter_and_duplicate_versions_are_rejected():
    value = operation()
    deleted = replace(value, is_deleted=True)
    assert not prepare_journal_export((deleted,), ExportSettings()).operations
    assert prepare_journal_export(
        (deleted,), ExportSettings(OperationQuery(include_deleted=True))
    ).operations == (deleted,)
    with pytest.raises(ValidationError):
        prepare_journal_export((value, replace(value, revision_id=uid())), ExportSettings())


def test_empty_export_has_headers_and_stable_nonfinancial_metadata():
    preview = prepare_journal_export((), ExportSettings())
    assert serialize_journal_export(preview, ExportFormat.JSON) == serialize_journal_export(
        preview, ExportFormat.JSON
    )
    assert json.loads(serialize_journal_export(preview, ExportFormat.JSON))["operations"] == []
    assert (
        len(serialize_journal_export(preview, ExportFormat.CSV).decode("utf-8-sig").splitlines())
        == 1
    )


def test_mutable_or_malformed_snapshot_cannot_bypass_preparation():
    with pytest.raises(ValidationError):
        ExportPreview([operation()], ExportSettings())
    invalid = Operation(uid(), uid(), "не финансовая операция")
    with pytest.raises(ValidationError):
        prepare_journal_export((invalid,), ExportSettings())


def test_names_are_optional_and_csv_names_cannot_become_formulas():
    value = operation()
    secret_name = "=ПРОВЕРОЧНОЕ_ИМЯ"
    labels = ExportLabels(accounts=((value.draft.account_id, secret_name),))
    preview = replace(prepare_journal_export((value,), ExportSettings()), labels=labels)
    row = next(
        csv.DictReader(
            io.StringIO(serialize_journal_export(preview, ExportFormat.CSV).decode("utf-8-sig")),
            delimiter=";",
        )
    )
    assert row["Счёт"] == "'" + secret_name
    assert row["Сумма"] == "1,00" and row["Вид операции"] == "Расход"
    private = replace(preview, settings=ExportSettings(include_names=False), source_book_id=uid())
    for format in ExportFormat:
        raw = serialize_journal_export(private, format)
        assert secret_name.encode() not in raw
        assert private.source_book_id.encode() not in raw
    assert (
        json.loads(serialize_journal_export(preview, ExportFormat.JSON))["operations"][0][
            "account_name"
        ]
        == secret_name
    )


def test_export_labels_are_immutable_and_unselected_labels_do_not_leak():
    value = operation()
    unrelated = "НЕ_ВЫБРАННОЕ_НАЗВАНИЕ"
    labels = ExportLabels(accounts=((uid(), unrelated),))
    preview = replace(prepare_journal_export((value,), ExportSettings()), labels=labels)
    assert unrelated.encode() not in serialize_journal_export(preview, ExportFormat.JSON)
    with pytest.raises(ValidationError):
        ExportLabels(accounts=[(uid(), unrelated)])
