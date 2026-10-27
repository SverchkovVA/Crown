"""Загрузчик использует только вымышленные локальные изображения."""

import hashlib
import struct
import zlib

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage, QImageReader

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import MAX_OCR_IMAGE_BYTES
from personal_finance.infrastructure.ocr.images import decode_ocr_image, load_ocr_image


def encode(image, format="PNG"):
    output = QBuffer()
    assert output.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(output, format)
    return output.data().data()


def picture():
    image = QImage(30, 20, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 0, 0, 0))
    image.setPixelColor(4, 5, QColor("blue"))
    image.setText("Описание", "вымышленная секретная заметка")
    return image


def test_png_is_normalized_in_memory_without_metadata_and_transparency():
    source = encode(picture())
    result = decode_ocr_image(source)
    assert result.source_sha256 == hashlib.sha256(source).hexdigest()
    assert result.image.png != source
    decoded = QImage.fromData(result.image.png)
    assert (decoded.width(), decoded.height()) == (30, 20)
    assert decoded.pixelColor(0, 0) == QColor("white")
    assert decoded.pixelColor(4, 5) == QColor("blue")
    assert decoded.textKeys() == []
    assert "заметка".encode() not in result.image.png


def test_jpeg_is_decoded_with_distinct_source_and_normalized_fingerprint():
    source = encode(picture(), "JPEG")
    result = decode_ocr_image(source)
    assert result.image.png.startswith(b"\x89PNG")
    assert result.image.sha256 != result.source_sha256


def test_file_loading_preserves_original_and_writes_no_additional_file(tmp_path):
    path = tmp_path / "тест.jpg"
    source = encode(picture(), "JPEG")
    path.write_bytes(source)
    result = load_ocr_image(path)
    assert result.source_sha256 == hashlib.sha256(source).hexdigest()
    assert path.read_bytes() == source
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "contents",
    [b"", b"bad data", b"\xff\xd8broken", b"0" * (MAX_OCR_IMAGE_BYTES + 1)],
    ids=["empty", "invalid", "broken-jpeg", "oversized"],
)
def test_invalid_and_oversized_contents_are_rejected(contents):
    with pytest.raises(ValidationError):
        decode_ocr_image(contents)


def test_other_qt_image_formats_are_rejected_instead_of_sniffing_every_plugin():
    if b"bmp" not in [v.data() for v in QImageReader.supportedImageFormats()]:
        pytest.skip("BMP отсутствует")
    with pytest.raises(ValidationError):
        decode_ocr_image(encode(picture(), "BMP"))


def test_png_huge_dimensions_are_rejected_before_full_decode():
    contents = bytearray(encode(picture()))
    contents[16:24] = struct.pack(">II", 100_000, 100_000)
    contents[29:33] = struct.pack(">I", zlib.crc32(contents[12:29]))
    with pytest.raises(ValidationError):
        decode_ocr_image(bytes(contents))


def test_missing_file_directory_and_symlink_are_rejected_without_path_disclosure(tmp_path):
    for path in (tmp_path / "приватное-имя.png", tmp_path):
        with pytest.raises(ValidationError) as error:
            load_ocr_image(path)
        assert str(path) not in str(error.value)
    source = tmp_path / "source.png"
    source.write_bytes(encode(picture()))
    linked = tmp_path / "linked.png"
    try:
        linked.symlink_to(source)
    except OSError:
        return  # Windows без Developer Mode не даёт создавать symlink.
    with pytest.raises(ValidationError):
        load_ocr_image(linked)


def test_jpeg_exif_rotation_is_applied_before_ocr():
    jpeg = encode(picture(), "JPEG")
    tiff = b"II\x2a\x00\x08\x00\x00\x00\x01\x00"
    tiff += struct.pack("<HHI", 0x112, 3, 1) + b"\x06\x00\x00\x00" + b"\0" * 4
    exif = b"Exif\0\0" + tiff
    rotated = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
    result = decode_ocr_image(rotated)
    assert (result.image.width, result.image.height) == (20, 30)
