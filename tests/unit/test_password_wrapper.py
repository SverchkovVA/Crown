"""Смена парольной обёртки на вымышленных ключах, без работы с файлами."""

import json
from uuid import UUID

import pytest

from personal_finance.domain.errors import InvalidCredentials, UnsupportedFormat, ValidationError
from personal_finance.infrastructure.security.keys import (
    canonical_json,
    make_manifest,
    replace_password_wrapper,
    unlock_manifest,
)

BOOK = str(UUID(int=10))
KEY = bytes(range(32))
OLD = " пароль теста "
NEW = "  一  "


@pytest.fixture
def sample():
    return make_manifest(BOOK, KEY, OLD)


def test_new_password_keeps_version_key_and_recovery_code(sample):
    source, code = sample
    changed = replace_password_wrapper(source, OLD, NEW, expected_book_id=BOOK, expected_key=KEY)
    before, after = json.loads(source), json.loads(changed)
    assert before.keys() == after.keys()
    assert after["format"] == 1 and after["book_id"] == BOOK
    assert after["kdf"]["salt"] != before["kdf"]["salt"]
    assert after["wrapped_key"] != before["wrapped_key"]
    assert after["recovery_wrapped_key"] == before["recovery_wrapped_key"]
    assert unlock_manifest(changed, NEW) == (BOOK, KEY)
    assert unlock_manifest(changed, code, recovery=True) == (BOOK, KEY)
    with pytest.raises(InvalidCredentials):
        unlock_manifest(changed, OLD)
    with pytest.raises(InvalidCredentials):
        unlock_manifest(changed, NEW.strip())


@pytest.mark.parametrize(
    ("current", "book_id", "key"),
    [(OLD.strip(), BOOK, KEY), (OLD, str(UUID(int=99)), KEY), (OLD, BOOK, bytes(32))],
)
def test_wrapper_requires_correct_current_secret_and_identity(sample, current, book_id, key):
    source, _ = sample
    with pytest.raises(InvalidCredentials):
        replace_password_wrapper(source, current, NEW, expected_book_id=book_id, expected_key=key)
    assert unlock_manifest(source, OLD) == (BOOK, KEY)


def test_rejects_empty_replacement_and_damaged_recovery_slot(sample):
    source, _ = sample
    with pytest.raises(ValidationError):
        replace_password_wrapper(source, OLD, "", expected_book_id=BOOK, expected_key=KEY)
    malformed = json.loads(source)
    malformed["recovery_wrapped_key"] = "broken"
    with pytest.raises(UnsupportedFormat):
        replace_password_wrapper(
            canonical_json(malformed), OLD, NEW, expected_book_id=BOOK, expected_key=KEY
        )
