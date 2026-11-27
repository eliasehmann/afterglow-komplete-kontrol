"""'Start with Windows': a value under HKCU\\...\\Run (no admin rights needed)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Afterglow"
OLD_VALUE_NAMES = ("KontrolLEDs",)  # what the app was called before


def supported() -> bool:
    return os.name == "nt"


def command() -> str:
    """How Windows should start the app at login: quietly, without opening the browser."""
    if getattr(sys, "frozen", False):  # the packaged .exe
        return f'"{sys.executable}" --background'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" -m kkleds.app --background'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg

    for name in (VALUE_NAME, *OLD_VALUE_NAMES):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, name)
            return True
        except OSError:
            continue
    return False


def set_enabled(enabled: bool) -> None:
    if not supported():
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for old in OLD_VALUE_NAMES:  # the app's earlier name: replace, never start twice
            try:
                winreg.DeleteValue(key, old)
            except FileNotFoundError:
                pass
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


def migrate() -> None:
    """If 'Start with Windows' was on under the old name, move it to this app."""
    if not supported():
        return
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            had_old = any(_has(winreg, key, old) for old in OLD_VALUE_NAMES)
    except OSError:
        return
    if had_old:
        set_enabled(True)


def _has(winreg, key, name: str) -> bool:
    try:
        winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False
