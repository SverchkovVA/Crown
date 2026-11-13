import csv
import json
from datetime import date
from io import StringIO

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QFileDialog

from personal_finance.domain.bonuses import BonusDraft, BonusKind
from personal_finance.domain.exporting import ExportFormat
from personal_finance.domain.models import CategoryKind
from personal_finance.presentation.bonus_export import BonusExportOptions, BonusExportPreview
from personal_finance.presentation.inline_forms import FormHost
from personal_finance.presentation.main_window import MainWindow


def current(window, expected):
    host = window._bonus_forms.stack.currentWidget()
    assert isinstance(host, FormHost)
    dialog = host.widget()
    assert isinstance(dialog, expected)
    return dialog


def test_bonus_export_preview_and_local_file_use_same_snapshot(
    session, tmp_path, qtbot, monkeypatch
):
    service = session.bonuses
    program = service.save_program("=Учебная программа")
    category = session.catalog.add_category("+Учебная категория", CategoryKind.EXPENSE)
    day = date(2026, 11, 12)
    valid = BonusDraft(program.id, day, BonusKind.ACCRUAL, 125, category.id, note="Скрытая заметка")
    service.save_entry(valid)
    to_cancel = service.save_entry(BonusDraft(program.id, day, BonusKind.SPENDING, -150))
    service.save_entry(
        to_cancel.draft,
        identifier=to_cancel.id,
        expected_revision_id=to_cancel.revision_id,
        is_deleted=True,
    )
    service.save_entry(BonusDraft(program.id, date(2026, 11, 13), BonusKind.ACCRUAL, 250))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window.bonus_export_action.isEnabled()
    window.activate_session(session)
    window.navigation.setCurrentRow(1)
    destination = tmp_path / "bonuses.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), ""))
    before = session.ledger.operations(), session.ledger.balances()

    try:
        assert window.bonus_export_action.isEnabled()
        window.bonus_export_action.trigger()
        options = current(window, BonusExportOptions)
        options.start.setDate(QDate(2026, 11, 13))
        options.end.setDate(QDate(day))
        options.preview.click()
        assert options.error.isVisible()
        options.start.setDate(QDate(day))
        options.format.setCurrentIndex(options.format.findData(ExportFormat.JSON.value))
        options.preview.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        preview = current(window, BonusExportPreview)
        assert preview.table.model().rowCount() == 1
        assert preview.table.model().item(0, 1).text() == "Программа 1"
        assert preview.table.model().item(0, 4).text() == "Категория 1"

        service.save_entry(BonusDraft(program.id, day, BonusKind.SPENDING, -35, category.id))
        preview.save.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        payload = json.loads(destination.read_text(encoding="utf-8"))
        assert payload["period"] == {"start": "2026-11-12", "end": "2026-11-12"}
        assert payload["entries"] == [
            {
                "date": "2026-11-12",
                "program": "Программа 1",
                "kind": "accrual",
                "units_hundredths": "125",
                "category": "Категория 1",
            }
        ]
        assert "Скрытая" not in str(payload) and program.id not in str(payload)

        destination = tmp_path / "bonuses.csv"
        options.format.setCurrentIndex(options.format.findData(ExportFormat.CSV.value))
        options.include_names.setChecked(True)
        options.preview.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        preview = current(window, BonusExportPreview)
        assert preview.table.model().rowCount() == 2
        preview.save.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert destination.read_bytes().startswith(b"\xef\xbb\xbf")
        rows = list(
            csv.DictReader(StringIO(destination.read_text(encoding="utf-8-sig")), delimiter=";")
        )
        assert {row["Бонусы"] for row in rows} == {"1,25", "-0,35"}
        assert {row["Программа"] for row in rows} == {"'=Учебная программа"}
        assert {row["Категория"] for row in rows} == {"'+Учебная категория"}
        assert (session.ledger.operations(), session.ledger.balances()) == before
    finally:
        window.lock_book()
