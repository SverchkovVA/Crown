import json

from personal_finance.bootstrap import self_test as module
from personal_finance.presentation.main_window import MainWindow


def test_report_records_actual_platform_and_keeps_manual_acceptance_pending(qapp, tmp_path) -> None:
    import sys

    path = tmp_path / "self-test.json"
    existing = {id(widget) for widget in qapp.topLevelWidgets()}
    assert module.self_test(qapp, path) == 0
    assert not any(
        isinstance(widget, MainWindow) and id(widget) not in existing
        for widget in qapp.topLevelWidgets()
    )
    report = json.loads(path.read_text())
    assert report["status"] == "passed"
    assert report["runtime"]["platform"] == sys.platform
    assert report["runtime"]["frozen"] is False
    assert report["windows11_manual_acceptance"] == "pending"
    assert report["uses_real_financial_data"] is False
    assert len(report["steps"]) == 26
    assert report["steps"][-1]["id"] == "preview_ocr_without_engine"
    assert all(step["status"] == "passed" for step in report["steps"])
    raw = path.read_text()
    for sensitive in [
        str(tmp_path),
        "тестовая фраза сборки",
        "пароль проверочной копии",
        "database_key",
        "PF1-",
        "Наличные",
    ]:
        assert sensitive not in raw


def test_failed_step_is_reported_without_exception_message(
    qapp, tmp_path, monkeypatch, capsys
) -> None:
    marker = "НЕ_РАСКРЫВАТЬ_СЕКРЕТ"

    def fail(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(module, "create_backup", fail)
    path = tmp_path / "failed.json"
    assert module.self_test(qapp, path) == 1
    report = json.loads(path.read_text())
    assert report["status"] == "failed"
    assert report["error_type"] == "RuntimeError"
    assert report["steps"][-1]["id"] == "create_verified_backup"
    assert report["steps"][-1]["status"] == "failed"
    assert marker not in path.read_text()
    assert marker not in capsys.readouterr().err


def test_existing_report_is_not_replaced(qapp, tmp_path) -> None:
    path = tmp_path / "existing.json"
    path.write_bytes(b"keep existing contents")
    assert module.self_test(qapp, path) == 2
    assert path.read_bytes() == b"keep existing contents"


def test_optional_ocr_self_test_has_two_extra_steps_without_paths(qapp, tmp_path, monkeypatch):
    from dataclasses import replace

    from personal_finance.application.ocr import Ocr
    from personal_finance.application.ocr_cards import CardRecognition
    from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
    from personal_finance.infrastructure.ocr.generic import (
        GenericCardAdapter,
        GenericHistoryAdapter,
    )
    from personal_finance.infrastructure.ocr.images import load_ocr_image
    from tests.ocr_fixtures import document

    class Engine:
        def recognize(self, image):
            doc = document(
                ("Детали операции", "Списано: 473,15 руб.", "22.10.2026 09:18"),
                image_sha256=image.sha256,
            )
            return replace(doc, width=image.width, height=image.height)

    service = CardRecognition(
        Ocr(Engine()),
        load_ocr_image,
        GenericCardAdapter(),
        history_adapter=GenericHistoryAdapter(),
        bank_name=UNIVERSAL_PROFILE,
        auto_history_hint=GenericHistoryAdapter.has_heading,
    )
    monkeypatch.setattr(module, "recognitions_for_directory", lambda directory: (service,))
    requested = tmp_path / "SECRET_RUNTIME_PATH"
    path = tmp_path / "ocr-self-test.json"
    assert module.self_test(qapp, path, ocr_directory=requested) == 0
    report = json.loads(path.read_text())
    assert report["status"] == "passed" and report["local_ocr_runtime_requested"] is True
    assert len(report["steps"]) == 28
    assert report["steps"][0]["id"] == "verify_local_ocr_runtime"
    assert report["steps"][-1]["id"] == "recognize_local_synthetic_image"
    assert "SECRET_RUNTIME_PATH" not in path.read_text() and str(tmp_path) not in path.read_text()


def test_invalid_optional_runtime_fails_before_any_book_is_created(qapp, tmp_path, monkeypatch):
    from personal_finance.infrastructure.ocr.process import OcrProcessError

    marker = "SECRET_RUNTIME_FAILURE"
    calls = []
    monkeypatch.setattr(module, "create_vault", lambda *args: calls.append(True))

    def failed(directory):
        raise OcrProcessError(marker)

    monkeypatch.setattr(module, "recognitions_for_directory", failed)
    path = tmp_path / "failed-ocr.json"
    assert module.self_test(qapp, path, ocr_directory=tmp_path) == 1
    report = json.loads(path.read_text())
    assert report["status"] == "failed" and report["error_type"] == "OcrProcessError"
    assert report["steps"][-1]["id"] == "verify_local_ocr_runtime"
    assert not calls and marker not in path.read_text() and str(tmp_path) not in path.read_text()
