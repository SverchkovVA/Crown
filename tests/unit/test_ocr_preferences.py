from datetime import date

import pytest
from PySide6.QtCore import QSettings

from personal_finance.application.ocr_review import OcrReviewDefaults, suggest_corrections
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.infrastructure.ocr_preferences import LocalOcrPreferences
from personal_finance.infrastructure.persistence.ocr_settings_repository import (
    decode_preferences,
    encode_preferences,
)
from tests.ocr_fixtures import card


def test_partial_date_currency_defaults_do_not_change_raw_source():
    source = card(("Т-Банк", "Покупка", "-321,4 Р", "24 октября", "Описание: Вымышленная покупка"))
    value = suggest_corrections(source, OcrReviewDefaults(reference_year=2026))
    assert value.occurred_on == "24.10.2026" and value.currency == "RUB"
    assert source.occurred_on.value is None and source.currency.value is None
    assert value.amount == "321,40"


@pytest.mark.parametrize("year", [True, 0, 10000, "2026"])
def test_year_defaults_are_strict(year):
    with pytest.raises(ValidationError):
        OcrReviewDefaults(reference_year=year)


def test_party_rules_match_literal_name_tokens_and_preserve_unicode():
    preferences = OcrPreferences(transfer_parties=(TransferParty("Вымышленная Анна С."),))
    assert len(preferences.matching_parties("Перевод от ВЫМЫШЛЕННАЯ   АННА С.")) == 1
    assert not preferences.matching_parties("Вымышленная Анна С.тест")
    assert preferences.year == date.today().year


def test_settings_roundtrip_and_no_aliases_in_local_qsettings(tmp_path):
    value = OcrPreferences(
        confirm_fields=False,
        review_before_save=False,
        transfer_parties=(TransferParty("Вымышленный отправитель", exclude=True),),
    )
    assert decode_preferences(encode_preferences(value)) == value
    settings = QSettings(str(tmp_path / "local.ini"), QSettings.Format.IniFormat)
    local = LocalOcrPreferences(settings)
    local.save(value)
    loaded = local.load()
    assert not loaded.confirm_fields and not loaded.review_before_save
    assert loaded.transfer_parties == ()
    assert "отправитель" not in (tmp_path / "local.ini").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "field,value",
    [
        ("confirm_fields", 1),
        ("reference_year", True),
        ("transfer_parties", {}),
        ("default_currency", "UNKNOWN"),
    ],
)
def test_saved_preference_types_are_not_coerced(field, value):
    import json

    payload = json.loads(encode_preferences(OcrPreferences()))
    payload[field] = value
    with pytest.raises(ValidationError):
        decode_preferences(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


@pytest.mark.parametrize("sign", ["—", "–"])
def test_dash_in_exact_monetary_area_proposes_outflow_without_changing_raw_field(sign):
    from personal_finance.domain.ocr_cards import CashDirection

    source = card(("Т-Банк", "Покупка", sign + "321,4 Р", "24 октября"))
    value = suggest_corrections(source, OcrReviewDefaults(reference_year=2026))
    assert value.direction == CashDirection.OUT
    assert source.direction.value is None
