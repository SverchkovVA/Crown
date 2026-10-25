# Основа сборки каталога приложения. Windows EXE собирается только на Windows.
from pathlib import Path

root = Path(SPECPATH).parents[1]
source = root / "src"
migrations = source / "personal_finance" / "infrastructure" / "persistence" / "migrations"
datas = [(str(path), str(path.parent.relative_to(source))) for path in migrations.rglob("*.py")]
datas += [(str(root / "THIRD_PARTY_NOTICES.md"), ".")]
legal = root / "docs" / "legal"
if legal.is_dir():
    datas += [(str(path), "legal") for path in legal.iterdir() if path.is_file()]

analysis = Analysis(
    [str(source / "personal_finance" / "bootstrap" / "main.py")],
    pathex=[str(source)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "sqlalchemy.dialects.sqlite.pysqlite", "sqlcipher3.dbapi2", "alembic.ddl.sqlite",
        "alembic.ddl.impl", "nacl.bindings", "_cffi_backend",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name="PersonalFinance", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
)
collection = COLLECT(
    exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="PersonalFinance",
)
