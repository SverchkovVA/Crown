import json
import subprocess
import sys
import time

import pytest

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import VaultError
from tests.conftest import PASSWORD


def test_process_termination_rolls_back_ddl_and_data(session, tmp_path) -> None:
    root, original_name = session.root, session.name
    session.close()
    ready = tmp_path / "ready"
    program = r"""
import json, sys
from pathlib import Path
from personal_finance.application.vault import open_vault
parameters = json.loads(sys.stdin.readline())
session = open_vault(Path(parameters['root']), parameters['password'])
connection = session.engine.connect()
transaction = connection.begin()
connection.exec_driver_sql("CREATE TABLE crash_probe (id TEXT)")
connection.exec_driver_sql("UPDATE books SET name='Незавершённое изменение'")
Path(parameters['ready']).write_text('ready')
sys.stdin.readline()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(
            json.dumps({"root": str(root), "password": PASSWORD, "ready": str(ready)}) + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.02)
        assert ready.exists(), "Дочерний процесс не начал проверочную транзакцию"
        with pytest.raises(VaultError, match="уже открыта"):
            open_vault(root, PASSWORD).close()
        process.terminate()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdin:
            process.stdin.close()
        if process.stderr:
            process.stderr.close()
    reopened = open_vault(root, PASSWORD)
    try:
        assert reopened.name == original_name
        with reopened.engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM sqlite_master WHERE name='crash_probe'"
                ).scalar_one()
                == 0
            )
        reopened.check_integrity()
    finally:
        reopened.close()
