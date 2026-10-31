from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget

from personal_finance.bootstrap import main as module
from personal_finance.bootstrap import self_test
from personal_finance.infrastructure.ocr.process import OcrProcessError


def window_factory(qapp, monkeypatch, seen):
    class Window(QWidget):
        def __init__(self, *, ocr_recognitions):
            super().__init__()
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            seen.append(ocr_recognitions)

        def show(self):
            super().show()
            QTimer.singleShot(0, qapp.quit)

    monkeypatch.setattr(module, "QApplication", lambda args: qapp)
    monkeypatch.setattr(module, "MainWindow", Window)


def test_entry_without_module_does_not_attempt_ocr_loading(qapp, monkeypatch):
    seen = []
    window_factory(qapp, monkeypatch, seen)
    monkeypatch.setattr(module, "runtime_directory", lambda explicit: None)

    def unexpected(path):
        raise AssertionError("Не должно быть поиска/запуска OCR")

    monkeypatch.setattr(module, "recognitions_for_directory", unexpected)
    assert module.main([]) == 0 and seen == [()]


def test_explicit_module_passes_same_services_into_main_window(qapp, monkeypatch, tmp_path):
    seen = []
    window_factory(qapp, monkeypatch, seen)
    services = (object(), object(), object())
    provided = []
    monkeypatch.setattr(
        module, "runtime_directory", lambda explicit: provided.append(explicit) or tmp_path
    )
    monkeypatch.setattr(
        module, "recognitions_for_directory", lambda path: services if path == tmp_path else None
    )
    assert module.main(["--ocr-runtime", str(tmp_path)]) == 0
    assert provided == [tmp_path] and seen == [services]


def test_bad_module_keeps_book_ui_available_and_sanitizes_warning(
    qapp, monkeypatch, tmp_path, capsys
):
    seen = []
    window_factory(qapp, monkeypatch, seen)
    warnings = []
    marker = "SECRET_RUNTIME_MARKER"
    monkeypatch.setattr(module, "runtime_directory", lambda explicit: tmp_path)

    def failed(path):
        raise OcrProcessError(marker)

    monkeypatch.setattr(module, "recognitions_for_directory", failed)
    monkeypatch.setattr(
        module.QMessageBox, "warning", lambda parent, title, message: warnings.append(message)
    )
    assert module.main(["--ocr-runtime", str(tmp_path)]) == 0 and seen == [()]
    assert len(warnings) == 1 and marker not in warnings[0] and str(tmp_path) not in warnings[0]
    captured = capsys.readouterr()
    assert marker not in captured.err + captured.out


def test_entry_routes_requested_module_into_safe_self_test(qapp, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(module, "QApplication", lambda args: qapp)
    monkeypatch.setattr(module, "runtime_directory", lambda explicit: tmp_path)

    def check(app, report, *, ocr_directory):
        seen.append((app, report, ocr_directory))
        return 7

    monkeypatch.setattr(self_test, "self_test", check)
    report = tmp_path / "safe-report.json"
    assert (
        module.main(
            ["--self-test", "--ocr-runtime", str(tmp_path), "--self-test-report", str(report)]
        )
        == 7
    )
    assert seen == [(qapp, report, tmp_path)]


def test_legacy_preview_launcher_uses_the_common_entry(monkeypatch, tmp_path):
    import sys

    from tools import preview_ocr

    calls = []
    monkeypatch.setattr(sys, "argv", ["preview_ocr", "--runtime", str(tmp_path)])
    monkeypatch.setattr(preview_ocr, "application_main", lambda args: calls.append(args) or 9)
    assert preview_ocr.main() == 9
    assert calls == [["--ocr-runtime", str(tmp_path)]]
