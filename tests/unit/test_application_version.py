"""Версия запуска, установленного пакета и замороженного проекта совпадает."""

import tomllib
from importlib.metadata import version
from pathlib import Path

from personal_finance import __version__


def test_application_and_distribution_versions_match_frozen_workspace():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]
    lock = tomllib.loads((root / "uv.lock").read_text("utf-8"))
    workspace = next(
        package
        for package in lock["package"]
        if package["name"] == project["name"] and package.get("source") == {"editable": "."}
    )
    assert project["version"] == workspace["version"] == __version__
    assert version(project["name"]) == __version__
