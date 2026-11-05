"""Проверка совокупного результата независимых групп pytest."""

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def verify_groups(root: Path, count: int) -> dict[str, object]:
    if type(count) is not int or not 1 <= count <= 16:
        raise ValueError("Некорректное число групп.")
    manifests = sorted(root.rglob("manifest.json"))
    if len(manifests) != count:
        raise ValueError("Получены не все манифесты групп.")
    all_keys: set[str] | None = None
    seen: set[str] = set()
    indices: set[int] = set()
    totals = []
    for path in manifests:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Манифест группы слишком большой.")
        value = json.loads(path.read_text(encoding="utf-8"))
        index = value.get("index")
        if (
            value.get("format") != 1
            or value.get("count") != count
            or type(index) is not int
            or not 0 <= index < count
            or index in indices
        ):
            raise ValueError("Некорректный или повторный номер группы.")
        indices.add(index)
        collected, selected = value.get("collected_keys"), value.get("selected_keys")
        if (
            not isinstance(collected, list)
            or not isinstance(selected, list)
            or not all(isinstance(v, str) and v for v in collected + selected)
            or len(set(collected)) != len(collected)
            or len(set(selected)) != len(selected)
        ):
            raise ValueError("Некорректные ключи коллекции.")
        current, chosen = set(collected), set(selected)
        if not current or not chosen or not chosen <= current:
            raise ValueError("Пустая группа или выбор вне полной коллекции.")
        if all_keys is None:
            all_keys = current
        elif all_keys != current:
            raise ValueError("Группы собраны из разных коллекций тестов.")
        if seen & chosen:
            raise ValueError("Тест выполнен более чем в одной группе.")
        seen.update(chosen)
        junit = path.parent / "tests.xml"
        if not junit.is_file() or junit.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Нет полного JUnit группы.")
        report = ET.parse(junit).getroot()
        suites = [report] if report.tag == "testsuite" else report.findall("testsuite")
        if len(suites) != 1:
            raise ValueError("Ожидается один полный набор JUnit.")
        suite = suites[0]
        cases = suite.findall("testcase")
        identities = [(v.get("classname"), v.get("name")) for v in cases]
        if len(set(identities)) != len(cases) or any(not a or not b for a, b in identities):
            raise ValueError("JUnit содержит повторные или пустые идентификаторы тестов.")
        if (
            int(suite.get("tests", "-1")) != len(chosen)
            or len(cases) != len(chosen)
            or any(int(suite.get(key, "0")) != 0 for key in ("errors", "failures", "skipped"))
            or any(
                v.find(key) is not None for v in cases for key in ("error", "failure", "skipped")
            )
        ):
            raise ValueError("Группа выполнена не полностью или содержит неуспешные тесты.")
        totals.append({"index": index, "tests": len(chosen), "seconds": suite.get("time")})
    if seen != all_keys:
        raise ValueError("Часть тестов не выполнена ни одной группой.")
    return {
        "status": "passed",
        "tests": len(seen),
        "count": count,
        "groups": sorted(totals, key=lambda v: v["index"]),
        "intersection_empty": True,
        "all_tests_covered": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверить полноту всех групп тестов.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_groups(args.root, args.count), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
