"""Сборка локального OCR из доверенного манифеста приложения, без поиска в PATH."""

import json
import sys
from pathlib import Path

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import OcrEngineInfo, require_sha256
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.infrastructure.ocr.images import load_ocr_image
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from personal_finance.infrastructure.ocr.tesseract import TesseractEngine, TesseractRuntime
from personal_finance.infrastructure.ocr.vtb import VtbCardAdapter
from personal_finance.infrastructure.ocr.vtb_history import VtbHistoryAdapter

_ERROR = "Не удалось подключить проверенный комплект локального распознавания."
MAX_MANIFEST_BYTES = 128 * 1024
FileHashes = tuple[tuple[str, str], ...]


def trusted_manifest_path() -> Path:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if not isinstance(base, str):
            raise ValidationError(_ERROR)
        return Path(base) / "ocr" / "runtime-candidate.json"
    return Path(__file__).resolve().parents[3] / "packaging" / "ocr" / "runtime-candidate.json"


def _pairs(value: object) -> FileHashes:
    if not isinstance(value, list) or not 1 <= len(value) <= 150:
        raise ValidationError(_ERROR)
    pairs = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(v, str) for v in item)
        ):
            raise ValidationError(_ERROR)
        name, digest = item
        require_sha256(digest)
        pairs.append((name, digest))
    return tuple(pairs)


def load_runtime_manifest() -> tuple[OcrEngineInfo, FileHashes]:
    try:
        with trusted_manifest_path().open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValidationError(_ERROR)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or type(data.get("format")) is not int or data["format"] != 1:
            raise ValidationError(_ERROR)
        info = OcrEngineInfo("tesseract", data["version"], _pairs(data["models"]))
        files = _pairs(data["file_sha256"])
        names = dict(files)
        if {n for n, _ in info.model_sha256} != {"rus", "eng"} or "tesseract.exe" not in names:
            raise ValidationError(_ERROR)
        if any(
            names.get(f"tessdata/{name}.traineddata") != digest
            for name, digest in info.model_sha256
        ):
            raise ValidationError(_ERROR)
        return info, files
    except (OSError, ValueError, TypeError, KeyError, RecursionError, ValidationError):
        raise ValidationError(_ERROR) from None


def runtime_directory(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit.absolute()
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).absolute().parent / "ocr-runtime"
        if candidate.exists() or candidate.is_symlink():
            return candidate
    return None


def create_runtime(directory: Path) -> TesseractRuntime:
    info, files = load_runtime_manifest()
    # Не разыменовываем пользовательский путь до проверки ссылок в verify().
    runtime = TesseractRuntime(directory.absolute(), info, files)
    runtime.verify()
    return runtime


def recognitions_for_directory(directory: Path) -> tuple[CardRecognition, ...]:
    runtime = create_runtime(directory)
    ocr = Ocr(TesseractEngine(runtime))
    fallback = Ocr(TesseractEngine(runtime, page_segmentation=11))
    return (
        CardRecognition(
            ocr,
            load_ocr_image,
            GenericCardAdapter(),
            history_adapter=GenericHistoryAdapter(),
            bank_name=UNIVERSAL_PROFILE,
            fallback_ocr=fallback,
            auto_history_hint=GenericHistoryAdapter.has_heading,
            retry_missing_card=GenericCardAdapter.can_retry,
        ),
        CardRecognition(
            ocr,
            load_ocr_image,
            TBankCardAdapter(),
            history_adapter=TBankHistoryAdapter(),
            fallback_ocr=fallback,
        ),
        CardRecognition(
            ocr,
            load_ocr_image,
            VtbCardAdapter(),
            history_adapter=VtbHistoryAdapter(),
            bank_name="ВТБ",
            fallback_ocr=fallback,
        ),
    )
