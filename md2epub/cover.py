"""Generate a typographic cover: a solid colour, a double frame, the title, subtitle and author."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1600, 2560

# (background, text, accent): dark cloth colours with cream text and a gilt accent.
PALETTES = [
    ("#1f2a44", "#f3ebdd", "#c9a86a"),
    ("#24372c", "#efe9da", "#bfa46f"),
    ("#5b2121", "#f2e8dc", "#d6ab7d"),
    ("#2b2b2e", "#ede6da", "#c4a26b"),
    ("#1d4048", "#f0e9dc", "#d8b882"),
    ("#3a2541", "#f1e7de", "#cfa77e"),
]

SYSTEM = Path("/System/Library/Fonts")
FONTS = {
    "regular": [
        (SYSTEM / "Supplemental/Baskerville.ttc", 0),
        (SYSTEM / "Supplemental/Iowan Old Style.ttc", 0),
        (SYSTEM / "Supplemental/Georgia.ttf", 0),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"), 0),
        (Path("C:/Windows/Fonts/georgia.ttf"), 0),
    ],
    "italic": [
        (SYSTEM / "Supplemental/Baskerville.ttc", 2),
        (SYSTEM / "Supplemental/Iowan Old Style.ttc", 2),
        (SYSTEM / "Supplemental/Georgia Italic.ttf", 0),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"), 0),
        (Path("C:/Windows/Fonts/georgiai.ttf"), 0),
    ],
}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path, index in FONTS[kind]:
        if path.is_file():
            return ImageFont.truetype(str(path), size, index=index)
    return ImageFont.load_default(size)


def palette_for(title: str) -> tuple[str, str, str]:
    digest = hashlib.sha256(title.encode("utf-8")).digest()
    return PALETTES[digest[0] % len(PALETTES)]


def wrap(text: str, face: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        candidate = f"{lines[-1]} {word}" if lines else word
        if lines and face.getlength(candidate) <= max_width:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines


def fit(text: str, kind: str, start: int, smallest: int, max_width: int, max_lines: int):
    """Largest font size (stepping down from start) at which text wraps within the limits."""
    size = start
    while True:
        face = font(kind, size)
        lines = wrap(text, face, max_width)
        widest = max((face.getlength(line) for line in lines), default=0)
        if (len(lines) <= max_lines and widest <= max_width) or size <= smallest:
            return face, lines
        size -= 6


def draw_lines(draw, lines, face, top: float, fill: str, leading: float) -> float:
    for line in lines:
        draw.text((WIDTH / 2, top), line, font=face, fill=fill, anchor="ma")
        top += face.size * leading
    return top


def draw_spaced(draw, text: str, face, centre_y: float, fill: str, tracking: float) -> None:
    widths = [face.getlength(ch) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = (WIDTH - total) / 2
    for ch, width in zip(text, widths):
        draw.text((x, centre_y), ch, font=face, fill=fill, anchor="lm")
        x += width + tracking


def render_cover(title: str, subtitle: str | None = None, authors: list[str] | None = None) -> bytes:
    background, ink, accent = palette_for(title)
    image = Image.new("RGB", (WIDTH, HEIGHT), background)
    draw = ImageDraw.Draw(image)

    draw.rectangle((72, 72, WIDTH - 73, HEIGHT - 73), outline=accent, width=5)
    draw.rectangle((96, 96, WIDTH - 97, HEIGHT - 97), outline=accent, width=2)

    title_face, title_lines = fit(title, "regular", 210, 96, 1180, 5)
    leading = 1.14
    block = title_face.size * leading * len(title_lines)
    top = max(520, 1050 - block / 2)
    bottom = draw_lines(draw, title_lines, title_face, top, ink, leading)

    rule_y = bottom + 70
    draw.line((WIDTH / 2 - 110, rule_y, WIDTH / 2 + 110, rule_y), fill=accent, width=4)
    draw.ellipse((WIDTH / 2 - 9, rule_y - 9, WIDTH / 2 + 9, rule_y + 9), fill=accent)

    if subtitle:
        sub_face, sub_lines = fit(subtitle, "italic", 84, 52, 1120, 4)
        draw_lines(draw, sub_lines, sub_face, rule_y + 80, ink, 1.25)

    if authors:
        names = " · ".join(authors).upper()
        author_face, _ = fit(names, "regular", 64, 36, 1100, 1)
        while author_face.getlength(names) + 14 * len(names) > 1180 and author_face.size > 30:
            author_face = font("regular", author_face.size - 4)
        draw_spaced(draw, names, author_face, HEIGHT - 330, accent, tracking=14)

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()
