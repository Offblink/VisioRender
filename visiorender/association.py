"""Let VisioRender be the program that opens .vsdx files (Windows only).

Registering writes two things:

* ``HKCU\\Software\\Classes\\.vsdx`` -> our ProgID (user scope, no admin needed)
* ``%LOCALAPPDATA%\\VisioRender\\open_vsdx.py`` -> a launcher that calls
  ``visiorender --open`` no matter which directory the shell starts it in

Everything that touches the registry is built from :func:`registration_table`,
so the mapping is testable without touching HKCU.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import winreg  # exists on Windows only
except ImportError:        # pragma: no cover — non-Windows
    winreg = None          # type: ignore[assignment]

PROGID = 'VisioRender.vsdx'
DESCRIPTION = 'VisioRender drawing'
CLASSES_ROOT = 'Software\\Classes'
ICON_NAME = 'icon.ico'
# Windows keeps a hash-protected per-file "open with" choice that beats every
# key under Software\Classes.  It is user-scope and can only be deleted.
FILE_EXTS = 'Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts'
EXTENSION = '.vsdx'


def require_windows() -> None:
    if sys.platform != 'win32' or winreg is None:
        raise OSError('shell registration is only available on Windows')


def launcher_path() -> Path:
    """Where the shell launcher script lives (per user, no admin)."""
    base = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(base) / 'VisioRender' / 'open_vsdx.py'


def write_launcher() -> Path:
    """Write the tiny script the shell runs; it works from any working dir.

    ``pythonw`` has no console, so any failure would otherwise vanish —
    the launcher appends it to ``%LOCALAPPDATA%\\VisioRender\\open.log``.
    """
    path = launcher_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    package_parent = Path(__file__).resolve().parent.parent
    path.write_text(
        '"""Opened by the VisioRender shell association."""\n'
        'import os\n'
        'import sys\n'
        'import traceback\n'
        'from pathlib import Path\n'
        '\n'
        f'sys.path.insert(0, {str(package_parent)!r})\n'
        '\n'
        'try:\n'
        '    from visiorender.cli import main\n'
        '    raise SystemExit(main(["--open", *sys.argv[1:]]))\n'
        'except SystemExit:\n'
        '    raise\n'
        'except Exception:\n'
        '    log = Path(os.environ.get("LOCALAPPDATA", ".")) / "VisioRender" / "open.log"\n'
        '    log.parent.mkdir(parents=True, exist_ok=True)\n'
        '    log.write_text(repr(sys.argv) + "\\n" + traceback.format_exc(),\n'
        '                   encoding="utf-8", errors="replace")\n'
        '    raise\n',
        encoding='utf-8',
    )
    return path


def launch_command() -> str:
    """The shell command line: console-free python if we have one."""
    python = Path(sys.executable)
    pythonw = python.with_name('pythonw' + python.suffix)
    exe = pythonw if pythonw.exists() else python
    return f'"{exe}" "{launcher_path()}" "%1"'


def icon_path() -> Path | None:
    ico = Path(__file__).resolve().parent / 'assets' / ICON_NAME
    return ico if ico.is_file() else None


def registration_table(command: str, icon: str | None = None) -> dict[str, dict[str, str]]:
    """Keys are relative to HKCU\\Software\\Classes; ``''`` is the default value."""
    table: dict[str, dict[str, str]] = {
        '.vsdx': {'': PROGID},
        PROGID: {'': DESCRIPTION},
        f'{PROGID}\\shell\\open\\command': {'': command},
    }
    if icon:
        table[PROGID]['DefaultIcon'] = icon
    return table


def clear_user_choice() -> bool:
    """Drop the hash-protected UserChoice so the Classes key decides again.

    Windows keeps a per-file "open with" choice under FileExts that beats every
    key under Software\\Classes, and its value is signed with a secret hash:
    it can be deleted, never written.  Returns True when one was removed.
    ``--unregister`` does not need to restore it — without UserChoice the
    extension falls back to the machine default, i.e. pre-registration
    behaviour.
    """
    require_windows()
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                         f'{FILE_EXTS}\\{EXTENSION}\\UserChoice')
    except OSError:
        return False
    return True


def state_path() -> Path:
    """Where we remember what we displaced, so --unregister can put it back."""
    base = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(base) / 'VisioRender' / 'state.json'


def _progids_key() -> str:
    return f'{FILE_EXTS}\\{EXTENSION}\\OpenWithProgids'


def claim_open_with_progids() -> list[str]:
    """Leave only our ProgID in OpenWithProgids; move the rest aside.

    With no UserChoice, Windows resolves the extension through
    OpenWithProgids, so the old handler there beat our Classes default even
    though ``reg query HKCR\\.vsdx`` already said otherwise.  Returns the
    names moved aside; they are remembered in state.json.
    """
    require_windows()
    key_path = _progids_key()
    displaced: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            names = [winreg.EnumValue(key, i)[0]
                     for i in range(winreg.QueryInfoKey(key)[1])]
            for name in names:
                if name != PROGID:
                    displaced.append(name)
                    winreg.DeleteValue(key, name)
    except OSError:
        pass
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, PROGID, 0, winreg.REG_NONE, b'')
    if displaced:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(json.dumps({'displaced_progids': displaced}),
                                encoding='utf-8')
    return displaced


def restore_open_with_progids() -> list[str]:
    """Undo claim_open_with_progids()."""
    require_windows()
    displaced: list[str] = []
    state = state_path()
    if state.is_file():
        try:
            displaced = json.loads(state.read_text(encoding='utf-8')).get(
                'displaced_progids', [])
        except (OSError, ValueError):
            displaced = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _progids_key(), 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            names = [winreg.EnumValue(key, i)[0]
                     for i in range(winreg.QueryInfoKey(key)[1])]
            for name in names:
                if name == PROGID:
                    winreg.DeleteValue(key, name)
            for name in displaced:
                winreg.SetValueEx(key, name, 0, winreg.REG_NONE, b'')
    except OSError:
        pass
    with contextlib.suppress(OSError):
        state.unlink(missing_ok=True)
    return displaced


def register() -> list[str]:
    """Write the association; returns the registry keys it touched."""
    require_windows()

    command = launch_command()
    icon = icon_path()
    written = []
    for path, values in registration_table(command, str(icon) if icon else None).items():
        full = f'{CLASSES_ROOT}\\{path}'
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, full, 0, winreg.KEY_SET_VALUE) as key:
            for name, data in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, data)
        written.append(f'HKCU\\{full}')
    if clear_user_choice():
        written.append(f'HKCU\\{FILE_EXTS}\\{EXTENSION}\\UserChoice (cleared)')
    for name in claim_open_with_progids():
        written.append(f'HKCU\\{_progids_key()} (cleared {name})')
    return written


def unregister() -> list[str]:
    """Remove what register() added; never touches someone else's setup."""
    require_windows()

    removed = []
    for rel in (PROGID, '.vsdx'):
        path = f'{CLASSES_ROOT}\\{rel}'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
                current, _ = winreg.QueryValueEx(key, '')
        except OSError:
            continue
        # .vsdx only goes if it still points at us; the ProgID tree is ours
        if rel == '.vsdx' and current != PROGID:
            continue
        _delete_tree(rel)
        removed.append(f'HKCU\\{path}')
    for name in restore_open_with_progids():
        removed.append(f'HKCU\\{_progids_key()} (restored {name})')
    return removed


def _delete_tree(path: str) -> None:
    """Depth-first: clear values, then children, then the key itself."""
    key_path = f'{CLASSES_ROOT}\\{path}'
    with (contextlib.suppress(OSError),
          winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                         winreg.KEY_READ | winreg.KEY_WRITE) as key):
        subkeys, values, _ = winreg.QueryInfoKey(key)
        for _ in range(values):
            winreg.DeleteValue(key, winreg.EnumValue(key, 0)[0])
        for _ in range(subkeys):
            _delete_tree(f'{path}\\{winreg.EnumKey(key, 0)}')
    with contextlib.suppress(OSError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)


def open_with_default_viewer(path: Path) -> None:
    """Hand the file to whatever the shell says opens it."""
    if sys.platform == 'win32':
        os.startfile(str(path))
    elif sys.platform == 'darwin':
        subprocess.run(['open', str(path)], check=False)
    else:
        subprocess.run(['xdg-open', str(path)], check=False)


__all__ = [
    'DESCRIPTION',
    'PROGID',
    'icon_path',
    'launch_command',
    'launcher_path',
    'open_with_default_viewer',
    'register',
    'registration_table',
    'require_windows',
    'unregister',
    'write_launcher',
]
