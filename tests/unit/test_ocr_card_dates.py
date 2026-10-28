from datetime import date, time

import pytest

from personal_finance.application.ocr_review import OcrReviewDefaults, suggest_corrections
from personal_finance.domain.ocr_cards import OcrIssue
from tests.ocr_fixtures import LINES, card


@pytest.mark.parametrize("prefix", ["< ", "‹ ", "← ", "> ", "‘> ", "<", "’>"])
@pytest.mark.parametrize("year", ["", " 2024"])
def test_navigation_icon_next_to_date_keeps_exact_source_and_proposes_only_missing_year(
    prefix, year
):
    source = card((*LINES[:3], prefix + "24 октября" + year + ", 13:42", *LINES[4:]))
    proposal = suggest_corrections(source, OcrReviewDefaults(reference_year=2026))
    assert not source.blocked_reason
    assert source.occurred_on.word_indices == (3,)
    assert source.local_time.value == time(13, 42)
    assert source.document.words[3].text.startswith(prefix)
    if year:
        assert source.occurred_on.value == date(2024, 10, 24)
        assert proposal.occurred_on == "24.10.2024"
    else:
        assert source.occurred_on.value is None
        assert OcrIssue.MISSING in source.occurred_on.issues
        assert proposal.occurred_on == "24.10.2026"


@pytest.mark.parametrize(
    "value",
    [
        "Телефон 24 октября, 13:42",
        "< 24 октября, 13:42 неизвестный текст",
        "< 24 октября / 25 октября",
        "< 31 февраля, 13:42",
        "< Сегодня, 13:42",
        "- 24 октября, 13:42",
        "15:10 < 24 октября, 13:42",
        "<< 24 октября, 13:42",
        "<> 24 октября, 13:42",
    ],
)
def test_icon_does_not_turn_partial_relative_or_contradictory_text_into_a_complete_date(value):
    source = card((*LINES[:3], value, *LINES[4:]))
    assert not suggest_corrections(source, OcrReviewDefaults(reference_year=2026)).occurred_on


def test_completed_status_is_still_required_with_navigation_date():
    source = card((*LINES[:3], "< 24 октября, 13:42", "В обработке"))
    assert source.blocked_reason


def test_invalid_time_is_not_invented_but_a_valid_calendar_date_can_be_proposed():
    source = card((*LINES[:3], "< 24 октября, 25:42", *LINES[4:]))
    proposal = suggest_corrections(source, OcrReviewDefaults(reference_year=2026))
    assert proposal.occurred_on == "24.10.2026" and proposal.local_time == ""
    assert source.local_time.value is None and OcrIssue.INVALID in source.local_time.issues
