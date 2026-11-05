import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.run_test_group import GroupSelection, group_for


def item(name, *, index=0, original="test_one", family="tests/test_sample.py::test_one"):
    return SimpleNamespace(
        nodeid=family + "[" + name + "]",
        name=original + "[" + name + "]",
        originalname=original,
        callspec=SimpleNamespace(indices={"value": index}),
    )


@pytest.mark.parametrize("count", [0, -1, 17, True, 2.0, "2"])
def test_invalid_count_rejected(count):
    with pytest.raises(ValueError):
        group_for("tests/a.py::test_a", count)


@pytest.mark.parametrize("index,count", [(-1, 2), (2, 2), (True, 2), (0, 0), (0, 17)])
def test_invalid_group_index_rejected(tmp_path, index, count):
    with pytest.raises(ValueError):
        GroupSelection(index, count, tmp_path / "manifest.json")


@pytest.mark.parametrize("node_id", ["", None, 1, True])
def test_invalid_node_identity_rejected(node_id):
    with pytest.raises(ValueError):
        group_for(node_id, 2)


def test_random_parameter_id_never_changes_assigned_group():
    for count in range(1, 17):
        assert group_for("tests/a.py::test_a[random-1]", count) == group_for(
            "tests/a.py::test_a[random-2]", count
        )


def test_separate_collections_cover_every_stable_key_once(tmp_path):
    results = []
    for index in range(2):
        values = [
            item(
                f"random-{index}-{n}",
                index=n % 3,
                family=f"tests/a.py::test_{n // 3}",
                original=f"test_{n // 3}",
            )
            for n in range(30)
        ]
        original = list(values)
        config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=Mock()))
        path = tmp_path / f"manifest-{index}.json"
        GroupSelection(index, 2, path).pytest_collection_modifyitems(config, values)
        result = json.loads(path.read_text(encoding="utf-8"))
        results.append(result)
        assert len(values) == len(result["selected_keys"])
        assert all(group_for(v.nodeid, 2) == index for v in values)
        omitted = config.hook.pytest_deselected.call_args.kwargs["items"]
        assert len(omitted) + len(values) == len(original)
    assert results[0]["collected_keys"] == results[1]["collected_keys"]
    a, b = map(lambda r: set(r["selected_keys"]), results)
    assert not a & b and a | b == set(results[0]["collected_keys"])


def test_parameterized_classes_keep_distinct_method_keys(tmp_path):
    values = [
        item("x", original="test_one", family="tests/a.py::TestClass[variable-1]::test_one"),
        item("y", original="test_two", family="tests/a.py::TestClass[variable-1]::test_two"),
    ]
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=Mock()))
    path = tmp_path / "class.json"
    GroupSelection(0, 1, path).pytest_collection_modifyitems(config, values)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert len(set(result["selected_keys"])) == 2 and len(values) == 2


def test_duplicate_stable_key_rejected_before_writing_manifest(tmp_path):
    values = [item("one"), item("two")]
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=Mock()))
    path = tmp_path / "bad.json"
    with pytest.raises(ValueError, match="ключ"):
        GroupSelection(0, 2, path).pytest_collection_modifyitems(config, values)
    assert not path.exists()


def test_duplicate_node_ids_rejected(tmp_path):
    values = [item("same", index=0), item("same", index=1)]
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=Mock()))
    path = tmp_path / "bad.json"
    with pytest.raises(ValueError, match="идентификатор"):
        GroupSelection(0, 2, path).pytest_collection_modifyitems(config, values)
    assert not path.exists()


def test_real_cli_executes_each_synthetic_case_exactly_once(tmp_path):
    import os
    import subprocess
    import sys
    import xml.etree.ElementTree as ET
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    ini = tmp_path / "pytest.ini"
    ini.write_text("[pytest]\n", encoding="utf-8")
    source = tmp_path / "test_example.py"
    source.write_text(
        "import pytest\nfrom uuid import uuid4\n"
        "@pytest.mark.parametrize('value',[uuid4(),uuid4()])\n"
        "def test_random(value): assert value\n"
        "def test_other(): assert True\n",
        encoding="utf-8",
    )
    results = []
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONIOENCODING="utf-8")
    for index in range(2):
        manifest = tmp_path / f"cli-{index}.json"
        junit = tmp_path / f"cli-{index}.xml"
        outcome = subprocess.run(
            [
                sys.executable,
                str(root / "tools/run_test_group.py"),
                "--index",
                str(index),
                "--count",
                "2",
                "--manifest",
                str(manifest),
                "-c",
                str(ini),
                "--rootdir",
                str(tmp_path),
                str(source),
                "--junitxml",
                str(junit),
                "-q",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        # Непустая группа должна быть успешна; пустая не выдаётся за пройденные тесты.
        result = json.loads(manifest.read_text(encoding="utf-8"))
        assert outcome.returncode == (0 if result["selected"] else 5), outcome.stdout
        cases = ET.parse(junit).findall(".//testcase")
        assert len(cases) == len(result["selected"])
        assert not any(v.find("failure") is not None or v.find("error") is not None for v in cases)
        results.append(result)
    a, b = map(lambda r: set(r["selected_keys"]), results)
    assert not a & b and a | b == set(results[0]["collected_keys"])
    assert len(a | b) == 3
