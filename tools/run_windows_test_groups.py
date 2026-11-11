"""Параллельный прогон Windows с проверкой полноты и единым JUnit."""

import argparse
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

from verify_test_groups import verify_groups

GROUP_COUNT = 2


def _run_group(root: Path, temporary: Path, index: int) -> int:
    group = root / f"group-{index}"
    group.mkdir()
    isolated = temporary / group.name
    isolated.mkdir()
    runner = Path(__file__).with_name("run_test_group.py")
    command = [
        sys.executable,
        str(runner),
        "--index",
        str(index),
        "--count",
        str(GROUP_COUNT),
        "--manifest",
        str(group / "manifest.json"),
        "-vv",
        "-o",
        "faulthandler_timeout=90",
        "-o",
        "faulthandler_exit_on_timeout=true",
        "-o",
        f"cache_dir={isolated / 'cache'}",
        "--basetemp",
        str(isolated / "pytest"),
        "--junitxml",
        str(group / "tests.xml"),
        "tests",
    ]
    with (group / "stdout.log").open("wb") as stdout, (group / "stderr.log").open("wb") as stderr:
        return subprocess.run(command, stdout=stdout, stderr=stderr, check=False).returncode


def _merge_junit(root: Path, destination: Path, seconds: float) -> None:
    cases: list[ET.Element] = []
    for group in sorted(root.glob("group-*")):
        report = ET.parse(group / "tests.xml").getroot()
        suite = report if report.tag == "testsuite" else report.find("testsuite")
        if suite is None:
            raise ValueError("В группе отсутствует JUnit.")
        cases.extend(suite.findall("testcase"))
    identities = {(case.get("classname"), case.get("name")) for case in cases}
    if len(identities) != len(cases):
        raise ValueError("Общий JUnit содержит повторяющиеся идентификаторы.")
    report = ET.Element("testsuites")
    suite = ET.SubElement(
        report,
        "testsuite",
        name="pytest",
        tests=str(len(cases)),
        errors="0",
        failures="0",
        skipped="0",
        time=f"{seconds:.3f}",
    )
    suite.extend(cases)
    ET.ElementTree(report).write(destination, encoding="utf-8", xml_declaration=True)


def run_groups(output: Path) -> int:
    root = output / "windows-test-groups"
    root.mkdir()
    started = monotonic()
    with (
        tempfile.TemporaryDirectory(prefix="crown-windows-pytest-") as temporary,
        ThreadPoolExecutor(max_workers=GROUP_COUNT) as executor,
    ):
        futures = [
            executor.submit(_run_group, root, Path(temporary), index)
            for index in range(GROUP_COUNT)
        ]
        exit_codes = [job.result() for job in futures]
    if any(exit_codes):
        print(f"Коды завершения групп: {exit_codes}")
        return 1
    summary = verify_groups(root, GROUP_COUNT)
    _merge_junit(root, output / "tests.xml", monotonic() - started)
    (output / "windows-groups-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Выполнить весь набор тестов двумя группами.")
    parser.add_argument("output", type=Path)
    return run_groups(parser.parse_args().output)


if __name__ == "__main__":
    raise SystemExit(main())
