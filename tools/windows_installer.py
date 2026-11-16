"""Сверка штатной сборки и подготовка списка файлов для NSIS."""

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import asdict
from pathlib import Path

from personal_finance import __version__
from tools.windows_payload import PayloadFile, measure

BUNDLE = Path("app/PersonalFinance")
EXE = "PersonalFinance.exe"
MANIFEST = "app-payload.json"
COMPILER_INPUT = "compiler-input"


def _read_build(directory: Path) -> tuple[tuple[PayloadFile, ...], bytes]:
    record = json.loads((directory / "build-result.json").read_bytes())
    if (
        not isinstance(record, dict)
        or record.get("format") != 1
        or record.get("status") != "passed"
        or record.get("executable") != (BUNDLE / EXE).as_posix()
        or record.get("payload_manifest") != MANIFEST
    ):
        raise ValueError("Укажите папку штатной проверенной сборки Windows.")
    raw_manifest = (directory / MANIFEST).read_bytes()
    digest = hashlib.sha256(raw_manifest).hexdigest()
    if record.get("payload_sha256") != digest:
        raise ValueError("Ведомость отличается от результата сборки.")
    manifest = json.loads(raw_manifest)
    files = measure(directory / BUNDLE)
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != 1
        or manifest.get("application") != __version__
        or manifest.get("file_count") != len(files)
        or manifest.get("bytes") != sum(item.size for item in files)
        or manifest.get("files") != [asdict(item) for item in files]
        or record.get("exe_sha256") != next(item.sha256 for item in files if item.path == EXE)
    ):
        raise ValueError("Файлы приложения или их версия отличаются от результата сборки.")
    return files, raw_manifest


def _script(files: tuple[PayloadFile, ...], digest: str) -> str:
    root = "$" + "{ROOT}"
    bundle = "$" + "{CROWN_BUNDLE}"
    failure = "$" + "{FAILURE}"
    install = ["!macro InstallPayload ROOT", f'  SetOutPath "{root}"']
    remove = ["!macro RemovePayload ROOT"]
    check = ["!macro CheckPayloadDirectories ROOT FAILURE"]
    prune = ["!macro PrunePayload ROOT"]
    directories: set[Path] = set()
    last: Path | None = None
    for file in files:
        path = Path(file.path)
        parent = path.parent
        if parent != last and parent.parts:
            target = parent.as_posix().replace("/", "\\")
            install.append(f'  SetOutPath "{root}\\{target}"')
        relative = file.path.replace("/", "\\")
        install.append(f'  File "{bundle}\\{relative}"')
        remove.append(f'  Delete "{root}\\{relative}"')
        directories.update(item for item in path.parents if item.parts)
        last = parent

    ordered = sorted(directories, key=lambda path: (len(path.parts), path.as_posix()))
    for path in ordered:
        relative = path.as_posix().replace("/", "\\")
        check.append(f'  !insertmacro CheckDirectory "{root}\\{relative}" {failure}')
    for path in reversed(ordered):
        relative = path.as_posix().replace("/", "\\")
        prune.append(f'  RMDir "{root}\\{relative}"')
    return "\n".join(
        [
            f'!define CROWN_PAYLOAD_SHA "{digest}"',
            "\n".join(install + ["!macroend"]),
            "\n".join(remove + ["!macroend"]),
            "\n".join(check + ["!macroend"]),
            "\n".join(prune + ["!macroend"]),
            "",
        ]
    )


def prepare(build: Path, output: Path) -> tuple[int, str]:
    files, raw_manifest = _read_build(build)
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?", __version__):
        raise ValueError("Номер версии приложения не подходит для установки.")

    compiler_input = output / COMPILER_INPUT
    compiler_input.mkdir()
    copied_bundle = compiler_input / BUNDLE
    for file in files:
        destination = copied_bundle / file.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(build / BUNDLE / file.path, destination)
    if measure(copied_bundle) != files:
        raise ValueError("Файлы приложения изменились при копировании для NSIS.")
    (compiler_input / MANIFEST).write_bytes(raw_manifest)
    digest = hashlib.sha256(raw_manifest).hexdigest()
    with (output / "payload.nsh").open("x", encoding="utf-8-sig", newline="\n") as stream:
        stream.write(_script(files, digest))
    return len(files), digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    prepare(args.build, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
