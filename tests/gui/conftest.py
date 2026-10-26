"""Неожиданное модальное сообщение не должно ждать человека в GUI-тесте."""

import pytest
from PySide6.QtWidgets import QMessageBox


@pytest.fixture(autouse=True)
def reject_unexpected_message_boxes(monkeypatch):
    def unexpected(parent, title, message, *args, **kwargs):
        pytest.fail(f"Неожиданное модальное сообщение «{title}»: {message}")

    # Тесты ожидаемых сообщений явно заменяют нужный метод своим обработчиком.
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, unexpected)
