"""Подготовка закреплённых публичных материалов только для изолированной OCR-пробы."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import urlparse

MAX_DOWNLOAD = 32 * 1024 * 1024
WRAPPER = "15c534717b1cb58261b58d4e4c1200c7f81f668c"
MODELS = "65727574dfcd264acbb0c3e07860e4e9e9b22185"
SOURCE = f"https://raw.githubusercontent.com/adaptech-cz/Tesseract4Android/{WRAPPER}/"
CPP = SOURCE + "tesseract4android/src/main/cpp/"


@dataclass(frozen=True, slots=True)
class Artifact:
    name: str
    url: str
    sha256: str

    def __post_init__(self) -> None:
        parts = PurePosixPath(self.name)
        if (
            not self.name
            or parts.is_absolute()
            or ".." in parts.parts
            or "\\" in self.name
            or ":" in self.name
        ):
            raise ValueError("Небезопасное имя материала")
        parsed = urlparse(self.url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "jitpack.io",
            "raw.githubusercontent.com",
        }:
            raise ValueError("Неизвестный источник материала")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ValueError("Неверная контрольная сумма")


ARTIFACTS = (
    Artifact(
        "tesseract4android-4.9.0.aar",
        "https://jitpack.io/cz/adaptech/tesseract4android/tesseract4android/4.9.0/tesseract4android-4.9.0.aar",
        "bce5d6413a1a5ae3d7240033fbbc851ba3217d0a08d9769400e17a077f42cb2a",
    ),
    Artifact(
        "rus.traineddata",
        f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{MODELS}/rus.traineddata",
        "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
    ),
    Artifact(
        "eng.traineddata",
        f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{MODELS}/eng.traineddata",
        "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    ),
    Artifact(
        "licenses/wrapper.txt",
        SOURCE + "LICENSE",
        "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    ),
    Artifact(
        "licenses/tesseract.txt",
        CPP + "tesseract/src/LICENSE",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    ),
    Artifact(
        "licenses/leptonica.txt",
        CPP + "leptonica/src/leptonica-license.txt",
        "87829abb5bbb00b55a107365da89e9a33f86c4250169e5a1e5588505be7d5806",
    ),
    Artifact(
        "licenses/png.txt",
        CPP + "libpng/src/LICENSE",
        "16d9daaafbf63a31a5bdc91d4600972548fef5aaa1244202393288dbd079c49a",
    ),
    Artifact(
        "licenses/jpeg.txt",
        CPP + "libjpeg/src/README",
        "7c25493a9f64fed34d01445467341bda77bc1cdbeccbe33558659ef173fb9ff2",
    ),
    Artifact(
        "licenses/tessdata-fast.txt",
        f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{MODELS}/LICENSE",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    ),
)


def read_bounded(stream: BinaryIO, limit: int = MAX_DOWNLOAD) -> bytes:
    data = bytearray()
    while block := stream.read(min(65536, limit + 1 - len(data))):
        data.extend(block)
        if len(data) > limit:
            raise ValueError("Материал превышает допустимый размер")
    return bytes(data)


def download(artifact: Artifact) -> bytes:
    request = urllib.request.Request(
        artifact.url, headers={"User-Agent": "Crown-OCR-candidate-check"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        if urlparse(response.geturl()).scheme != "https":
            raise ValueError("Незащищённое перенаправление источника")
        return read_bounded(response)


def prepare(
    output: Path,
    *,
    artifacts: tuple[Artifact, ...] = ARTIFACTS,
    fetch: Callable[[Artifact], bytes] = download,
) -> None:
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Выходной каталог уже существует; перезапись запрещена")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="crown-ocr-inputs-", dir=output.parent))
    try:
        evidence = []
        for artifact in artifacts:
            content = fetch(artifact)
            if (
                not 0 < len(content) <= MAX_DOWNLOAD
                or hashlib.sha256(content).hexdigest() != artifact.sha256
            ):
                raise ValueError("Контрольная сумма материала не совпала: " + artifact.name)
            target = staging / artifact.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            evidence.append({"name": artifact.name, "url": artifact.url, "sha256": artifact.sha256})
        manifest = {
            "format": 1,
            "wrapper_commit": WRAPPER,
            "models_commit": MODELS,
            "files": evidence,
            "production_integration": False,
            "full_license_audit_complete": False,
            "binary_reproducibility_verified": False,
            "user_data_used": False,
        }
        (staging / "candidate-inputs.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if output.exists() or output.is_symlink():
            raise ValueError("Выходной каталог появился во время подготовки")
        staging.rename(output)
    finally:
        if staging.exists():
            if staging.resolve().parent != output.parent.resolve() or not staging.name.startswith(
                "crown-ocr-inputs-"
            ):
                raise RuntimeError("Нельзя очистить каталог вне временной подготовки")
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="Новый каталог публичных материалов"
    )
    args = parser.parse_args()
    prepare(args.output)
    print(
        "Материалы OCR-пробы подготовлены; полный аудит лицензий и происхождения ещё не завершён."
    )


if __name__ == "__main__":
    main()
