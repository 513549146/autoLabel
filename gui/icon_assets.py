"""Runtime access to the generated review-workspace icon set."""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image, ImageChops, ImageTk


_SPRITE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "ui-icons-v2.png")
_NAV_SPRITE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "nav-icons-v1.png")
_POSITIONS = {
    "back": (0, 0),
    "forward": (1, 0),
    "pan": (2, 0),
    "box": (3, 0),
    "undo": (0, 1),
    "redo": (1, 1),
    "delete": (2, 1),
    "save": (3, 1),
}


@lru_cache(maxsize=1)
def _sprite():
    return Image.open(_SPRITE_PATH).convert("RGBA")


@lru_cache(maxsize=1)
def _nav_sprite():
    return Image.open(_NAV_SPRITE_PATH).convert("RGBA")


def icon(name, size=24):
    """Return a transparent PhotoImage cropped from the generated icon sprite."""
    image = _sprite()
    col, row = _POSITIONS[name]
    cell_w, cell_h = image.width // 4, image.height // 2
    cell = image.crop((col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)).convert("RGB")
    diff = ImageChops.difference(cell, Image.new("RGB", cell.size, "white"))
    bbox = diff.getbbox() or (0, 0, cell.width, cell.height)
    margin = 18
    left = max(0, bbox[0] - margin)
    top = max(0, bbox[1] - margin)
    right = min(cell.width, bbox[2] + margin)
    bottom = min(cell.height, bbox[3] + margin)
    glyph = _transparent(cell.crop((left, top, right, bottom)))
    glyph.thumbnail((size, size), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    result.alpha_composite(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2))
    return ImageTk.PhotoImage(result)


def nav_icon(name, size=19):
    positions = {"auto": (0, 0), "review": (1, 0), "approved": (0, 1), "settings": (1, 1)}
    image = _nav_sprite()
    col, row = positions[name]
    cell_w, cell_h = image.width // 2, image.height // 2
    cell = image.crop((col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)).convert("RGB")
    diff = ImageChops.difference(cell, Image.new("RGB", cell.size, "white"))
    bbox = diff.getbbox() or (0, 0, cell.width, cell.height)
    margin = 24
    glyph = _transparent(cell.crop((max(0, bbox[0] - margin), max(0, bbox[1] - margin),
                                   min(cell.width, bbox[2] + margin), min(cell.height, bbox[3] + margin))))
    glyph.thumbnail((size, size), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    result.alpha_composite(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2))
    return ImageTk.PhotoImage(result)


def _transparent(image):
    """Convert the generated white matte into alpha while retaining antialiasing."""
    rgb = image.convert("RGB")
    difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white"))
    alpha = difference.convert("L").point(lambda value: min(255, value * 4))
    rgba = rgb.convert("RGBA")
    rgba.putalpha(alpha)
    return rgba
