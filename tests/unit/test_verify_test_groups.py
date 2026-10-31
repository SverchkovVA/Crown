import json
import xml.etree.ElementTree as ET

import pytest

from tools.verify_test_groups import verify_groups


def group(root, index, selected, collected=("a", "b"), *, errors=0, failures=0, skipped=0):
    folder = root / f"group-{index}"
    folder.mkdir(parents=True)
    value = {
        "format": 1,
        "count": 2,
        "index": index,
        "collected_keys": list(collected),
        "selected_keys": list(selected),
    }
    (folder / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
    suite = ET.Element(
        "testsuite",
        tests=str(len(selected)),
        errors=str(errors),
        failures=str(failures),
        skipped=str(skipped),
        time="0.1",
    )
    for name in selected:
        ET.SubElement(suite, "testcase", classname="tests.example", name=name)
    tree = ET.Element("testsuites")
    tree.append(suite)
    ET.ElementTree(tree).write(folder / "tests.xml", encoding="utf-8")
    return folder


def rewrite(path, update):
    value = json.loads(path.read_text(encoding="utf-8"))
    update(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_all_cases_are_covered_without_repeats(tmp_path):
    group(tmp_path, 0, ["a"])
    group(tmp_path, 1, ["b"])
    result = verify_groups(tmp_path, 2)
    assert result["status"] == "passed" and result["tests"] == 2
    assert result["all_tests_covered"] and result["intersection_empty"]


@pytest.mark.parametrize("count", [0, 17, True, 1.0, "2"])
def test_invalid_group_count(tmp_path, count):
    with pytest.raises(ValueError):
        verify_groups(tmp_path, count)


def test_missing_report_fails(tmp_path):
    group(tmp_path, 0, ["a"])
    with pytest.raises(ValueError, match="манифест"):
        verify_groups(tmp_path, 2)


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(index=0),
        lambda v: v.update(count=3),
        lambda v: v.update(format=2),
        lambda v: v.update(index=True),
        lambda v: v.update(collected_keys=["a", "c"]),
        lambda v: v.update(selected_keys=["a"]),
        lambda v: v.update(selected_keys=[]),
        lambda v: v.update(selected_keys=["outside"]),
        lambda v: v.update(selected_keys=["b", "b"]),
        lambda v: v.update(collected_keys=["a", "a"]),
        lambda v: v.update(selected_keys=[1]),
    ],
)
def test_bad_or_overlapping_manifest_fails(tmp_path, change):
    group(tmp_path, 0, ["a"])
    folder = group(tmp_path, 1, ["b"])
    rewrite(folder / "manifest.json", change)
    with pytest.raises(ValueError):
        verify_groups(tmp_path, 2)


def test_uncovered_test_is_detected(tmp_path):
    group(tmp_path, 0, ["a"], collected=["a", "b", "c"])
    group(tmp_path, 1, ["b"], collected=["a", "b", "c"])
    with pytest.raises(ValueError, match="Часть"):
        verify_groups(tmp_path, 2)


@pytest.mark.parametrize("kind", ["errors", "failures", "skipped"])
def test_unsuccessful_junit_is_not_accepted(tmp_path, kind):
    group(tmp_path, 0, ["a"])
    group(tmp_path, 1, ["b"], **{kind: 1})
    with pytest.raises(ValueError):
        verify_groups(tmp_path, 2)


@pytest.mark.parametrize("kind", ["error", "failure", "skipped"])
def test_hidden_unsuccessful_testcase_is_not_accepted(tmp_path, kind):
    group(tmp_path, 0, ["a"])
    folder = group(tmp_path, 1, ["b"])
    path = folder / "tests.xml"
    tree = ET.parse(path)
    ET.SubElement(tree.find(".//testcase"), kind)
    tree.write(path, encoding="utf-8")
    with pytest.raises(ValueError):
        verify_groups(tmp_path, 2)


@pytest.mark.parametrize("kind", ["missing_file", "partial", "missing_case", "duplicate"])
def test_partial_or_duplicate_junit_fails(tmp_path, kind):
    group(tmp_path, 0, ["a"], collected=["a", "b", "c"])
    folder = group(tmp_path, 1, ["b", "c"], collected=["a", "b", "c"])
    path = folder / "tests.xml"
    if kind == "missing_file":
        path.unlink()
    elif kind == "partial":
        path.write_text("<testsuites>", encoding="utf-8")
    else:
        tree = ET.parse(path)
        suite = tree.find("testsuite")
        if kind == "missing_case":
            suite.remove(suite.findall("testcase")[0])
        else:
            suite.findall("testcase")[1].set("name", "b")
        tree.write(path, encoding="utf-8")
    with pytest.raises((ValueError, ET.ParseError)):
        verify_groups(tmp_path, 2)
