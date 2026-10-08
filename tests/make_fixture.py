"""Write a small .vsdx package from scratch.

Everything here is authored in this file, so the test fixture ships nothing
third-party: geometry, arrows, text, a gradient and a shadow are all on the
page.  Run it directly to (re)write tests/fixtures/demo.vsdx.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

NS = "http://schemas.microsoft.com/office/visio/2012/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
OUT = Path(__file__).parent / 'fixtures' / 'demo.vsdx'

PAGE_W, PAGE_H = 6.0, 4.0


def cell(n: str, v: str, f: str | None = None) -> str:
    return f"<Cell N='{n}' V='{v}'" + (f" F='{f}'/>" if f else '/>')


def text_block(body: str, size: str = '0.1389', color: str = '#000000',
               style: str = '1') -> str:
    return ("<Section N='Character'><Row IX='0'>"
            + cell('Font', 'Times New Roman') + cell('Color', color)
            + cell('Style', style) + cell('Size', size)
            + "</Row></Section><Text><cp IX='0'/>" + body + "</Text>")


def rect(sid: int, cx: float, cy: float, w: float, h: float, fill: str,
         line: str = '#6c8ebf', rounding: str = '0.08', extra: str = '',
         body: str = '', size: str = '0.1389') -> str:
    r = rounding
    geom = ('<Section N=\'Geometry\' IX=\'0\'>'
            "<Row T='MoveTo' IX='1'>" + cell('X', '0') + cell('Y', '0') + '</Row>'
            "<Row T='LineTo' IX='2'>" + cell('X', f'{w}') + cell('Y', '0') + '</Row>'
            "<Row T='LineTo' IX='3'>" + cell('X', f'{w}') + cell('Y', f'{h}') + '</Row>'
            "<Row T='LineTo' IX='4'>" + cell('X', '0') + cell('Y', f'{h}') + '</Row>'
            "<Row T='LineTo' IX='5'>" + cell('X', '0') + cell('Y', '0') + '</Row>'
            '</Section>')
    return (f"<Shape ID='{sid}' NameU='Box.{sid}' Name='Box.{sid}' Type='Shape'>"
            + cell('PinX', f'{cx}') + cell('PinY', f'{cy}')
            + cell('Width', f'{w}') + cell('Height', f'{h}')
            + cell('LocPinX', f'{w / 2}', 'Width*0.5')
            + cell('LocPinY', f'{h / 2}', 'Height*0.5')
            + cell('Angle', '0') + cell('FlipX', '0') + cell('FlipY', '0')
            + cell('FillPattern', '1') + cell('FillForegnd', fill)
            + cell('LineColor', line) + cell('LinePattern', '1')
            + cell('Rounding', r)
            + extra + geom + (text_block(body, size) if body else '')
            + '</Shape>')


def line(sid: int, x0: float, y0: float, x1: float, y1: float,
         extra: str = '') -> str:
    length = abs(x1 - x0) or abs(y1 - y0)
    return (f"<Shape ID='{sid}' NameU='Line.{sid}' Name='Line.{sid}' Type='Shape'>"
            + cell('PinX', f'{(x0 + x1) / 2}') + cell('PinY', f'{(y0 + y1) / 2}')
            + cell('Width', f'{length}') + cell('LocPinX', f'{length / 2}')
            + cell('LocPinY', '0') + cell('Angle', '0')
            + cell('BeginX', f'{x0}') + cell('BeginY', f'{y0}')
            + cell('EndX', f'{x1}') + cell('EndY', f'{y1}')
            + cell('LineColor', '#ee0000') + cell('LineWeight', '0.020833333333333332', '1.5PT')
            + extra
            + "<Section N='Geometry' IX='0'><Row T='MoveTo' IX='1'>"
            + cell('X', '0') + cell('Y', '0') + "</Row><Row T='LineTo' IX='2'>"
            + cell('X', f'{length}') + cell('Y', '0') + '</Row></Section>'
            + '</Shape>')


def circle(sid: int, cx: float, cy: float, d: float, fill: str) -> str:
    r, half = d / 2, d / 2
    geom = ("<Section N='Geometry' IX='0'>"
            "<Row T='MoveTo' IX='1'>" + cell('X', '0') + cell('Y', f'{half}') + '</Row>'
            "<Row T='EllipticalArcTo' IX='2'>" + cell('X', f'{d}') + cell('Y', f'{half}')
            + cell('A', f'{r}') + cell('B', f'{d}') + '</Row>'
            "<Row T='EllipticalArcTo' IX='3'>" + cell('X', '0') + cell('Y', f'{half}')
            + cell('A', f'{r}') + cell('B', '0') + '</Row>'
            '</Section>')
    return (f"<Shape ID='{sid}' NameU='Circle.{sid}' Name='Circle.{sid}' Type='Shape'>"
            + cell('PinX', f'{cx}') + cell('PinY', f'{cy}')
            + cell('Width', f'{d}') + cell('Height', f'{d}')
            + cell('LocPinX', f'{r}', 'Width*0.5') + cell('LocPinY', f'{r}', 'Height*0.5')
            + cell('Angle', '0') + cell('FillPattern', '1')
            + cell('FillForegnd', fill) + cell('LineColor', fill) + cell('LinePattern', '1')
            + geom + '</Shape>')


def build_page() -> str:
    shadow = (cell('ShdwPattern', '1') + cell('ShdwForegnd', '#000000')
              + cell('ShdwForegndTrans', '0.74')
              + cell('ShapeShdwOffsetX', '0.04166666666666666')
              + cell('ShapeShdwOffsetY', '0.04166666666666666')
              + cell('ShapeShdwType', '1'))
    gradient = (cell('FillGradientEnabled', '1')
                + cell('FillGradientAngle', '1.5707963267948966')
                + "<Section N='FillGradient'>"
                + "<Row IX='0'>" + cell('GradientStopColor', '#abbfe4')
                + cell('GradientStopColorTrans', '0.75')
                + cell('GradientStopPosition', '0') + '</Row>'
                + "<Row IX='1'>" + cell('GradientStopColor', '#b5c6e7')
                + cell('GradientStopColorTrans', '0')
                + cell('GradientStopPosition', '1') + '</Row>'
                + '<Row IX=\'2\' Del=\'1\'/><Row IX=\'3\' Del=\'1\'/>'
                + '<Row IX=\'4\' Del=\'1\'/><Row IX=\'5\' Del=\'1\'/>'
                + '<Row IX=\'6\' Del=\'1\'/><Row IX=\'7\' Del=\'1\'/>'
                + '<Row IX=\'8\' Del=\'1\'/><Row IX=\'9\' Del=\'1\'/>'
                + '</Section>')

    shapes = [
        rect(2, 1.4, 2.6, 2.0, 1.0, '#dae8fc', extra=shadow,
             body='VisioRender', size='0.1667'),
        rect(3, 4.4, 3.1, 2.0, 1.0, '#fff2cc', line='#d6b656',
             body='Train free Byte Tokenizer must wrap across lines', size='0.1667'),
        rect(4, 4.4, 1.6, 2.0, 1.2, '#b5c6e7', line='#7f8fb5',
             extra=gradient, body='gradient'),
        circle(5, 1.0, 0.9, 0.6, '#fc8570'),
        line(6, 0.73, 0.63, 1.27, 1.17),
        line(7, 0.73, 1.17, 1.27, 0.63),
        line(8, 2.4, 0.6, 4.6, 0.6, extra=cell('EndArrow', '4') + cell('EndArrowSize', '6')),
    ]
    return (f"<?xml version='1.0' encoding='utf-8'?>"
            f"<PageContents xmlns='{NS}' xmlns:r='{REL}' xml:space='preserve'>"
            f"<Shapes>{''.join(shapes)}</Shapes></PageContents>")


def build() -> bytes:
    content_types = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
        "<Default Extension='rels' ContentType='application/vnd.openxmlformats-package.relationships+xml'/>"
        "<Default Extension='xml' ContentType='application/xml'/>"
        "<Override PartName='/visio/document.xml' "
        "ContentType='application/vnd.ms-visio.drawing.main+xml'/>"
        "<Override PartName='/visio/pages/pages.xml' "
        "ContentType='application/vnd.ms-visio.pages+xml'/>"
        "<Override PartName='/visio/pages/page1.xml' "
        "ContentType='application/vnd.ms-visio.page+xml'/>"
        "</Types>")
    root_rels = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        f"<Relationship Id='rId1' Type='{REL}/document' Target='visio/document.xml'/>"
        '</Relationships>')
    doc_rels = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        f"<Relationship Id='rId1' Type='{REL}/pages' Target='pages/pages.xml'/>"
        '</Relationships>')
    pages_rels = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        f"<Relationship Id='rId1' Type='{REL}/page' Target='page1.xml'/>"
        '</Relationships>')
    document = (
        f"<?xml version='1.0' encoding='utf-8'?>"
        f"<Document xmlns='{NS}' xmlns:r='{REL}' xml:space='preserve'>"
        "<StyleSheets>"
        "<StyleSheet ID='0' Name='No Style' NameU='No Style'>"
        + cell('LineWeight', '0.01041666666666667') + cell('LinePattern', '1')
        + cell('Rounding', '0') + '</StyleSheet>'
        '</StyleSheets></Document>')
    pages = (
        f"<?xml version='1.0' encoding='utf-8'?>"
        f"<Pages xmlns='{NS}' xmlns:r='{REL}' xml:space='preserve'>"
        "<Page ID='0' NameU='Page-1' Name='Page-1' ViewScale='1'>"
        "<PageSheet>"
        + cell('PageWidth', f'{PAGE_W}') + cell('PageHeight', f'{PAGE_H}')
        + '</PageSheet></Page></Pages>')

    buf = {}
    buf['[Content_Types].xml'] = content_types
    buf['_rels/.rels'] = root_rels
    buf['visio/document.xml'] = document
    buf['visio/_rels/document.xml.rels'] = doc_rels
    buf['visio/pages/pages.xml'] = pages
    buf['visio/pages/_rels/pages.xml.rels'] = pages_rels
    buf['visio/pages/page1.xml'] = build_page()

    from io import BytesIO
    out = BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in ('[Content_Types].xml', '_rels/.rels', 'visio/document.xml',
                     'visio/_rels/document.xml.rels', 'visio/pages/pages.xml',
                     'visio/pages/_rels/pages.xml.rels', 'visio/pages/page1.xml'):
            zf.writestr(name, buf[name])
    return out.getvalue()


def write(path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build())
    return path


if __name__ == '__main__':
    print('wrote', write())
