"""Embedded pictures: extraction, decoding, override — in both modes.

Each test swaps the fixture page for one carrying a <Shape Type='Foreign'>
and its media part, so the suite stays self-contained: no Visio, no
third-party content, no network.
"""

from __future__ import annotations

import ctypes
import io
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from tests.make_fixture import NS, REL, build, cell, rect
from visiorender.media import decode
from visiorender.render import render

PAGE_W, PAGE_H, DPI = 6.0, 4.0, 100.0
PIC_X, PIC_Y, PIC_W, PIC_H = 3.0, 2.0, 2.0, 1.2      # picture centre on the page


def foreign_page(extra_shapes: str = '') -> str:
    return (f"<?xml version='1.0' encoding='utf-8'?>"
            f"<PageContents xmlns='{NS}' xmlns:r='{REL}' xml:space='preserve'>"
            "<Shapes><Shape ID='9' NameU='Picture.9' Name='Picture.9' Type='Foreign'>"
            + cell('PinX', f'{PIC_X}') + cell('PinY', f'{PIC_Y}')
            + cell('Width', f'{PIC_W}') + cell('Height', f'{PIC_H}')
            + cell('LocPinX', f'{PIC_W / 2}', 'Width*0.5')
            + cell('LocPinY', f'{PIC_H / 2}', 'Height*0.5')
            + "<ForeignData ForeignType='EnhMetaFile'><Rel r:id='rId1'/></ForeignData>"
            + '</Shape>' + extra_shapes + '</Shapes></PageContents>')


def with_picture(pic: bytes, ext: str = '.png', extra_shapes: str = '') -> bytes:
    """The demo package with page1 replaced by the foreign picture (and more)."""
    with zipfile.ZipFile(io.BytesIO(build())) as z:
        items = {n: z.read(n) for n in z.namelist()}
    ctype = {'png': 'image/png', 'emf': 'image/x-emf'}[ext[1:]]
    items['[Content_Types].xml'] = items['[Content_Types].xml'].replace(
        b'</Types>', f"<Default Extension='{ext[1:]}' ContentType='{ctype}'/></Types>".encode())
    items['visio/pages/page1.xml'] = foreign_page(extra_shapes).encode()
    items['visio/pages/_rels/page1.xml.rels'] = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        f"<Relationship Id='rId1' Type='{REL}/image' Target='../media/image1{ext}'/>"
        '</Relationships>').encode()
    items[f'visio/media/image1{ext}'] = pic
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in items.items():
            z.writestr(name, data)
    return buf.getvalue()


def png_bytes(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(buf, 'PNG')
    return buf.getvalue()


def red_rect_emf() -> bytes:
    """A real EMF, recorded through GDI: red rectangle filling its frame."""
    g32 = ctypes.WinDLL('gdi32')
    g32.CreateEnhMetaFileW.restype = ctypes.c_void_p
    g32.CreateEnhMetaFileW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_wchar_p, ctypes.c_void_p]
    g32.CloseEnhMetaFile.restype = ctypes.c_void_p
    g32.CloseEnhMetaFile.argtypes = [ctypes.c_void_p]
    g32.GetEnhMetaFileBits.restype = ctypes.c_uint
    g32.GetEnhMetaFileBits.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
    g32.DeleteEnhMetaFile.argtypes = [ctypes.c_void_p]

    hdc = g32.CreateEnhMetaFileW(None, None, None, None)
    assert hdc
    pen = g32.CreatePen(0, 1, 0x000000FF)           # PS_SOLID, red
    brush = g32.CreateSolidBrush(0x000000FF)        # red
    g32.SelectObject(hdc, pen)
    g32.SelectObject(hdc, brush)
    g32.Rectangle(hdc, 0, 0, 200, 120)
    hemf = g32.CloseEnhMetaFile(hdc)
    size = g32.GetEnhMetaFileBits(hemf, 0, None)
    buf = ctypes.create_string_buffer(size)
    g32.GetEnhMetaFileBits(hemf, size, buf)
    g32.DeleteEnhMetaFile(hemf)
    return buf.raw[:size]


def render_pic(tmp_path: Path, data: bytes, mode: str = 'full',
               **kw) -> tuple[Image.Image, Path]:
    src = tmp_path / 'pic.vsdx'
    src.write_bytes(data)
    out = tmp_path / 'out.png'
    render(src, out, dpi=DPI, mode=mode, **kw)
    return Image.open(out).convert('RGB'), out


def picture_center(img: Image.Image) -> tuple[int, int, int]:
    """Centre of the picture rect on the *full* page at 100 dpi."""
    return img.getpixel((int(PIC_X * DPI), int((PAGE_H - PIC_Y) * DPI)))


def test_auto_decodes_embedded_png(tmp_path: Path):
    img, _ = render_pic(tmp_path, with_picture(png_bytes((255, 0, 0))))
    assert picture_center(img) == (255, 0, 0)
    assert img.getpixel((10, 10)) == (255, 255, 255)     # page stays white


def test_media_argument_overrides_embedded(tmp_path: Path):
    override = tmp_path / 'mine.png'
    override.write_bytes(png_bytes((0, 200, 0)))
    img, _ = render_pic(tmp_path, with_picture(png_bytes((255, 0, 0))),
                        media=override)
    assert picture_center(img) == (0, 200, 0)


@pytest.mark.skipif(sys.platform != 'win32', reason='GDI metafile recording is Windows-only')
def test_emf_part_is_rasterised(tmp_path: Path):
    img, _ = render_pic(tmp_path, with_picture(red_rect_emf(), ext='.emf'))
    assert picture_center(img) == (255, 0, 0)


def test_undecodable_part_warns_and_stays_blank(tmp_path: Path, capsys):
    img, _ = render_pic(tmp_path, with_picture(b'not a picture', ext='.png'))
    assert picture_center(img) == (255, 255, 255)
    assert 'cannot decode embedded picture: visio/media/image1.png' in capsys.readouterr().err


def test_unknown_extension_is_not_decoded():
    assert decode(b'anything', 'shape.svg') is None


def test_vector_mode_draws_picture_and_crops_to_it(tmp_path: Path):
    """vector = crop only: the picture is content, not something to drop."""
    img, _ = render_pic(tmp_path, with_picture(png_bytes((255, 0, 0))), mode='vector')
    w, h = img.size
    assert abs(w - (PIC_W + 0.7) * DPI) <= 1      # picture + the 0.35in margins
    assert abs(h - (PIC_H + 0.7) * DPI) <= 1
    assert w < PAGE_W * DPI and h < PAGE_H * DPI  # cropped, not the whole page
    assert img.getpixel((w // 2, h // 2)) == (255, 0, 0)


def test_vector_canvas_covers_shapes_and_picture(tmp_path: Path):
    """The vector bbox is the union of drawn shapes and embedded pictures."""
    box = rect(2, 1.0, 3.4, 1.0, 0.6, '#dae8fc')   # x 0.5..1.5, y 3.1..3.7
    img, _ = render_pic(tmp_path, with_picture(png_bytes((255, 0, 0)),
                                               extra_shapes=box), mode='vector')
    w, h = img.size
    assert abs(w - (3.5 + 0.7) * DPI) <= 1        # x 0.5..4.0
    assert abs(h - (2.3 + 0.7) * DPI) <= 1        # y 1.4..3.7
    assert img.getpixel((int((PIC_X - 0.5 + 0.35) * DPI),
                         int((3.7 + 0.35 - PIC_Y) * DPI))) == (255, 0, 0)
    assert img.getpixel((int((1.0 - 0.5 + 0.35) * DPI),
                         int((3.7 + 0.35 - 3.4) * DPI))) == (0xda, 0xe8, 0xfc)
