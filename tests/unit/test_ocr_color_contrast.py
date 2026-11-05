"""Цветной текст на тёмной теме должен оставаться видимым для OCR."""

from io import BytesIO

from PIL import Image, ImageDraw

from personal_finance.domain.ocr import OcrImage
from personal_finance.infrastructure.ocr.preprocess import normalize_dark_png


def picture(background):
    pixels = Image.new("RGB", (100, 80), background)
    painter = ImageDraw.Draw(pixels)
    painter.rectangle((0, 0, 9, 9), fill=(255, 255, 255))
    painter.rectangle((20, 20, 49, 39), fill=(2, 134, 79))
    target = BytesIO()
    pixels.save(target, format="PNG")
    return OcrImage(target.getvalue(), 100, 80)


def test_green_text_keeps_stronger_ink_contrast_and_original_bytes():
    source = picture((24, 24, 24))
    original = source.png, source.sha256
    normalized, changed = normalize_dark_png(source)
    assert changed
    with Image.open(BytesIO(normalized)) as result:
        assert result.size == (100, 80)
        assert result.getpixel((0, 0)) == 0
        assert result.getpixel((99, 79)) == 255
        assert result.getpixel((25, 25)) <= 150
    assert (source.png, source.sha256) == original


def test_saturated_background_does_not_flatten_white_text_to_uniform_pixels():
    source = picture((0, 0, 255))
    normalized, changed = normalize_dark_png(source)
    assert changed
    with Image.open(BytesIO(normalized)) as result:
        assert result.getpixel((0, 0)) == 0
        assert result.getpixel((99, 79)) == 255
