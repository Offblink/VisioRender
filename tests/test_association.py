"""Tests for the ".vsdx opens with VisioRender" feature."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

from tests.make_fixture import build
from visiorender import association
from visiorender.cli import main

WINDOWS = pytest.mark.skipif(sys.platform != 'win32', reason='Windows registry')


def test_registration_table_layout():
    table = association.registration_table('"py.exe" "x.py" "%1"')
    assert table['.vsdx'][''] == association.PROGID
    assert table[association.PROGID][''] == association.DESCRIPTION
    assert table[f'{association.PROGID}\\shell\\open\\command'][''].endswith('"%1"')
    assert 'DefaultIcon' not in table[association.PROGID]      # no icon file -> no key


def test_registration_table_with_icon():
    table = association.registration_table('cmd', r'C:\i\icon.ico')
    assert table[association.PROGID]['DefaultIcon'] == r'C:\i\icon.ico'


def test_launcher_writes_path_and_entrypoint(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    path = association.write_launcher()
    assert path == tmp_path / 'VisioRender' / 'open_vsdx.py'
    body = path.read_text(encoding='utf-8')
    assert 'sys.path.insert' in body            # works from any working dir
    assert '["--open"' in body or "['--open'" in body
    assert 'visiorender.cli import main' in body


def test_launch_command_targets_launcher(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    cmd = association.launch_command()
    assert cmd.endswith('"%1"')
    assert 'open_vsdx.py' in cmd
    exe = cmd.split('"')[1]
    assert Path(exe).exists()
    assert Path(exe).name.lower().startswith('python')


@WINDOWS
def test_register_unregister_roundtrip(tmp_path: Path, monkeypatch):
    """Everything under a test-only registry root; real .vsdx is untouched."""
    import winreg

    root = 'Software\\ClassesVisioRenderTest'
    monkeypatch.setattr(association, 'CLASSES_ROOT', root)
    monkeypatch.setattr(association, 'PROGID', 'VisioRenderTest.vsdx')
    monkeypatch.setattr(association, 'DESCRIPTION', 'VisioRender test')
    monkeypatch.setattr(association, 'FILE_EXTS', f'{root}\\FileExts')
    monkeypatch.setattr(association, 'EXTENSION', '.vsdx')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))

    # seed the two things Windows resolves through: UserChoice + old handler
    uc = f'{root}\\FileExts\\.vsdx\\UserChoice'
    progids = f'{root}\\FileExts\\.vsdx\\OpenWithProgids'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, uc) as key:
        winreg.SetValueEx(key, 'ProgId', 0, winreg.REG_SZ, 'OldHandler.vsdx')
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, progids) as key:
        winreg.SetValueEx(key, 'OldHandler.vsdx', 0, winreg.REG_NONE, b'')

    written = association.register()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f'{root}\\.vsdx') as key:
            assert winreg.QueryValueEx(key, '')[0] == 'VisioRenderTest.vsdx'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            f'{root}\\VisioRenderTest.vsdx\\shell\\open\\command') as key:
            assert winreg.QueryValueEx(key, '')[0].endswith('"%1"')
        assert any('(cleared)' in k for k in written)          # UserChoice dropped
        assert any('OldHandler.vsdx' in k for k in written)    # old handler moved aside
        with pytest.raises(FileNotFoundError):                 # UserChoice really gone
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, uc)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, progids) as key:
            names = [winreg.EnumValue(key, i)[0]
                     for i in range(winreg.QueryInfoKey(key)[1])]
        assert names == ['VisioRenderTest.vsdx']              # only us left
        assert association.state_path().read_text(encoding='utf-8') == \
            '{"displaced_progids": ["OldHandler.vsdx"]}'
    finally:
        removed = association.unregister()
    assert any('restored OldHandler.vsdx' in k for k in removed)
    for path in (f'{root}\\.vsdx', f'{root}\\VisioRenderTest.vsdx'):
        with pytest.raises(OSError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, progids) as key:   # handler back
        assert winreg.QueryValueEx(key, 'OldHandler.vsdx')[1] == winreg.REG_NONE
    assert not association.state_path().exists()

    def drop_tree(sub: str) -> None:                              # test-only root
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as key:
                for _ in range(winreg.QueryInfoKey(key)[0]):
                    drop_tree(f'{sub}\\{winreg.EnumKey(key, 0)}')
        except OSError:
            return
        with contextlib.suppress(OSError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)

    drop_tree(root)


def test_cli_requires_a_file(capsys):
    assert main([]) == 2
    assert 'file to render is required' in capsys.readouterr().err


def test_cli_register_rejects_file(tmp_path: Path, capsys):
    src = tmp_path / 'a.vsdx'
    src.write_bytes(build())
    assert main(['--register', str(src)]) == 2
    assert 'does not take a file' in capsys.readouterr().err


def test_cli_open_renders_then_opens(tmp_path: Path, monkeypatch, capsys):
    src = tmp_path / 'demo.vsdx'
    src.write_bytes(build())
    opened: list[Path] = []
    monkeypatch.setattr('visiorender.cli.open_with_default_viewer',
                        lambda p: opened.append(Path(p)))
    rc = main(['--open', str(src), '--dpi', '100', '--mode', 'full'])
    out = capsys.readouterr()
    assert rc == 0
    assert opened == [tmp_path / 'demo.png']
    assert 'opened' in out.out and 'shapes' in out.out
