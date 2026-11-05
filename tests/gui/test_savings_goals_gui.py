from datetime import date

from PySide6.QtCore import QDate, QThread

from personal_finance.presentation import main_window as module
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.savings_goals import SavingsGoalDialog, SavingsGoalOptions
from tests.gui.test_analytics_gui import fingerprints


def test_goal_calculates_in_embedded_form_and_preserves_conditions_without_writing(
    qtbot, session, monkeypatch
):
    before = fingerprints(session)
    method = module.project_savings_goal
    worker = []

    def observe(scenario):
        worker.append(QThread.currentThread() != window.thread())
        return method(scenario)

    monkeypatch.setattr(module, "project_savings_goal", observe)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window.savings_goal_action.isEnabled()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    button = next(b for b in window.report_buttons if b.text() == "Цель накоплений…")
    button.click()
    panel = window._report_forms

    def current(kind):
        form = panel.stack.currentWidget().widget()
        assert isinstance(form, kind) and form.isVisible() and not form.isWindow()
        return form

    options = current(SavingsGoalOptions)
    options.target.setText("0")
    options.calculate.click()
    assert options.feedback.isVisible() and options.result_request is None and not worker
    options.as_of.setDate(QDate(2026, 1, 31))
    options.current.setText("100")
    options.target.setText("300")
    options.monthly.setText("100")
    options.deadline.setDate(QDate(2026, 3, 30))
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    result = current(SavingsGoalDialog)
    assert worker == [True]
    assert result.projection.reached_on == date(2026, 3, 31)
    assert result.fields["Прогресс"].text() == "33,33 %"
    assert result.fields["Нужно пополнять в месяц"].text() == "200,00 RUB"
    assert result.fields["Сумма к сроку"].text() == "200,00 RUB"
    result.reject()
    assert current(SavingsGoalOptions) is options
    assert options.current.text() == "100" and options.monthly.text() == "100"
    options.deadline.setDate(QDate(2026, 1, 30))
    options.calculate.click()
    assert options.result_request is None and options.feedback.isVisible() and worker == [True]
    options.use_deadline.setChecked(False)
    assert not options.deadline.isEnabled()
    options.monthly.setText("0")
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    second = current(SavingsGoalDialog)
    assert worker == [True, True]
    assert second.projection.reached_on is None
    assert "Без пополнений" in second.fields["Ожидаемая дата цели"].text()
    assert "Срок включительно" not in second.fields
    second.reject()
    options.reject()
    assert panel.stack.count() == 0 and fingerprints(session) == before
    window.lock_book()
    assert not window.savings_goal_action.isEnabled()
