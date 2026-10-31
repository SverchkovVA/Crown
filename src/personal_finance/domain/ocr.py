"""Неизменяемый результат локального OCR; не является финансовой операцией."""

import hashlib
import re
import struct
from dataclasses import dataclass, field
from decimal import Decimal

from personal_finance.domain.errors import ValidationError

MAX_OCR_IMAGE_BYTES = 12 * 1024 * 1024
MAX_OCR_DIMENSION = 10_000
MAX_OCR_PIXELS = 20_000_000
MAX_OCR_WORDS = 10_000
MAX_OCR_TSV_BYTES = 4 * 1024 * 1024


def require_sha256(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValidationError("Некорректный отпечаток OCR.")


def require_dimensions(width: int, height: int) -> None:
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= MAX_OCR_DIMENSION
        or not 1 <= height <= MAX_OCR_DIMENSION
        or width * height > MAX_OCR_PIXELS
    ):
        raise ValidationError("Изображение OCR превышает допустимые размеры.")


@dataclass(frozen=True, slots=True)
class OcrImage:
    """Нормализованный PNG в памяти. Полная декодировка — обязанность загрузчика."""

    png: bytes = field(repr=False)
    width: int
    height: int

    def __post_init__(self) -> None:
        require_dimensions(self.width, self.height)
        if (
            not isinstance(self.png, bytes)
            or not 33 <= len(self.png) <= MAX_OCR_IMAGE_BYTES
            or self.png[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            or struct.unpack(">II", self.png[16:24]) != (self.width, self.height)
        ):
            raise ValidationError("Для OCR требуется ограниченное изображение PNG.")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.png).hexdigest()


@dataclass(frozen=True, slots=True)
class OcrBox:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if (
            any(type(v) is not int for v in (self.left, self.top, self.width, self.height))
            or self.left < 0
            or self.top < 0
            or self.width <= 0
            or self.height <= 0
        ):
            raise ValidationError("Некорректная область распознанного слова.")

    def fits(self, width: int, height: int) -> bool:
        return self.left + self.width <= width and self.top + self.height <= height


@dataclass(frozen=True, slots=True)
class OcrWord:
    text: str = field(repr=False)
    box: OcrBox
    confidence: Decimal
    line_key: tuple[int, int, int]
    word_number: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.text, str)
            or not self.text.strip()
            or len(self.text) > 500
            or any(not c.isprintable() and c not in "\u00a0\u202f" for c in self.text)
            or not isinstance(self.box, OcrBox)
            or not isinstance(self.confidence, Decimal)
            or not self.confidence.is_finite()
            or not Decimal(0) <= self.confidence <= Decimal(100)
            or not isinstance(self.line_key, tuple)
            or len(self.line_key) != 3
            or any(type(v) is not int or v <= 0 for v in self.line_key)
            or type(self.word_number) is not int
            or self.word_number <= 0
        ):
            raise ValidationError("Некорректные сведения распознанного слова.")


@dataclass(frozen=True, slots=True)
class OcrEngineInfo:
    name: str
    version: str
    model_sha256: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not isinstance(self.version, str)
            or not 1 <= len(self.name) <= 80
            or not 1 <= len(self.version) <= 80
            or not all(c.isprintable() for c in self.name + self.version)
            or not isinstance(self.model_sha256, tuple)
            or not 1 <= len(self.model_sha256) <= 8
        ):
            raise ValidationError("Некорректная версия локального OCR.")
        names = set()
        for item in self.model_sha256:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or re.fullmatch(r"[a-z]{3}", item[0]) is None
                or item[0] in names
            ):
                raise ValidationError("Некорректный набор моделей OCR.")
            require_sha256(item[1])
            names.add(item[0])


@dataclass(frozen=True, slots=True)
class OcrDocument:
    image_sha256: str
    width: int
    height: int
    engine: OcrEngineInfo
    words: tuple[OcrWord, ...] = field(repr=False)

    def __post_init__(self) -> None:
        require_sha256(self.image_sha256)
        require_dimensions(self.width, self.height)
        if (
            not isinstance(self.engine, OcrEngineInfo)
            or not isinstance(self.words, tuple)
            or len(self.words) > MAX_OCR_WORDS
            or any(
                not isinstance(w, OcrWord) or not w.box.fits(self.width, self.height)
                for w in self.words
            )
            or len({(w.line_key, w.word_number) for w in self.words}) != len(self.words)
        ):
            raise ValidationError("Некорректный документ OCR.")

    def lines(self) -> tuple[tuple[OcrWord, ...], ...]:
        """Порядок строк движка и порядок слов внутри строки; текст не исправляется."""
        groups: dict[tuple[int, int, int], list[OcrWord]] = {}
        for word in self.words:
            groups.setdefault(word.line_key, []).append(word)
        return tuple(tuple(sorted(words, key=lambda w: w.word_number)) for words in groups.values())


@dataclass(frozen=True, slots=True)
class LoadedOcrImage:
    source_sha256: str
    image: OcrImage

    def __post_init__(self) -> None:
        require_sha256(self.source_sha256)
        if not isinstance(self.image, OcrImage):
            raise ValidationError("Некорректное исходное изображение OCR.")
