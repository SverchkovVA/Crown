"""Публикация нового экспортного файла без замены существующих данных."""

import os
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.exporting import ExportFormat, ExportPreview, serialize_journal_export


def _publish(temporary: Path, destination: Path) -> None:
    if os.name == "nt":
        # Windows rename не заменяет существующее имя, в том числе при гонке.
        temporary.rename(destination)
    else:
        # POSIX rename заменил бы чужой файл; link создаёт только новое имя.
        os.link(temporary, destination)
        temporary.unlink()


def write_export_new(
    destination: Path, preview: ExportPreview, format: ExportFormat, *, vault_root: Path
) -> Path:
    """Сохраняет явно выбранный снимок журнала."""
    return write_export_contents(
        destination,
        lambda: serialize_journal_export(preview, format),
        format,
        vault_root=vault_root,
    )


def write_export_contents(
    destination: Path, contents: Callable[[], bytes], format: ExportFormat, *, vault_root: Path
) -> Path:
    """Публикует открытый файл после выбора состава и нового имени."""
    if (
        not isinstance(destination, Path)
        or not isinstance(vault_root, Path)
        or not isinstance(format, ExportFormat)
    ):
        raise ValidationError("Выберите новый файл и формат экспорта.")
    if destination.is_symlink():
        raise ValidationError("Выберите новое имя, не символическую ссылку.")
    destination = destination.resolve()
    if destination.is_relative_to(vault_root.resolve()):
        raise ValidationError("Сохраняйте экспорт за пределами папки финансовой книги.")
    if destination.suffix.lower() != "." + format.value or not destination.parent.is_dir():
        raise ValidationError("Выберите существующую папку и расширение CSV либо JSON.")
    if destination.exists():
        raise ValidationError("Файл уже существует. Выберите новое имя экспорта.")
    data = contents()
    temporary = destination.with_name(".crown-export-" + uuid4().hex + ".tmp")
    descriptor: int | None = None
    owned = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owned = True
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        _publish(temporary, destination)
        return destination
    except FileExistsError as exc:
        raise ValidationError("Файл уже существует. Выберите новое имя экспорта.") from exc
    except OSError as exc:
        raise FinanceError(
            "Не удалось сохранить экспорт. Проверьте папку и свободное место."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # Только наше новое временное имя; пользовательское назначение не удаляется.
        if owned:
            temporary.unlink(missing_ok=True)
