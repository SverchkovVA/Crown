"""Снимок интерфейса на временных синтетических данных."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from personal_finance.application.vault import create_vault  # noqa: E402
from personal_finance.domain.models import AccountKind  # noqa: E402
from personal_finance.presentation.main_window import MainWindow  # noqa: E402


def main() -> None:
    app = QApplication([])
    destination = Path(__file__).resolve().parents[1] / "artifacts"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pf-preview-") as directory:
        session, _ = create_vault(
            Path(directory) / "preview.pfvault", "Мои финансы", "синтетическая парольная фраза"
        )
        bank = next(bank for bank in session.catalog.institutions() if bank.name == "Т-Банк")
        session.catalog.add_account("Карта для покупок", AccountKind.DEBIT, bank.id)
        session.catalog.add_account("Резерв", AccountKind.SAVINGS, bank.id)
        session.catalog.add_account("Наличные", AccountKind.CASH)
        window = MainWindow()
        window.activate_session(session)
        window.show()
        app.processEvents()
        window.grab().save(str(destination / "preview.png"))
        window.navigation.setCurrentRow(1)
        app.processEvents()
        window.grab().save(str(destination / "accounts-preview.png"))
        window.close()


if __name__ == "__main__":
    main()
