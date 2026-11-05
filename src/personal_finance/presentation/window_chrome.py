"""Цвет нативной рамки Windows 11 без замены системного управления окном."""

import ctypes
import sys

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget


def apply_caption_theme(window: QWidget, *, dark: bool) -> bool:
    if sys.platform != "win32" or QApplication.platformName() != "windows":
        return False
    try:
        api = ctypes.WinDLL("dwmapi")
        setter = api.DwmSetWindowAttribute
        setter.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
        setter.restype = ctypes.c_long
        background = QColor("#151d23" if dark else "#f5f7f9")
        foreground = QColor("#e5edf2" if dark else "#163247")
        values = (
            (20, int(dark)),
            (35, background.red() | background.green() << 8 | background.blue() << 16),
            (36, foreground.red() | foreground.green() << 8 | foreground.blue() << 16),
        )
        handle = int(window.winId())
        results = []
        for attribute, value in values:
            native = ctypes.c_uint(value)
            results.append(setter(handle, attribute, ctypes.byref(native), ctypes.sizeof(native)))
        return all(result >= 0 for result in results)
    except (OSError, AttributeError):
        return False
