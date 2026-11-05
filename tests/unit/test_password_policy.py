from uuid import uuid4

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.infrastructure.security.keys import (
    make_manifest,
    unlock_manifest,
    validate_password,
)


@pytest.mark.parametrize(
    "password",
    ["x", "я", "  x  ", "Ж" * 1500],
    ids=["one_char", "unicode_short", "spaces_preserved", "long_unicode"],
)
def test_short_and_long_passwords_wrap_and_unlock_without_normalization(password):
    identifier = str(uuid4())
    key = b"x" * 32
    manifest, recovery = make_manifest(identifier, key, password)
    assert unlock_manifest(manifest, password)[1] == key
    assert unlock_manifest(manifest, recovery, recovery=True)[1] == key


@pytest.mark.parametrize("password", ["", None])
def test_empty_field_is_rejected(password):
    with pytest.raises(ValidationError):
        validate_password(password)
