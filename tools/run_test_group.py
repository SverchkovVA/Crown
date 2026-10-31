"""Детерминированное распределение полного набора pytest без новых зависимостей."""

import argparse
import hashlib
import json
from pathlib import Path

import pytest


def group_for(node_id: str, count: int) -> int:
    if (
        not isinstance(node_id, str)
        or not node_id
        or type(count) is not int
        or not 1 <= count <= 16
    ):
        raise ValueError("Некорректная группа тестов.")
    return (
        int.from_bytes(
            hashlib.sha256(node_id.partition("[")[0].encode("utf-8")).digest()[:8], "big"
        )
        % count
    )


class GroupSelection:
    def __init__(self, index: int, count: int, manifest: Path) -> None:
        if (
            type(index) is not int
            or type(count) is not int
            or not 1 <= count <= 16
            or not 0 <= index < count
        ):
            raise ValueError("Некорректный номер группы тестов.")
        self.index, self.count, self.manifest = index, count, manifest

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        all_ids = [item.nodeid for item in items]

        def key(item: pytest.Item) -> str:
            indices = getattr(getattr(item, "callspec", None), "indices", {})
            original = getattr(item, "originalname", None) or item.name.partition("[")[0]
            return json.dumps(
                [item.nodeid.partition("[")[0], original, sorted(indices.items())],
                ensure_ascii=False,
                separators=(",", ":"),
            )

        all_keys = [key(item) for item in items]
        if len(set(all_keys)) != len(all_keys):
            raise ValueError("Коллекция не имеет однозначных стабильных ключей.")
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("Коллекция тестов содержит повторяющиеся идентификаторы.")
        chosen, omitted = [], []
        for item in items:
            (chosen if group_for(item.nodeid, self.count) == self.index else omitted).append(item)
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(
            json.dumps(
                {
                    "format": 1,
                    "index": self.index,
                    "count": self.count,
                    "collected": all_ids,
                    "selected": [item.nodeid for item in chosen],
                    "collected_keys": all_keys,
                    "selected_keys": [key(item) for item in chosen],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        items[:] = chosen
        config.hook.pytest_deselected(items=omitted)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Запустить отдельную группу полного набора тестов."
    )
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    options, arguments = parser.parse_known_args()
    plugin = GroupSelection(options.index, options.count, options.manifest)
    return int(pytest.main(arguments, plugins=[plugin]))


if __name__ == "__main__":
    raise SystemExit(main())
