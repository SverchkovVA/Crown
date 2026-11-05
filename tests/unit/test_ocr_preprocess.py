from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from personal_finance.domain.ocr import OcrImage
from personal_finance.infrastructure.ocr import preprocess
from personal_finance.infrastructure.ocr.process import OcrProcessError


def picture(background=24, foreground=255, *, mode="L", half=False):
    pixels = Image.new(mode, (100, 80), background)
    ImageDraw.Draw(pixels).rectangle((0, 0, 49 if half else 9, 79 if half else 9), fill=foreground)
    output = BytesIO()
    pixels.save(output, format="PNG")
    return OcrImage(output.getvalue(), 100, 80)


def test_dark_normalization_keeps_original_bytes_and_coordinate_geometry():
    source = picture()
    before = source.png, source.sha256
    contents, changed = preprocess.normalize_dark_png(source)
    assert changed and contents != source.png
    with Image.open(BytesIO(contents)) as pixels:
        assert pixels.size == (source.width, source.height)
        assert pixels.getpixel((0, 0)) == 0 and pixels.getpixel((99, 79)) == 255
    assert (source.png, source.sha256) == before


@pytest.mark.parametrize(
    ("background", "foreground", "half"),
    [(255, 0, False), (24, 24, False), (24, 70, False), (24, 255, True)],
)
def test_light_uniform_low_contrast_and_mixed_images_remain_unchanged(background, foreground, half):
    source = picture(background, foreground, half=half)
    contents, changed = preprocess.normalize_dark_png(source)
    assert not changed and contents is source.png


def test_alpha_does_not_expose_invisible_pixels():
    source = picture((0, 0, 0, 0), (255, 255, 255, 255), mode="RGBA")
    contents, changed = preprocess.normalize_dark_png(source)
    assert not changed and contents is source.png


def test_normalization_cannot_increase_process_input_beyond_limit(monkeypatch):
    source = picture()
    monkeypatch.setattr(preprocess, "MAX_OCR_IMAGE_BYTES", 1)
    contents, changed = preprocess.normalize_dark_png(source)
    assert not changed and contents is source.png


def test_invalid_png_fails_without_leaking_its_contents():
    from tests.ocr_fixtures import image

    with pytest.raises(OcrProcessError, match="Не удалось подготовить"):
        preprocess.normalize_dark_png(image())
