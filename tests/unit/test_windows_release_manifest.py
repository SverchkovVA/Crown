from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from tools.windows_release_manifest import main, verify_installer, verify_manifest

_NOW = datetime(2026, 11, 16, tzinfo=UTC)
_SAMPLE_INSTALLER = b"fictional installer for test"


def _manifest(**changes: object) -> bytes:
    return json.dumps(
        {
            "format": 1,
            "platform": "windows-x64",
            "version": "0.10.0",
            "expires_utc": "2026-12-01T00:00:00Z",
            "installer_bytes": len(_SAMPLE_INSTALLER),
            "installer_sha256": hashlib.sha256(_SAMPLE_INSTALLER).hexdigest(),
            "source_sha256": "5" * 64,
        }
        | changes
    ).encode()


def _verify(payload: bytes, minimum: str = "0.9.0", now: datetime = _NOW):
    key = SigningKey.generate()
    return verify_manifest(
        payload,
        key.sign(payload).signature,
        bytes(key.verify_key),
        minimum_version=minimum,
        now=now,
    )


def test_checks_signed_installer(tmp_path: Path) -> None:
    release = _verify(_manifest())
    installer = tmp_path / release.filename
    installer.write_bytes(_SAMPLE_INSTALLER)
    verify_installer(installer, release)
    assert release.version == "0.10.0"
    assert release.source_sha256 == "5" * 64
    assert release.expires_at.utcoffset() == timedelta(0)


def test_rejects_changed_signed_manifest_and_different_key() -> None:
    key = SigningKey.generate()
    other = SigningKey.generate()
    encoded = _manifest()
    signature = key.sign(encoded).signature
    with pytest.raises(ValueError, match="Подпись"):
        verify_manifest(
            _manifest(version="0.11.0"),
            signature,
            bytes(key.verify_key),
            minimum_version="0.9.0",
            now=_NOW,
        )
    with pytest.raises(ValueError, match="Подпись"):
        verify_manifest(
            encoded,
            signature,
            bytes(other.verify_key),
            minimum_version="0.9.0",
            now=_NOW,
        )


@pytest.mark.parametrize("minimum", ["0.10.0", "0.11.1"])
def test_rejects_rollback(minimum: str) -> None:
    with pytest.raises(ValueError, match="должна быть новее"):
        _verify(_manifest(), minimum=minimum)


@pytest.mark.parametrize("now", [_NOW, _NOW + timedelta(days=1)])
def test_rejects_expired_manifest(now: datetime) -> None:
    with pytest.raises(ValueError, match="истёк"):
        _verify(_manifest(expires_utc="2026-11-16T00:00:00Z"), now=now)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ({"platform": "windows-arm64"}, "платформа"),
        ({"format": True}, "Формат"),
        ({"installer_bytes": True}, "размер"),
        ({"installer_bytes": 0}, "размер"),
        ({"installer_sha256": "bad"}, "SHA-256"),
        ({"source_sha256": "A" * 64}, "SHA-256"),
        ({"version": "0.10.0.dev129"}, "стабильного выпуска"),
        ({"version": "00.10.0"}, "стабильного выпуска"),
        ({"expires_utc": "2026-02-30T00:00:00Z"}, "неверный срок"),
        ({"download_url": "https://example.test/tool.exe"}, "набор полей"),
    ],
)
def test_rejects_signed_but_unsupported_fields(
    replacement: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _verify(_manifest(**replacement))


def test_rejects_duplicate_json_fields_and_oversized_record() -> None:
    raw = _manifest()
    with pytest.raises(ValueError, match="повторяется поле"):
        _verify(b'{"version":"0.19.0",' + raw[1:])
    with pytest.raises(ValueError, match="Неверный размер"):
        _verify(raw + b" " * 4096)


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("other.exe", _SAMPLE_INSTALLER, "Имя"),
        ("Crown-0.10.0-windows-x64-setup.exe", b"tiny", "Размер"),
        ("Crown-0.10.0-windows-x64-setup.exe", b"." * len(_SAMPLE_INSTALLER), "SHA-256"),
    ],
)
def test_rejects_different_installer(
    tmp_path: Path, filename: str, content: bytes, message: str
) -> None:
    installer = tmp_path / filename
    installer.write_bytes(content)
    with pytest.raises(ValueError, match=message):
        verify_installer(installer, _verify(_manifest()))


def test_local_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    timestamp = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    encoded = _manifest(expires_utc=timestamp)
    key = SigningKey.generate()
    manifest = tmp_path / "release.json"
    signature = tmp_path / "release.sig"
    public = tmp_path / "publisher.pub"
    installer = tmp_path / "Crown-0.10.0-windows-x64-setup.exe"
    manifest.write_bytes(encoded)
    signature.write_bytes(key.sign(encoded).signature)
    public.write_bytes(bytes(key.verify_key))
    installer.write_bytes(_SAMPLE_INSTALLER)
    monkeypatch.setattr(
        "sys.argv",
        [
            "test",
            "--manifest",
            str(manifest),
            "--signature",
            str(signature),
            "--public-key",
            str(public),
            "--minimum-version",
            "0.9.0",
            "--installer",
            str(installer),
        ],
    )
    assert main() == 0
    assert "0.10.0" in capsys.readouterr().out
