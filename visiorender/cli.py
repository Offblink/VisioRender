"""Command line front end: `visiorender <file.vsdx> [options]`."""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

from .association import (
    PROGID,
    launcher_path,
    open_with_default_viewer,
    register,
    unregister,
    write_launcher,
)
from .render import render

SHADOW_MODES = ('explicit', 'all', 'none')


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='visiorender',
        description='Render a Visio .vsdx file to PNG without Visio, or '
                    'become the program that opens .vsdx files.',
        epilog='Both modes draw the pictures the page embeds (raster parts and '
               'EMF/WMF are decoded automatically); --media substitutes your '
               'own PNG for every embedded picture.',
    )
    p.add_argument('src', nargs='?', help='path to the .vsdx file')
    p.add_argument('-o', '--out', default=None,
                   help='output PNG (default: <src stem>.png beside the input)')
    p.add_argument('--dpi', type=float, default=200.0,
                   help='pixels per Visio inch (default: 200)')
    p.add_argument('--mode', choices=('vector', 'full'), default='vector',
                   help="'vector' crops to the drawn shapes and pictures, "
                        "'full' renders the whole page")
    p.add_argument('--media', default=None,
                   help='PNG to substitute for every embedded picture '
                        '(by default embedded pictures are decoded from the '
                        'package)')
    p.add_argument('--shadow-mode', choices=SHADOW_MODES, default='explicit',
                   help="'explicit' shadows only where the file asks for them "
                        "(default), 'all' on every filled shape, 'none' off")
    p.add_argument('--shadow-alpha', type=float, default=0.16,
                   help='peak shadow opacity, 0..1 (default: 0.16)')
    p.add_argument('--shadow-blur', type=float, default=0.030,
                   help='shadow blur radius in Visio inches (default: 0.030)')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--open', action='store_true',
                      help='render, then open the PNG with the default viewer')
    mode.add_argument('--register', action='store_true',
                      help='make VisioRender the program that opens .vsdx files '
                           '(Windows, per user — no admin)')
    mode.add_argument('--unregister', action='store_true',
                      help='undo --register')
    return p


def _register() -> int:
    try:
        written = register()
        launcher = write_launcher()
    except OSError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    for key in written:
        print(f'  {key}')
    print(f'  launcher: {launcher}')
    print(f'.vsdx now opens with VisioRender ({PROGID}); undo with --unregister')
    return 0


def _unregister() -> int:
    try:
        removed = unregister()
    except OSError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    for key in removed:
        print(f'  removed {key}')
    if not removed:
        print('nothing to remove (not registered)')
    else:
        with contextlib.suppress(OSError):
            launcher_path().unlink(missing_ok=True)
        print('.vsdx association restored')
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.register:
        if args.src:
            print('error: --register does not take a file', file=sys.stderr)
            return 2
        return _register()
    if args.unregister:
        if args.src:
            print('error: --unregister does not take a file', file=sys.stderr)
            return 2
        return _unregister()
    if not args.src:
        print('error: the file to render is required', file=sys.stderr)
        return 2

    src = Path(args.src)
    if not src.is_file():
        print(f'error: no such file: {src}', file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else src.with_suffix('.png')
    if out.suffix.lower() != '.png':
        print(f'error: output must be a .png path: {out}', file=sys.stderr)
        return 2
    media = Path(args.media) if args.media else None
    if media is not None and not media.is_file():
        print(f'error: no such media file: {media}', file=sys.stderr)
        return 2

    try:
        path, size, shapes = render(
            src, out, dpi=args.dpi, mode=args.mode, media=media,
            shadow_mode=args.shadow_mode, shadow_alpha=args.shadow_alpha,
            shadow_blur=args.shadow_blur,
        )
    except (OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1

    print(f'{path}  {size[0]}x{size[1]} px  {shapes} shapes  {args.dpi:g} px/in'
          + (f'  mode={args.mode}' if args.mode == 'full' else ''))

    if args.open:
        try:
            open_with_default_viewer(Path(path))
        except OSError as exc:
            print(f'error: could not open {path}: {exc}', file=sys.stderr)
            return 1
        print(f'opened {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
