"""Ограниченный дочерний процесс: каналы в памяти, без shell и сырых журналов."""

import os
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from threading import Event, Thread
from typing import BinaryIO

from personal_finance.domain.errors import FinanceError
from personal_finance.domain.ocr import MAX_OCR_IMAGE_BYTES, MAX_OCR_TSV_BYTES

_ERROR = "Локальный OCR завершился с ошибкой. Изображение не добавлено в книгу."


class OcrProcessError(FinanceError):
    pass


def run_ocr_process(
    command: Sequence[str],
    image: bytes,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int = 30,
) -> bytes:
    """Внутренний адаптер. Команда/окружение задаются доверенной конфигурацией."""
    if (
        type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 120
        or not isinstance(image, bytes)
        or len(image) > MAX_OCR_IMAGE_BYTES
    ):
        raise OcrProcessError(_ERROR)
    failed = Event()
    output = bytearray()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=environment,
            shell=False,
            creationflags=flags,
            bufsize=0,
        )
    except OSError:
        raise OcrProcessError("Локальный OCR недоступен. Проверьте комплект движка.") from None

    def kill() -> None:
        with suppress(OSError):
            process.kill()

    def receive(stream: BinaryIO, limit: int, keep: bool) -> None:
        count = 0
        try:
            while chunk := stream.read(16 * 1024):
                count += len(chunk)
                if count > limit:
                    failed.set()
                    kill()
                    break
                if keep:
                    output.extend(chunk)
        except OSError:
            failed.set()
            kill()
        finally:
            stream.close()

    def send(stream: BinaryIO) -> None:
        try:
            view = memoryview(image)
            while view:
                written = stream.write(view[: 64 * 1024])
                if not written:
                    raise OSError
                view = view[written:]
        except OSError:
            failed.set()
            kill()
        finally:
            stream.close()

    assert process.stdout is not None and process.stderr is not None and process.stdin is not None
    threads = (
        Thread(target=receive, args=(process.stdout, MAX_OCR_TSV_BYTES, True)),
        Thread(target=receive, args=(process.stderr, 64 * 1024, False)),
        Thread(target=send, args=(process.stdin,)),
    )
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill()
        process.wait(timeout=5)
    finally:
        for thread in threads:
            thread.join(timeout=5)
    if timed_out:
        raise OcrProcessError("Локальный OCR не завершился вовремя. Попробуйте другое изображение.")
    if failed.is_set() or process.returncode != 0 or any(t.is_alive() for t in threads):
        raise OcrProcessError(_ERROR)
    return bytes(output)
