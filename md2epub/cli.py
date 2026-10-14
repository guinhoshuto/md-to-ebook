"""Command line: md2epub book.md [-o book.epub]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .book import build_book
from .epub import write_epub


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="md2epub",
        description="Convert a Markdown file into a well-typeset EPUB 3 book.",
        epilog="Metadata can also come from YAML front matter: title, subtitle, author, lang, date, "
        "publisher, description, rights, tags, identifier/isbn, cover.",
    )
    p.add_argument("input", type=Path, help="the Markdown file")
    p.add_argument("-o", "--output", type=Path, help="the EPUB to write (default: next to the input)")
    p.add_argument("--title", help="book title (default: front matter, then the single top heading, then the file name)")
    p.add_argument("--subtitle", help="book subtitle")
    p.add_argument("--author", action="append", default=[], help="author name; repeat for several")
    p.add_argument("--lang", help="language tag such as pt-BR or en (default: detected from the text)")
    cover = p.add_mutually_exclusive_group()
    cover.add_argument("--cover", type=Path, help="cover image (default: front matter, else a generated cover)")
    cover.add_argument("--no-cover", action="store_true", help="leave the book without a cover")
    p.add_argument("--css", type=Path, help="extra stylesheet appended after the built-in one")
    p.add_argument("--split-level", type=int, choices=range(1, 7), metavar="N",
                   help="heading level that starts a chapter (default: chosen from the document)")
    p.add_argument("--toc-depth", type=int, choices=(1, 2), default=2, help="levels in the table of contents")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    source: Path = args.input
    if not source.is_file():
        print(f"error: no such file: {source}", file=sys.stderr)
        return 1
    output = args.output or source.with_suffix(".epub")
    overrides = {"title": args.title, "subtitle": args.subtitle, "author": args.author, "lang": args.lang}
    cover = False if args.no_cover else args.cover
    book = build_book(source, overrides, split_level=args.split_level, toc_depth=args.toc_depth, cover=cover)
    extra_css = args.css.read_text(encoding="utf-8") if args.css else None
    write_epub(book, output, extra_css)
    titled = sum(1 for chapter in book.chapters if chapter.title)
    print(
        f"{output}  ({book.meta.title!r}, {book.meta.lang}, {len(book.chapters)} sections, "
        f"{titled} in the contents, {len(book.images)} images, {output.stat().st_size / 1024:.0f} KB)"
    )
    return 0
