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
    assert len(report["steps"]) == 24
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
