"""Tests for VisioRender.

Every test builds the same package with tests/make_fixture.py, so the suite is
self-contained: no Visio, no third-party stencil content, no network.

Coordinates below use mode='full', where the canvas is the page itself:
1 Visio inch == dpi pixels, x right, y down from the top edge.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from tests.make_fixture import build
from visiorender.cli import main
from visiorender.render import render

PAGE_W, PAGE_H, DPI = 6.0, 4.0, 200.0


@pytest.fixture()
def src(tmp_path: Path) -> Path:
    p = tmp_path / 'demo.vsdx'
    p.write_bytes(build())
    return p


@pytest.fixture()
def rendered(src: Path, tmp_path: Path) -> Path:
    out = tmp_path / 'out.png'
    render(src, out, dpi=DPI, mode='full')
    return out


def test_fixture_is_a_valid_package():
    data = build()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        names = set(z.namelist())
        assert {'[Content_Types].xml', '_rels/.rels', 'visio/document.xml',
                'visio/pages/pages.xml', 'visio/pages/page1.xml'} <= names
        for name in names:
            if name.endswith(('.xml', '.rels')):
                assert b'<' in z.read(name)


def test_render_shape_count_and_size(src: Path, tmp_path: Path):
    out = tmp_path / 'out.png'
    path, size, shapes = render(src, out, dpi=DPI, mode='full')
    assert path == str(out)
    assert size == (int(PAGE_W * DPI), int(PAGE_H * DPI))
    assert shapes == 7          # 3 boxes + circle + 3 lines
    assert out.is_file()


def test_solid_fill(rendered: Path):
    img = Image.open(rendered).convert('RGB')
    # box 2: centre (1.4, 2.6), 2.0x1.0 -> sample below the label text
    assert img.getpixel((int(1.4 * DPI), int((PAGE_H - 2.25) * DPI))) == (0xda, 0xe8, 0xfc)


def test_shadow_shape_and_none_mode(src: Path, tmp_path: Path):
    # box 2 right edge at x = 2.4; its shadow sits just outside that edge
    x = int(2.4 * DPI) + 4
    y = int((PAGE_H - 2.6) * DPI)
    with_shadow = tmp_path / 'shadow.png'
    render(src, with_shadow, dpi=DPI, mode='full')
    without = tmp_path / 'no-shadow.png'
    render(src, without, dpi=DPI, mode='full', shadow_mode='none')
    a = Image.open(with_shadow).convert('RGB').getpixel((x, y))[0]
    b = Image.open(without).convert('RGB').getpixel((x, y))[0]
    assert a < 250, 'expected a shadow right of the box'
    assert b >= 254, 'shadow_mode=none must not darken the background'
    assert a < b


def test_gradient_renders_both_stops(rendered: Path):
    img = Image.open(rendered).convert('RGB')
    # box 4: centre (4.4, 1.6), 2.0x1.2 -> top edge y=1.0in, bottom y=2.2in
    top = img.getpixel((int(4.4 * DPI), int((PAGE_H - 1.06) * DPI)))
    bottom = img.getpixel((int(4.4 * DPI), int((PAGE_H - 2.14) * DPI)))
    assert top != bottom, 'gradient did not render'
    # stop 1 #b5c6e7 lands at one end, stop 0 #abbfe4 (75% transparent over
    # white -> ~234,239,248) at the other, whichever way the angle points
    def near(px, rgb, tol=10):
        return all(abs(a - b) <= tol for a, b in zip(px, rgb, strict=True))
    faded = (234, 239, 248)
    solid = (0xb5, 0xc6, 0xe7)
    assert near(top, solid) or near(bottom, solid)
    assert near(top, faded) or near(bottom, faded)


def test_circle_is_round_not_lensed(rendered: Path):
    img = Image.open(rendered).convert('RGB')
    cx, cy, r = int(1.0 * DPI), int((PAGE_H - 0.9) * DPI), int(0.3 * DPI)
    # the four cardinal points of the disc must all be filled: a bezier
    # approximation of the arc leaves the top/bottom of the bbox empty
    for dx, dy in ((0, -r), (0, r), (-r, 0), (r, 0)):
        px = img.getpixel((cx + dx, cy + dy))
        assert px != (255, 255, 255), f'empty at ({dx}, {dy}) — arc is flattened'
    assert img.getpixel((cx, cy - int(r * 0.5))) == (0xfc, 0x85, 0x70)   # disc fill


def test_cross_lines_and_arrowhead(rendered: Path):
    img = Image.open(rendered).convert('RGB')

    def red(px):
        r, g, b = px[:3]
        return r > 150 and g < 90 and b < 90

    # the two crossing lines of the "multiply" node meet at its centre
    assert red(img.getpixel((int(1.0 * DPI), int((PAGE_H - 0.9) * DPI))))
    # line 8 ends at (4.6, 0.6) with EndArrow=4 -> a head at the tip
    assert red(img.getpixel((int(4.6 * DPI), int((PAGE_H - 0.6) * DPI))))
    # the head is wider than the line itself: sample beside the line just
    # before its tip, where only the triangle can reach
    assert red(img.getpixel((int(4.505 * DPI), int((PAGE_H - 0.6) * DPI) + 5))), \
        'no arrowhead: only the head triangle reaches this far'
    # and the line itself is red along the way
    assert red(img.getpixel((int(3.5 * DPI), int((PAGE_H - 0.6) * DPI))))


def test_long_text_wraps(rendered: Path):
    import numpy as np
    img = np.array(Image.open(rendered).convert('L'))
    # box 3: centre (4.4, 3.1), 2.0x1.0 -> interior strip excluding the outline
    x0, x1 = int((4.4 - 1.0) * DPI) + 6, int((4.4 + 1.0) * DPI) - 6
    y0, y1 = int((PAGE_H - 3.6) * DPI) + 6, int((PAGE_H - 2.6) * DPI) - 6
    strip = img[y0:y1, x0:x1] < 90
    rows = strip.sum(axis=1)
    runs, on = [], False
    for n in rows:
        if n >= 3 and not on:
            runs.append(1)
            on = True
        elif n < 3:
            on = False
        elif runs:
            runs[-1] += 1
    assert len(runs) >= 2, 'text did not wrap into two lines'


def test_short_text_is_one_line(rendered: Path):
    import numpy as np
    img = np.array(Image.open(rendered).convert('L'))
    # box 2: centre (1.4, 2.6), 2.0x1.0, label "VisioRender"
    x0, x1 = int((1.4 - 1.0) * DPI) + 6, int((1.4 + 1.0) * DPI) - 6
    y0, y1 = int((PAGE_H - 3.1) * DPI) + 6, int((PAGE_H - 2.1) * DPI) - 6
    strip = img[y0:y1, x0:x1] < 90
    rows = strip.sum(axis=1)
    runs, on = [], False
    for n in rows:
        if n >= 3 and not on:
            runs.append(1)
            on = True
        elif n < 3:
            on = False
        elif runs:
            runs[-1] += 1
    assert len(runs) == 1, f'expected a single line of text, got {len(runs)}'


def test_cli_writes_png(src: Path, tmp_path: Path, capsys):
    out = tmp_path / 'cli.png'
    rc = main([str(src), '-o', str(out), '--dpi', '100', '--mode', 'full'])
    captured = capsys.readouterr()
    assert rc == 0
    assert out.is_file()
    assert 'px' in captured.out and 'shapes' in captured.out


def test_cli_rejects_bad_args(tmp_path: Path, capsys):
    assert main([str(tmp_path / 'missing.vsdx')]) == 2
    assert 'no such file' in capsys.readouterr().err
    src = tmp_path / 'demo.vsdx'
    src.write_bytes(build())
    assert main([str(src), '-o', str(tmp_path / 'x.jpg')]) == 2
    assert 'must be a .png' in capsys.readouterr().err
    assert main([str(src), '--media', str(tmp_path / 'no.png')]) == 2
    assert 'no such media' in capsys.readouterr().err
