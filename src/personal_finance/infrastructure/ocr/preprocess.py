"""Нормализация тёмного PNG для OCR без изменения источника и координат."""

from io import BytesIO

from PIL import Image, ImageOps

from personal_finance.domain.ocr import MAX_OCR_IMAGE_BYTES, OcrImage
from personal_finance.infrastructure.ocr.process import OcrProcessError


def normalize_dark_png(image: OcrImage) -> tuple[bytes, bool]:
    try:
        with Image.open(BytesIO(image.png)) as pixels:
            if pixels.format != "PNG" or pixels.size != (image.width, image.height):
                raise ValueError("Несовпадение размеров PNG")
            # Прозрачность нормализует входной загрузчик. При прямом вызове движка
            # не превращаем невидимые RGB-значения в видимый текст.
            if "A" in pixels.getbands() or "transparency" in pixels.info:
                return image.png, False
            gray = pixels.convert("L")
        histogram = gray.histogram()
        if sum(histogram[:96]) * 4 < image.width * image.height * 3:
            return image.png, False
        levels = [index for index, count in enumerate(histogram) if count]
        if levels[-1] - levels[0] < 96:
            return image.png, False
        normalized = ImageOps.autocontrast(ImageOps.invert(gray))
        output = BytesIO()
        normalized.save(output, format="PNG")
        contents = output.getvalue()
        if len(contents) > MAX_OCR_IMAGE_BYTES:
            return image.png, False
        return contents, True
    except (OSError, ValueError, Image.DecompressionBombError):
        raise OcrProcessError("Не удалось подготовить изображение для локального OCR.") from None
