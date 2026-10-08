"""Decode the picture parts a .vsdx page embeds (full mode pastes them).

Raster parts (PNG/JPEG/GIF/BMP/TIFF) are read with Pillow.  EMF/WMF — what
Visio itself writes — are rasterised through GDI+, which ships with every
Windows; Pillow has no metafile decoder.  Anything undecodable returns None
and the picture slot stays blank (render also prints a warning).
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

RASTER_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp'}
METAFILE_EXT = {'.emf', '.wmf'}

_PIX_FMT_32ARGB = 0x26200A
_LOCK_READ = 1
_ARGB_OPAQUE_WHITE = -1            # 0xFFFFFFFF as the signed INT GDI+ wants


def decode_part(z: zipfile.ZipFile, name: str) -> Image.Image | None:
    """Decode the package part ``name`` (e.g. 'visio/media/image1.emf')."""
    if not name:
        return None
    try:
        data = z.read(name)
    except KeyError:
        return None
    return decode(data, name)


def decode(data: bytes, name: str) -> Image.Image | None:
    """Decode picture bytes ``data``; ``name`` only supplies the extension."""
    ext = Path(name).suffix.lower()
    try:
        if ext in RASTER_EXT:
            im = Image.open(io.BytesIO(data))
            im.load()
            return _to_rgb(im)
        if ext in METAFILE_EXT and sys.platform == 'win32':
            return _gdiplus(data, ext)
    except OSError:                 # unreadable/corrupt part
        return None
    return None


def _to_rgb(im: Image.Image) -> Image.Image:
    if im.mode == 'RGB':
        return im
    if 'A' in im.getbands() or 'transparency' in im.info:
        rgba = im.convert('RGBA')
        flat = Image.new('RGB', rgba.size, 'white')   # drop alpha on white
        flat.paste(rgba, (0, 0), rgba)
        return flat
    return im.convert('RGB')


# ---- GDI+ metafile rasterisation ------------------------------------------

class _StartupInput(ctypes.Structure):
    _fields_ = [('Version', ctypes.c_uint),
                ('DebugEventCallback', ctypes.c_void_p),
                ('SuppressBackgroundThread', ctypes.c_int),
                ('SuppressExternalCodecs', ctypes.c_int)]


class _Rect(ctypes.Structure):
    _fields_ = [('X', ctypes.c_int), ('Y', ctypes.c_int),
                ('Width', ctypes.c_int), ('Height', ctypes.c_int)]


class _BitmapData(ctypes.Structure):
    _fields_ = [('Width', ctypes.c_uint), ('Height', ctypes.c_uint),
                ('Stride', ctypes.c_int), ('PixelFormat', ctypes.c_uint),
                ('Scan0', ctypes.c_void_p), ('Reserved', ctypes.c_uint)]


def _gdiplus(data: bytes, ext: str) -> Image.Image | None:
    """Rasterise metafile bytes through gdiplus.dll (Windows only).

    Load from a temp file (GdipLoadImageFromFile), draw onto a 32bpp ARGB
    bitmap cleared opaque white, then LockBits the pixels out as RGB.
    """
    gp = ctypes.WinDLL('gdiplus')
    token = ctypes.c_void_p()
    if gp.GdiplusStartup(ctypes.byref(token), ctypes.byref(_StartupInput(1, None, 0, 0)),
                         None) != 0:
        return None
    img = bmp = graph = None
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=ext)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        if gp.GdipLoadImageFromFile(ctypes.c_wchar_p(tmp), ctypes.byref(img := ctypes.c_void_p())) != 0:
            return None
        w, h = ctypes.c_uint(), ctypes.c_uint()
        gp.GdipGetImageWidth(img, ctypes.byref(w))
        gp.GdipGetImageHeight(img, ctypes.byref(h))
        if not (w.value and h.value):
            return None
        if gp.GdipCreateBitmapFromScan0(w.value, h.value, 0, _PIX_FMT_32ARGB, None,
                                        ctypes.byref(bmp := ctypes.c_void_p())) != 0:
            return None
        gp.GdipGetImageGraphicsContext(bmp, ctypes.byref(graph := ctypes.c_void_p()))
        gp.GdipGraphicsClear(graph, _ARGB_OPAQUE_WHITE)
        # REAL parameters: without argtypes ctypes passes them as C ints and
        # the destination rect becomes garbage (GDI+ then returns GenericError)
        gp.GdipDrawImageRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_float, ctypes.c_float,
                                         ctypes.c_float, ctypes.c_float]
        if gp.GdipDrawImageRect(graph, img, 0.0, 0.0,
                                float(w.value), float(h.value)) != 0:
            return None
        bd = _BitmapData()
        rect = _Rect(0, 0, w.value, h.value)
        if gp.GdipBitmapLockBits(bmp, ctypes.byref(rect), _LOCK_READ, _PIX_FMT_32ARGB,
                                 ctypes.byref(bd)) != 0:
            return None
        stride = abs(bd.Stride)
        buf = ctypes.string_at(bd.Scan0, stride * bd.Height)
        rows = np.frombuffer(buf, np.uint8).reshape(bd.Height, stride)
        bgra = rows[:, :w.value * 4].reshape(bd.Height, w.value, 4)
        return Image.fromarray(bgra[:, :, [2, 1, 0]].copy())
    except (OSError, ValueError):   # GDI+ load/draw failure
        return None
    finally:
        if graph and graph.value:
            gp.GdipDeleteGraphics(graph)
        if bmp and bmp.value:
            gp.GdipDisposeImage(bmp)      # bitmaps are GpImage too
        if img and img.value:
            gp.GdipDisposeImage(img)
        gp.GdiplusShutdown(token)
        if tmp:
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
