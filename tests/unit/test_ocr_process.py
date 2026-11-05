"""Реальные одноразовые процессы только с вымышленным содержимым."""

import os
import sys
import threading
import time

import pytest

from personal_finance.infrastructure.ocr.process import OcrProcessError, run_ocr_process


def run(code, tmp_path, data=b"test", timeout=5):
    env = {"SystemRoot": os.environ["SYSTEMROOT"]} if os.name == "nt" else {}
    return run_ocr_process(
        (sys.executable, "-I", "-c", code),
        data,
        cwd=tmp_path,
        environment=env,
        timeout_seconds=timeout,
    )


def test_binary_image_and_tsv_use_pipes_without_files(tmp_path):
    data = b"\x89PNG\0" * 100_000
    output = run("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())", tmp_path, data)
    assert output == data
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_excessive_output_is_terminated_without_echo(channel, tmp_path):
    code = (
        "import sys; sys.stdin.buffer.read(); "
        f"sys.{channel}.buffer.write(b'synthetic secret'*400000)"
    )
    with pytest.raises(OcrProcessError) as caught:
        run(code, tmp_path)
    assert "synthetic secret" not in str(caught.value)


def test_stderr_and_failure_are_not_exposed(tmp_path):
    with pytest.raises(OcrProcessError) as caught:
        run(
            "import sys; sys.stdin.buffer.read(); "
            "sys.stderr.write('synthetic secret'); sys.exit(7)",
            tmp_path,
        )
    assert "synthetic secret" not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_timeout_also_releases_blocked_writer_and_all_reader_threads(tmp_path):
    before = set(threading.enumerate())
    started = time.monotonic()
    with pytest.raises(OcrProcessError, match="вовремя"):
        run("import time; time.sleep(30)", tmp_path, b"x" * 1_000_000, timeout=1)
    assert time.monotonic() - started < 8
    assert set(threading.enumerate()) == before


def test_missing_executable_error_does_not_echo_path(tmp_path):
    with pytest.raises(OcrProcessError) as caught:
        run_ocr_process((str(tmp_path / "secret-missing"),), b"", cwd=tmp_path, environment={})
    assert "secret-missing" not in str(caught.value)


@pytest.mark.parametrize("timeout", [0, 121, True, 1.5])
def test_invalid_timeout_is_rejected_before_process_start(timeout, tmp_path):
    with pytest.raises(OcrProcessError):
        run("raise RuntimeError", tmp_path, timeout=timeout)
