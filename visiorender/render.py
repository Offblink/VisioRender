"""Read a Visio .vsdx package and rasterise the page — no Visio required.

The package is plain OPC/ZIP + XML: geometry, arrows, text, gradients and
shadows all come from the file itself.  Visio-specific conventions that this
module reproduces (and the places it approximates) are documented in NOTES.md.
"""

from __future__ import annotations

import math
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .media import decode_part

V = '{http://schemas.microsoft.com/office/visio/2012/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def cells_of(el):
    return {c.get('N'): (c.get('V'), c.get('F')) for c in el.findall(V + 'Cell')}


def rows_of(el, section='Geometry'):
    """{row IX: (type, cells)}"""
    out = {}
    for s in el.findall(V + 'Section'):
        if s.get('N') == section:
            for row in s.findall(V + 'Row'):
                out[int(row.get('IX') or 0)] = (row.get('T'), cells_of(row))
    return out


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def val(cells, name, w=None, h=None, default=None):
    """Cell value: formula F evaluated against Width/Height when possible, else V."""
    if name not in cells:
        return default
    v, f = cells[name]
    if f and w is not None:
        expr = re.sub(r'\bWidth\b', repr(w), re.sub(r'\bHeight\b', repr(h), f))
        expr = re.sub(r'\b(\d+(?:\.\d+)?)PT\b', r'\1', expr, flags=re.I)
        expr = re.sub(r'\b(\d+(?:\.\d+)?)MM\b', r'\1', expr, flags=re.I)
        if re.fullmatch(r'[\d\.\+\-\*/\(\)\s]+', expr):
            try:
                return eval(expr, {'__builtins__': {}}, {})
            except Exception:
                pass
    if v not in (None, ''):
        try:
            return float(v)
        except ValueError:
            return v
    return default


# ---------------- masters -------------------------------------------------

ONE_D = {'Straight Line', 'Dynamic connector', 'Mid-arrow', 'Arced arrow',
         'Single arrowhead', 'Dot & arrow', 'Line Arrow'}

# document style sheets (line arrow defaults live here, e.g. style 7 "连接线")

def ms_shape(mxml):
    return mxml.find(V + 'Shapes/' + V + 'Shape')


def arc_through_points(p0, p1, M, steps=64):
    """Visio EllipticalArcTo gives start (p0), end (p1) and a point on the arc
    (A,B).  With D==1 the arc is circular, so it is the circle through p0,p1,M."""
    (ax, ay), (bx, by), (mx, my) = p0, p1, M
    d = 2 * (ax * (by - my) + bx * (my - ay) + mx * (ay - by))
    if abs(d) < 1e-12:
        return None                                   # collinear / open arc
    ux = ((ax * ax + ay * ay) * (by - my) + (bx * bx + by * by) * (my - ay)
          + (mx * mx + my * my) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (mx - bx) + (bx * bx + by * by) * (ax - mx)
          + (mx * mx + my * my) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    if r < 1e-9:
        return None
    a0 = math.atan2(ay - uy, ax - ux)
    a1 = math.atan2(by - uy, bx - ux)
    am = math.atan2(my - uy, mx - ux)
    sweep = (a1 - a0) % (2 * math.pi)
    to_m = (am - a0) % (2 * math.pi)
    if to_m > sweep:
        sweep -= 2 * math.pi                 # pass through M the other way round
    return [(ux + r * math.cos(a0 + sweep * i / steps),
             uy + r * math.sin(a0 + sweep * i / steps)) for i in range(1, steps + 1)]


def gradient_stops(elements):
    """FillGradient rows carry resolved hex stops: (position, color, transparency)."""
    for el in elements:
        if el is None:
            continue
        for sec in el.findall(V + 'Section'):
            if sec.get('N') != 'FillGradient':
                continue
            stops = []
            for row in sec.findall(V + 'Row'):
                if row.get('Del') == '1':
                    continue
                c = cells_of(row)
                col = c.get('GradientStopColor', (None, None))[0]
                if not (col or '').startswith('#'):
                    continue
                pos = float(c.get('GradientStopPosition', ('0', None))[0] or 0)
                tra = float(c.get('GradientStopColorTrans', ('0', None))[0] or 0)
                stops.append((pos, col, tra))
            if stops:
                return sorted(stops)
    return []


def hex_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def paint_gradient(img, box, mask_img, stops, angle):
    """Linear gradient along `angle` (Visio radians, y up) clipped by mask_img."""
    x0, y0, x1, y1 = box
    W, H = x1 - x0, y1 - y0
    if W < 1 or H < 1:
        return
    pos = np.array([s[0] for s in stops], float)
    cols = np.array([hex_rgb(s[1]) for s in stops], float)
    trans = np.array([s[2] for s in stops], float)
    if pos.size == 1:
        pos = np.array([0.0, 1.0])
        cols = np.vstack([cols, cols])
        trans = np.array([trans[0], trans[0]])
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ca, sa = math.cos(angle), -math.sin(angle)          # pixel y grows downwards
    t = xx * ca + yy * sa
    t = (t - t.min()) / max(t.max() - t.min(), 1e-6)
    rgb = np.stack([np.interp(t, pos, cols[:, i]) for i in range(3)], -1).astype(np.uint8)
    a = 1.0 - np.interp(t, pos, trans)
    m = np.asarray(mask_img, np.float32) / 255.0
    alpha = np.clip(m * a, 0, 1)
    img.paste(Image.fromarray(rgb), (x0, y0), Image.fromarray((alpha * 255).astype(np.uint8)))




def draw_shadow(img, it, pts, bx, canvas, blur_in, su):
    """Soft offset silhouette, like Visio's default subtle shadow."""
    dx, dy = it['sh_dx'] * su, it['sh_dy'] * su           # down-right, like Visio's theme shadow
    pad = int(abs(dx) + abs(dy) + blur_in * su * 4) + 2
    x0 = int(max(int(bx[0]) - pad, 0))
    y0 = int(max(int(bx[1]) - pad, 0))
    x1 = int(min(int(bx[2]) + 2 * pad + abs(dx) + 1, canvas[0]))
    y1 = int(min(int(bx[3]) + 2 * pad + abs(dy) + 1, canvas[1]))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    m = Image.new('L', (x1 - x0, y1 - y0), 0)
    md = ImageDraw.Draw(m)
    rel = [(p[0] - x0 + dx, p[1] - y0 + dy) for p in pts]
    if it['rounding'] > 0 and len(pts) == 4:
        md.rounded_rectangle([rel[0][0], rel[0][1], rel[2][0], rel[2][1]],
                             radius=it['rounding'] * su, fill=255)
    else:
        md.polygon(rel, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(blur_in * su))
    m = m.point(lambda v: int(v * it['sh_alpha']))
    img.paste(Image.new('RGB', (x1 - x0, y1 - y0), '#000000'), (x0, y0), m)


def geom_flags(instance, master_shape):
    flags = {}
    for el in (master_shape, instance):
        if el is None:
            continue
        for sec in el.findall(V + 'Section'):
            if sec.get('N') != 'Geometry':
                continue
            for cell in sec.findall(V + 'Cell'):
                if cell.get('N') in ('NoFill', 'NoLine'):
                    flags[cell.get('N')] = cell.get('V')
    return flags


# ---------------- shapes -------------------------------------------------



def render(src, out, *, dpi=200.0, mode='vector', media=None,
           shadow_mode='explicit', shadow_alpha=0.16, shadow_blur=0.030):
    """Rasterise ``src`` into ``out``.

    mode='vector' renders the drawn shapes and the embedded pictures, cropped
    to their bbox; mode='full' renders the whole page.  Embedded pictures are
    decoded from the package (raster parts with Pillow, EMF/WMF with GDI+);
    ``media`` overrides that: your PNG replaces every embedded picture.

    shadow_mode: 'explicit' (only shapes carrying ShdwPattern=1), 'all',
    or 'none'.  Returns (out_path, (width, height), shape_count).
    """
    SRC, OUT, SU = str(src), str(out), float(dpi)
    MODE, MEDIA_PNG = mode, (str(media) if media else None)

    z = zipfile.ZipFile(SRC)
    try:
        prel = z.read('visio/pages/_rels/pages.xml.rels').decode()
    except KeyError as exc:
        raise ValueError('package has no visio/pages/_rels/pages.xml.rels') from exc
    page_targets = [t for _q, t in
                    re.findall(r"Target=(['\"])([^'\"]*page1\.xml)\1", prel)]
    if not page_targets:
        raise ValueError('no page1.xml relationship in visio/pages/_rels/pages.xml.rels')
    tgt = sorted(set(page_targets))[0]
    base = tgt.split('/')[-1]
    names = [n for n in z.namelist() if n.endswith('/' + base) or n == base]
    root = ET.fromstring(z.read(next((n for n in names if '/pages/' in n), names[0])))


    masters = {}
    try:
        mroot = ET.fromstring(z.read('visio/masters/masters.xml'))
        mrels = {r.get('Id'): r.get('Target')
                 for r in ET.fromstring(z.read('visio/masters/_rels/masters.xml.rels'))}
    except KeyError:                      # packages without stencils are legal
        mroot, mrels = None, {}
    for m in (mroot.findall(V + 'Master') if mroot is not None else []):
        rel = m.find(V + 'Rel')
        t = mrels.get(rel.get(R + 'id')) if rel is not None else None
        if not t:
            continue
        name = t.lstrip('/').replace('visio/masters/', '')
        masters[m.get('ID')] = (m.get('NameU'), ET.fromstring(z.read('visio/masters/' + name)))


    styles = {}
    try:
        doc_xml = z.read('visio/document.xml')
    except KeyError:                      # no style sheets: every value comes from the shape
        doc_xml = None
    for ss in (ET.fromstring(doc_xml).iter(V + 'StyleSheet') if doc_xml else []):
        styles[ss.get('ID')] = cells_of(ss)



    shapes = list(root.findall(V + 'Shapes/' + V + 'Shape'))
    items = []
    for sh in shapes:
        if sh.get('Type') == 'Foreign':
            continue
        c = cells_of(sh)
        mname, mxml = masters.get(sh.get('Master'), (None, None))
        msh = ms_shape(mxml) if mxml is not None else None
        mc = cells_of(msh) if msh is not None else {}

        w = val(c, 'Width')
        h = val(c, 'Height')
        if w is None:
            w = val(mc, 'Width', default=1.0)
        if h is None:
            h = val(mc, 'Height', default=1.0)
        w = float(w or 1.0)
        h = float(h or 1.0)

        rows = rows_of(sh)
        mrows = rows_of(msh) if msh is not None else {}
        # merge per row index: instance cells win, missing cells come from the master
        merged = {}
        for ix in sorted(set(rows) | set(mrows)):
            t_i, c_i = rows.get(ix, (None, {}))
            t_m, c_m = mrows.get(ix, (None, {}))
            cells = {}
            for name in ('X', 'Y', 'A', 'B', 'C', 'D'):
                cells[name] = c_i.get(name) or c_m.get(name)
            merged[ix] = (t_i or t_m, cells)

        pinx = float(val(c, 'PinX', default=0.0))
        piny = float(val(c, 'PinY', default=0.0))
        locx = float(val(c, 'LocPinX', w, h, w / 2) or 0.0)
        locy = float(val(c, 'LocPinY', w, h, h / 2) or 0.0)
        ang = float(val(c, 'Angle', default=0.0) or 0.0)
        flx = float(val(c, 'FlipX', default=0.0) or 0.0)
        fly = float(val(c, 'FlipY', default=0.0) or 0.0)

        # Visio resolves BeginX/BeginY/EndX/EndY for 1-D shapes; use them to pin the
        # local geometry (loc pin of 1-D shapes is otherwise inherited from the master
        # and would shift the whole line).
        beg = [val(c, k) for k in ('BeginX', 'BeginY')]
        end = [val(c, k) for k in ('EndX', 'EndY')]
        have_be = all(v is not None for v in beg + end)

        def local_first(pts_in):
            return pts_in[0] if pts_in else (0.0, 0.0)

        def xf(x, y):
            if flx:
                x = w - x
            if fly:
                y = h - y
            dx, dy = x - locx, y - locy
            ca, sa = math.cos(ang), math.sin(ang)
            return pinx + dx * ca - dy * sa, piny + dx * sa + dy * ca

        pts = []
        cur = [0.0, 0.0]
        for ix in sorted(merged):
            t, cc = merged[ix]
            if t in ('MoveTo', 'LineTo', 'RelMoveTo', 'RelLineTo'):
                x = val(cc, 'X', w, h)
                y = val(cc, 'Y', w, h)
                if t.startswith('Rel'):
                    cur = [float(x or 0) * w, float(y or 0) * h]
                else:
                    cur = [cur[0] if x is None else float(x),
                           cur[1] if y is None else float(y)]
                pts.append(tuple(cur))
            elif t == 'EllipticalArcTo':
                ex, ey = val(cc, 'X', w, h), val(cc, 'Y', w, h)
                ax, ay = val(cc, 'A', w, h), val(cc, 'B', w, h)
                p0 = tuple(cur)
                p1 = (float(ex if ex is not None else cur[0]), float(ey if ey is not None else cur[1]))
                kp = None if (ax is None or ay is None) else (float(ax), float(ay))
                out = None
                if kp is not None:
                    out = arc_through_points(p0, p1, kp)
                if out is None:
                    ctl = kp or p0
                    out = []
                    for i in range(1, 25):
                        u = i / 24.0
                        out.append(((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * ctl[0] + u * u * p1[0],
                                    (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * ctl[1] + u * u * p1[1]))
                pts.extend(out)
                cur = list(p1)

        two_point = len(pts) == 2
        if len(pts) < 2:
            pts = [(0, 0), (w, 0), (w, h), (0, h)]
            two_point = False

        if have_be and two_point:
            # simple 1-D line: the file's endpoints are authoritative
            page_pts = [tuple(beg), tuple(end)]
        elif have_be:
            # multi-segment connector: re-derive the loc pin so that the first
            # local point lands exactly on BeginX/BeginY
            p0x, p0y = pts[0]
            if flx:
                p0x = w - p0x
            if fly:
                p0y = h - p0y
            dxb, dyb = beg[0] - pinx, beg[1] - piny
            ca, sa = math.cos(ang), math.sin(ang)
            a = dxb * ca + dyb * sa
            b = -dxb * sa + dyb * ca
            locx, locy = p0x - a, p0y - b
            page_pts = [xf(*p) for p in pts]
        else:
            page_pts = [xf(*p) for p in pts]

        sr = styles.get(sh.get('FillStyle'), {})
        sl = styles.get(sh.get('LineStyle'), {})
        flags = geom_flags(sh, msh)

        is_1d = (mname in ONE_D) or ('BeginX' in c and 'EndX' in c)
        path_closed = (len(pts) > 2 and math.hypot(pts[0][0] - pts[-1][0],
                                                   pts[0][1] - pts[-1][1]) < 0.02 * max(w, h, 1e-6))
        closed = bool(path_closed) and not is_1d

        def paint(cells_list, name):
            for cs in cells_list:
                if name in cs:
                    v = cs[name][0]
                    if v not in (None, ''):
                        return v
            return None

        fill = paint((c, mc, sr), 'FillForegnd')
        if not (fill or '').startswith('#'):
            fill = None
        if paint((c, mc, sr), 'FillPattern') == '0':
            fill = None
        if flags.get('NoFill') == '1' or is_1d:
            fill = None
        stroke = paint((c, mc, sl), 'LineColor')
        if not (stroke or '').startswith('#'):
            stroke = None
        if paint((c, mc, sl), 'LinePattern') == '0' or flags.get('NoLine') == '1':
            stroke = 'NONE'
        else:
            stroke = stroke or '#000000'
        lw = val(c, 'LineWeight', default=None)
        if lw is None:
            lw = val(mc, 'LineWeight', default=0.0035)
        lw = float(lw or 0.0035)

        # arrow heads: instance cell -> master cell -> document style sheet
        sc = styles.get(sh.get('LineStyle'), {})

        def acell(name, default=0.0):
            for cs in (c, mc, sc):
                if name in cs:
                    v, f = cs[name]
                    if v not in (None, '') and f != 'Inh':
                        try:
                            return float(v)
                        except ValueError:
                            return default
            return default

        sh_pattern = paint((c, mc, sr), 'ShdwPattern')
        sh_offx = val(c, 'ShapeShdwOffsetX', default=None)
        if sh_offx is None and msh is not None:
            sh_offx = val(mc, 'ShapeShdwOffsetX', default=None)
        sh_offy = val(c, 'ShapeShdwOffsetY', default=None)
        if sh_offy is None and msh is not None:
            sh_offy = val(mc, 'ShapeShdwOffsetY', default=None)
        stops = gradient_stops([sh, msh]) if paint((c, mc, sr), 'FillGradientEnabled') != '0' else []
        g_angle = float(val(c, 'FillGradientAngle', default=None) or 0.0)
        if 'FillGradientAngle' not in c and msh is not None:
            g_angle = float(val(mc, 'FillGradientAngle', default=None) or 0.0)
        items.append({
            'id': sh.get('ID'), 'pts': page_pts, 'fill': fill, 'stroke': stroke,
            'lw': lw * SU,
            'grad': stops, 'grad_angle': g_angle,
            'shadow': (False if shadow_mode == 'none'
                       else (sh_pattern != '0') if shadow_mode == 'all'
                       else (sh_pattern == '1')),
            'sh_dx': abs(float(sh_offx)) if sh_offx and abs(float(sh_offx)) > 0.005 else 0.0417,
            'sh_dy': abs(float(sh_offy)) if sh_offy and abs(float(sh_offy)) > 0.005 else 0.0417,
            'sh_alpha': min(1.0 - float(val(c, 'ShdwForegndTrans', default=None) or 0.74),
                            shadow_alpha),
            'master': mname, 'closed': closed, 'sh': sh,
            'begin_arrow': acell('BeginArrow'), 'end_arrow': acell('EndArrow'),
            'begin_arrow_size': acell('BeginArrowSize', 2.0),
            'end_arrow_size': acell('EndArrowSize', 2.0),
            'rounding': float(val(c, 'Rounding', default=0.0) or 0.0),
        })

    # ---------------- canvas --------------------------------------------------
    # pasted pictures (Type='Foreign') are page content in both modes, so the
    # vector bbox has to account for them: cropping crops, it does not drop
    foreign = []
    rels = {}
    for n in z.namelist():
        if n.startswith('visio/pages/_rels/') and n.endswith('.rels'):
            for r in ET.fromstring(z.read(n)):
                if r.get('Type', '').endswith('/image'):
                    t = r.get('Target')
                    rels[r.get('Id')] = (t.lstrip('/') if t.startswith('/')
                                         else posixpath.normpath(
                                             n.rsplit('_rels/', 1)[0] + t))
    for sh in shapes:
        if sh.get('Type') != 'Foreign':
            continue
        c = cells_of(sh)
        w, h = float(c['Width'][0]), float(c['Height'][0])
        px, py = float(c['PinX'][0]), float(c['PinY'][0])
        src = None
        # the relationship lives inside <ForeignData>, not under <Shape>
        for r in sh.iter(V + 'Rel'):
            src = rels.get(r.get(R + 'id'))
        foreign.append((px - w / 2, py - h / 2, w, h, src))

    if MODE == 'full':
        pxml = ET.fromstring(z.read('visio/pages/pages.xml'))
        ps = pxml.find('.//' + V + 'PageSheet')
        pc = cells_of(ps)
        minx, maxx = 0.0, float(pc['PageWidth'][0])
        miny, maxy = 0.0, float(pc['PageHeight'][0])
        MARGIN = 0.0
    else:
        xs = [p[0] for it in items for p in it['pts']]
        ys = [p[1] for it in items for p in it['pts']]
        xs += [v for fx, fy, fw, fh, _src in foreign for v in (fx, fx + fw)]
        ys += [v for fx, fy, fw, fh, _src in foreign for v in (fy, fy + fh)]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        MARGIN = 0.35
    Wpx = int((maxx - minx + 2 * MARGIN) * SU)
    Hpx = int((maxy - miny + 2 * MARGIN) * SU)
    img = Image.new('RGB', (Wpx, Hpx), 'white')
    d = ImageDraw.Draw(img, 'RGBA')

    if foreign:
        override = Image.open(MEDIA_PNG).convert('RGB') if MEDIA_PNG else None
        pics = {}
        for fx, fy, fw, fh, src in foreign:
            pic = override
            if pic is None:
                if src not in pics:
                    pics[src] = decode_part(z, src)
                    if pics[src] is None and src:
                        print(f'warning: cannot decode embedded picture: {src}',
                              file=sys.stderr)
                pic = pics[src]
            if pic is None:
                continue
            x0 = (fx - minx + MARGIN) * SU
            y0 = (maxy + MARGIN - (fy + fh)) * SU
            img.paste(pic.resize((int(fw * SU), int(fh * SU))), (int(x0), int(y0)))


    def P(p):
        return ((p[0] - minx + MARGIN) * SU, (maxy + MARGIN - p[1]) * SU)


    fnt_cache = {}


    def font(sz, bold, italic, fam):
        key = (round(sz), bold, italic, fam)
        if key in fnt_cache:
            return fnt_cache[key]
        if fam == 'malgun':
            path = 'C:/Windows/Fonts/malgun.ttf'
        else:
            path = {(1, 1): 'timesbi.ttf', (1, 0): 'timesbd.ttf',
                    (0, 1): 'timesi.ttf', (0, 0): 'times.ttf'}[(int(bold), int(italic))]
            path = 'C:/Windows/Fonts/' + path
        f = ImageFont.truetype(path, key[0])
        fnt_cache[key] = f
        return f


    def draw_arrows(it):
        if it['closed']:
            return
        pts = [P(p) for p in it['pts']]
        col = it['stroke'] if it['stroke'] != 'NONE' else '#000000'
        if col is None or len(pts) < 2:
            return
        for flag, size, which in ((it['end_arrow'], it['end_arrow_size'], 'end'),
                                  (it['begin_arrow'], it['begin_arrow_size'], 'begin')):
            if not flag:
                continue
            p1, p0 = (pts[-1], pts[-2]) if which == 'end' else (pts[0], pts[1])
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            n = math.hypot(dx, dy) or 1.0
            ux, uy = dx / n, dy / n
            L = it['lw'] * (1.6 + 0.5 * max(1.0, size))     # Visio arrow size 1..6
            L = max(L, 10.0)
            hw = L * 0.42
            tip = p1
            b1 = (p1[0] - ux * L + uy * hw, p1[1] - uy * L - ux * hw)
            b2 = (p1[0] - ux * L - uy * hw, p1[1] - uy * L + ux * hw)
            d.polygon([tip, b1, b2], fill=col)

    # pass 1: shapes and connectors, in XML (z-)order
    for it in items:
        pts = [P(p) for p in it['pts']]
        stroke = None if it['stroke'] == 'NONE' else it['stroke']
        wpx = max(1, round(it['lw']))
        if it['closed']:
            bx = [min(p[0] for p in pts), min(p[1] for p in pts),
                  max(p[0] for p in pts), max(p[1] for p in pts)]
            if it['shadow']:
                draw_shadow(img, it, pts, bx, img.size, shadow_blur, SU)
            if it['grad']:
                box = (int(bx[0]), int(bx[1]), int(bx[2]) + 1, int(bx[3]) + 1)
                m = Image.new('L', (max(1, box[2] - box[0]), max(1, box[3] - box[1])), 0)
                md = ImageDraw.Draw(m)
                rel = [(p[0] - box[0], p[1] - box[1]) for p in pts]
                if it['rounding'] > 0 and len(pts) == 4:
                    md.rounded_rectangle([rel[0][0], rel[0][1], rel[2][0], rel[2][1]],
                                         radius=it['rounding'] * SU, fill=255)
                else:
                    md.polygon(rel, fill=255)
                paint_gradient(img, box, m, it['grad'], it['grad_angle'])
            flat = None if it['grad'] else it['fill']
            if flat and it['rounding'] > 0 and len(pts) == 4:
                d.rounded_rectangle(bx, radius=it['rounding'] * SU, fill=flat,
                                    outline=stroke, width=wpx)
            elif len(pts) == 4:
                d.rectangle(bx, fill=flat, outline=stroke, width=wpx)
            else:
                d.polygon(pts, fill=flat, outline=stroke)
                if stroke and wpx > 1:
                    d.line([*pts, pts[0]], fill=stroke, width=wpx, joint='curve')
        elif stroke:
            d.line(pts, fill=stroke, width=wpx, joint='curve')
        draw_arrows(it)

    # pass 3: text
    def text_runs(shape):
        t = shape.find(V + 'Text')
        if t is None:
            return []
        seq = []

        def collect(e, run):
            if e.text:
                seq.append((run, e.text))
            for ch in e:
                if ch.tag == V + 'cp':
                    run = int(ch.get('IX') or 0)
                elif ch.tag == V + 'pp':
                    pass
                else:
                    collect(ch, run)
                if ch.tail:
                    seq.append((run, ch.tail))

        collect(t, 0)
        return seq


    for it in items:
        runs = text_runs(it['sh'])
        if not runs or not any(t.strip() for _, t in runs):
            continue
        crow = {}
        for s in it['sh'].findall(V + 'Section'):
            if s.get('N') == 'Character':
                for r in s.findall(V + 'Row'):
                    crow[int(r.get('IX') or 0)] = cells_of(r)

        def style(run):
            cr = crow.get(run) or crow.get(0) or {}
            col = (cr.get('Color') or (None, None))[0]
            if not (col or '').startswith('#'):
                col = '#000000'
            sz = num((cr.get('Size') or (None, None))[0], 0.1389)
            fam = ((cr.get('Font') or (None, None))[0] or 'Times').lower()
            fam = 'malgun' if 'malgun' in fam else 'times'
            return col, sz, fam

        def measure(chars):
            """width in px of a [(char, run)] sequence"""
            w = 0.0
            buf, buf_run = '', None
            for ch, run in chars:
                if buf and run != buf_run:
                    f = font(style(buf_run)[1] * SU, True, True, style(buf_run)[2])
                    w += d.textlength(buf, font=f)
                    buf = ''
                buf_run = run
                buf += ch
            if buf:
                f = font(style(buf_run)[1] * SU, True, True, style(buf_run)[2])
                w += d.textlength(buf, font=f)
            return w

        # paragraphs -> visual lines, wrapped at the shape's text-block width
        paras = [[]]
        for run, txt in runs:
            for piece in re.split(r'(\r\n|\n|\r)', txt):
                if piece in ('\r\n', '\n', '\r'):
                    paras.append([])
                elif piece:
                    paras[-1].append((run, piece))
        paras = [p for p in paras if p and any(t.strip() for _, t in p)]
        if not paras:
            continue

        xs_ = [p[0] for p in it['pts']]
        ys_ = [p[1] for p in it['pts']]
        box_w_px = (max(xs_) - min(xs_)) * SU
        avail = max(box_w_px - 0.2 * SU, 0.3 * SU)      # Visio text-block side margins


        lines = []
        for para in paras:
            chars = [(ch, run) for run, t in para for ch in t]
            tokens, i = [], 0
            while i < len(chars):
                lead = []
                while i < len(chars) and chars[i][0] == ' ':
                    lead.append(chars[i])
                    i += 1
                core = []
                while i < len(chars) and chars[i][0] != ' ':
                    core.append(chars[i])
                    i += 1
                if core:
                    tokens.append((lead, core))
            line, line_w = [], 0.0
            for lead, core in tokens:
                w_core = measure(core)
                w_lead = measure(lead) if line else 0.0
                if line and line_w + w_lead + w_core > avail:
                    lines.append(line)
                    line, line_w = list(core), w_core
                else:
                    line.extend(lead if line else [])
                    line.extend(core)
                    line_w += w_lead + w_core
            lines.append(line)
        lines = [ln for ln in lines if ln]

        size_max = max(style(r)[1] for ln in lines for _, r in ln)
        lh_in = size_max * 1.25
        cx = (min(xs_) + max(xs_)) / 2
        cy = (min(ys_) + max(ys_)) / 2
        n = len(lines)
        top = cy + n * lh_in / 2.0
        for i, ln in enumerate(lines):
            yc = top - (i + 0.5) * lh_in
            segs = []                       # consecutive chars sharing a run -> (run, text)
            for ch, rn in ln:
                if segs and segs[-1][0] == rn:
                    segs[-1][1] += ch
                else:
                    segs.append([rn, ch])
            drawn = []
            tot = 0.0
            for rn, t in segs:
                col, sz, fam = style(rn)
                f = font(sz * SU, True, True, fam)
                wdt = d.textlength(t, font=f)
                drawn.append((t, f, col, wdt))
                tot += wdt
            x = (cx - minx + MARGIN) * SU - tot / 2.0
            ypix = (maxy + MARGIN - yc) * SU
            for t, f, col, wdt in drawn:
                d.text((x, ypix), t, font=f, fill=col, anchor='lm')
                x += wdt


    img.save(OUT)
    return OUT, img.size, len(items)




__all__ = ['render']
