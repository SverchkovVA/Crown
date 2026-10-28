"""Ограниченная загрузка PNG/JPEG и нормализация в памяти; исходник не меняется."""

import hashlib
import stat
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader, QImageWriter, QPainter

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import (
    MAX_OCR_IMAGE_BYTES,
    LoadedOcrImage,
    OcrImage,
    require_dimensions,
)

_ERROR = "Не удалось прочитать PNG/JPEG для OCR. Проверьте формат, размер и доступ к файлу."


def decode_ocr_image(contents: bytes) -> LoadedOcrImage:
    if not isinstance(contents, bytes) or not 1 <= len(contents) <= MAX_OCR_IMAGE_BYTES:
        raise ValidationError(_ERROR)
    source = QBuffer()
    source.setData(contents)
    if not source.open(QIODevice.OpenModeFlag.ReadOnly):
        raise ValidationError(_ERROR)
    try:
        reader = QImageReader(source)
        reader.setAutoTransform(True)
        if (
            bytes(reader.format().data()).lower() not in (b"png", b"jpeg")
            or reader.imageCount() != 1
        ):
            raise ValidationError(_ERROR)
        size = reader.size()
        require_dimensions(size.width(), size.height())
        decoded = reader.read()
        if decoded.isNull():
            raise ValidationError(_ERROR)
        require_dimensions(decoded.width(), decoded.height())
        # Удаляем EXIF/текстовые поля и переводим прозрачность на белый фон.
        normalized = QImage(decoded.size(), QImage.Format.Format_RGB32)
        # Qt defaults differ with/without QGuiApplication (96/100 DPI).
        # Keep OCR input and its hash independent of display scale and process state.
        normalized.setDotsPerMeterX(3937)
        normalized.setDotsPerMeterY(3937)
        normalized.fill("white")
        painter = QPainter(normalized)
        try:
            painter.drawImage(0, 0, decoded)
        finally:
            painter.end()
        target = QBuffer()
        if not target.open(QIODevice.OpenModeFlag.WriteOnly) or not QImageWriter(
            target, b"PNG"
        ).write(normalized):
            raise ValidationError(_ERROR)
        image = OcrImage(target.data().data(), normalized.width(), normalized.height())
        return LoadedOcrImage(hashlib.sha256(contents).hexdigest(), image)
    finally:
        source.close()


def load_ocr_image(path: Path) -> LoadedOcrImage:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_OCR_IMAGE_BYTES:
            raise ValidationError(_ERROR)
        with path.open("rb") as stream:
            contents = stream.read(MAX_OCR_IMAGE_BYTES + 1)
    except OSError:
        raise ValidationError(_ERROR) from None
    return decode_ocr_image(contents)
