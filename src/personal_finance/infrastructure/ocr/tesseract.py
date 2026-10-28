"""Явно настроенный локальный Tesseract. Нет поиска в PATH или загрузок по сети."""

import hashlib
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import OcrDocument, OcrEngineInfo, OcrImage, require_sha256
from personal_finance.infrastructure.ocr.process import OcrProcessError, run_ocr_process
from personal_finance.infrastructure.ocr.tsv import parse_tesseract_tsv

_ERROR = "Комплект локального OCR отсутствует, изменён или настроен неверно."


@dataclass(frozen=True, slots=True)
class TesseractRuntime:
    directory: Path
    engine: OcrEngineInfo
    file_sha256: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.directory, Path)
            or not self.directory.is_absolute()
            or not isinstance(self.engine, OcrEngineInfo)
            or self.engine.name != "tesseract"
            or {name for name, _ in self.engine.model_sha256} != {"rus", "eng"}
            or not isinstance(self.file_sha256, tuple)
            or not 3 <= len(self.file_sha256) <= 150
        ):
            raise ValidationError(_ERROR)
        names = set()
        for item in self.file_sha256:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or "\\" in item[0]
                or ":" in item[0]
                or not item[0]
            ):
                raise ValidationError(_ERROR)
            path = PurePosixPath(item[0])
            if (
                path.is_absolute()
                or any(p in (".", "..") for p in item[0].split("/"))
                or len(path.parts) > 2
                or str(path) != item[0]
                or item[0].casefold() in names
            ):
                raise ValidationError(_ERROR)
            names.add(item[0].casefold())
            require_sha256(item[1])
        files = dict(self.file_sha256)
        if self.executable_name not in files or any(
            files.get(f"tessdata/{name}.traineddata") != digest
            for name, digest in self.engine.model_sha256
        ):
            raise ValidationError(_ERROR)

    @property
    def executable_name(self) -> str:
        return "tesseract.exe" if os.name == "nt" else "tesseract"

    def verify(self) -> None:
        try:
            if self.directory.is_symlink() or not self.directory.is_dir():
                raise OcrProcessError(_ERROR)
            allowed_dlls = {name.casefold() for name, _ in self.file_sha256 if "/" not in name}
            for path in self.directory.iterdir():
                if path.suffix.lower() == ".dll" and path.name.casefold() not in allowed_dlls:
                    raise OcrProcessError(_ERROR)
            for name, expected in self.file_sha256:
                path = self.directory / name
                if not stat.S_ISREG(path.lstat().st_mode) or any(
                    parent.is_symlink() for parent in path.parents if parent != self.directory
                ):
                    raise OcrProcessError(_ERROR)
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(128 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise OcrProcessError(_ERROR)
        except OSError:
            raise OcrProcessError(_ERROR) from None


class TesseractEngine:
    def __init__(
        self, runtime: TesseractRuntime, *, timeout_seconds: int = 30, page_segmentation: int = 6
    ) -> None:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValidationError("Время OCR должно быть целым от 1 до 120 секунд.")
        if type(page_segmentation) is not int or page_segmentation not in (6, 11):
            raise ValidationError("Поддерживаются режимы сегментации OCR 6 и 11.")
        self._page_segmentation = page_segmentation
        self._runtime = runtime
        self._timeout = timeout_seconds

    def recognize(self, image: OcrImage) -> OcrDocument:
        self._runtime.verify()
        root = self._runtime.directory
        # В окружение не переходят proxy, пользовательский PATH и TESSDATA_PREFIX.
        environment = {"OMP_THREAD_LIMIT": "1"}
        if os.name == "nt":
            system = Path(os.environ["SYSTEMROOT"])
            environment.update(
                SystemRoot=str(system), WINDIR=str(system), PATH=str(system / "System32")
            )
        contents = run_ocr_process(
            (
                str(root / self._runtime.executable_name),
                "stdin",
                "stdout",
                "--tessdata-dir",
                str(root / "tessdata"),
                "-l",
                "rus+eng",
                "--oem",
                "1",
                "--psm",
                str(self._page_segmentation),
                "-c",
                "tessedit_create_tsv=1",
            ),
            image.png,
            cwd=root,
            environment=environment,
            timeout_seconds=self._timeout,
        )
        info = self._runtime.engine
        if self._page_segmentation != 6:
            info = replace(info, version=info.version + ";psm=11")
        return parse_tesseract_tsv(contents, image, info)
