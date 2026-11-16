"""Замена ключевого файла под блокировкой открытой книги."""

import ctypes
import os
import stat
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

from personal_finance.domain.errors import VaultError
from personal_finance.infrastructure.security.keys import MAX_MANIFEST_BYTES

_OWNER_GROUP_DACL = 0x7
_SE_FILE_OBJECT = 1
_SE_DACL_PROTECTED = 0x1000
_PROTECTED_DACL = 0x80000000
_UNPROTECTED_DACL = 0x20000000


def read_key_file(path: Path) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MANIFEST_BYTES:
        raise VaultError("Ключевой файл недоступен или повреждён.")
    with path.open("rb") as stream:
        data = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise VaultError("Ключевой файл недоступен или повреждён.")
    return data


if sys.platform == "win32":

    def _copy_access(source: Path, target: Path, descriptor: int) -> None:
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p
        pointer_out = ctypes.POINTER(pointer)
        get = api.GetNamedSecurityInfoW
        get.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            pointer_out,
            pointer_out,
            pointer_out,
            pointer_out,
            pointer_out,
        ]
        get.restype = wintypes.DWORD
        control_of = api.GetSecurityDescriptorControl
        control_of.argtypes = [
            pointer,
            ctypes.POINTER(wintypes.WORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        control_of.restype = wintypes.BOOL
        apply = api.SetNamedSecurityInfoW
        apply.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            pointer,
            pointer,
            pointer,
            pointer,
        ]
        apply.restype = wintypes.DWORD
        free = kernel.LocalFree
        free.argtypes = [pointer]
        free.restype = pointer

        owner, group, dacl, security = pointer(), pointer(), pointer(), pointer()
        status = get(
            str(source),
            _SE_FILE_OBJECT,
            _OWNER_GROUP_DACL,
            ctypes.byref(owner),
            ctypes.byref(group),
            ctypes.byref(dacl),
            None,
            ctypes.byref(security),
        )
        if status:
            raise ctypes.WinError(status)
        try:
            control, revision = wintypes.WORD(), wintypes.DWORD()
            if not control_of(security, ctypes.byref(control), ctypes.byref(revision)):
                raise ctypes.WinError(ctypes.get_last_error())
            protection = (
                _PROTECTED_DACL if control.value & _SE_DACL_PROTECTED else _UNPROTECTED_DACL
            )
            status = apply(
                str(target),
                _SE_FILE_OBJECT,
                _OWNER_GROUP_DACL | protection,
                owner,
                group,
                dacl,
                None,
            )
            if status:
                raise ctypes.WinError(status)
        finally:
            free(security)

else:

    def _copy_access(source: Path, target: Path, descriptor: int) -> None:
        old = source.stat()
        staged = os.fstat(descriptor)
        if (old.st_uid, old.st_gid) != (staged.st_uid, staged.st_gid):
            os.fchown(descriptor, old.st_uid, old.st_gid)
        os.fchmod(descriptor, stat.S_IMODE(old.st_mode))


def replace_key_file(path: Path, previous: bytes, replacement: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".password-", suffix=".tmp", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            _copy_access(path, staged, stream.fileno())
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if read_key_file(path) != previous:
            raise VaultError("Ключевой файл изменился. Откройте книгу заново.")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
