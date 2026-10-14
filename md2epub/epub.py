"""Write the book model as an EPUB 3 file (with an EPUB 2 NCX for older readers)."""

from __future__ import annotations

import re
import zipfile
from html import escape
from importlib.resources import files
from pathlib import Path

from lxml import etree

from . import parse
from .book import Book, Chapter, Image, TocEntry, warn
from .cover import HEIGHT, WIDTH, render_cover

CODE_STYLE = "friendly"
CODE_STYLE_DARK = "github-dark"
TOC_PAGE_MIN_CHAPTERS = 3
NIGHT = [':root[__ibooks_internal_theme*="Night"] pre', ':root[__ibooks_internal_theme*="Gray"] pre']

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def e(text: str) -> str:
    return escape(text, quote=True)


def xhtml(title: str, lang: str, body: str, body_attrs: str = "", head: str = "", css: bool = True) -> str:
    link = '\n    <link rel="stylesheet" type="text/css" href="../styles/book.css"/>' if css else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{e(lang)}" xml:lang="{e(lang)}">
  <head>
    <meta charset="UTF-8"/>
    <title>{e(title)}</title>{link}{head}
  </head>
  <body{body_attrs}>
{body}
  </body>
</html>
"""


def serialize(elements: list) -> str:
    return "\n".join(etree.tostring(el, method="xml", encoding="unicode", with_tail=False) for el in elements)


def chapter_page(book: Book, chapter: Chapter) -> str:
    content = serialize(chapter.body)
    if chapter.title is None:
        section = f'<section class="chapter untitled">\n{content}\n</section>'
    else:
        section = f'<section class="chapter" epub:type="chapter" role="doc-chapter">\n{content}\n</section>'
    return xhtml(chapter.title or book.meta.title, book.meta.lang, section, ' epub:type="bodymatter"')


def title_page(book: Book) -> str:
    meta = book.meta
    parts = [f'<h1 class="title" epub:type="title">{e(meta.title)}</h1>']
    if meta.subtitle:
        parts.append(f'<p class="subtitle" epub:type="subtitle">{e(meta.subtitle)}</p>')
    parts.append('<hr class="title-rule"/>')
    for author in meta.authors:
        parts.append(f'<p class="author">{e(author)}</p>')
    imprint = [x for x in (meta.publisher, (meta.date or "")[:4] or None) if x]
    if imprint:
        parts.append(f'<p class="imprint">{e(" · ".join(imprint))}</p>')
    body = '<section class="titlepage" epub:type="titlepage">\n' + "\n".join(parts) + "\n</section>"
    return xhtml(meta.title, meta.lang, body, ' epub:type="frontmatter"')


def cover_page(book: Book, image: Image) -> str:
    width, height = image.width or WIDTH, image.height or HEIGHT
    head = "\n    <style>html, body { margin: 0; padding: 0; height: 100%; } svg { display: block; }</style>"
    body = (
        '<section class="cover" epub:type="cover">\n'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" '
        f'width="100%" height="100%" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">\n'
        f'<image width="{width}" height="{height}" xlink:href="../images/{e(image.name)}"/>\n'
        "</svg>\n</section>"
    )
    return xhtml(book.meta.title, book.meta.lang, body, ' epub:type="cover"', head=head, css=False)


def toc_list(entries: list[TocEntry], indent: str = "") -> str:
    items = []
    for entry in entries:
        child = f"\n{toc_list(entry.children, indent + '  ')}\n{indent}  " if entry.children else ""
        items.append(f'{indent}  <li><a href="{e(entry.href)}">{e(entry.title)}</a>{child}</li>')
    return f"{indent}<ol>\n" + "\n".join(items) + f"\n{indent}</ol>"


def nav_page(book: Book, has_cover: bool) -> str:
    first = book.chapters[0].filename
    landmarks = []
    if has_cover:
        landmarks.append('<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>')
    landmarks += [
        '<li><a epub:type="titlepage" href="title.xhtml">Title Page</a></li>',
        '<li><a epub:type="toc" href="nav.xhtml#toc">Contents</a></li>',
        f'<li><a epub:type="bodymatter" href="{first}">Start</a></li>',
    ]
    body = (
        f'<nav epub:type="toc" id="toc" role="doc-toc">\n<h1 class="chapter-title">{e(book.meta.contents_label)}</h1>\n'
        f"{toc_list(book.toc)}\n</nav>\n"
        '<nav epub:type="landmarks" id="landmarks" hidden="hidden">\n<ol>\n'
        + "\n".join(landmarks)
        + "\n</ol>\n</nav>"
    )
    return xhtml(book.meta.contents_label, book.meta.lang, body, ' epub:type="frontmatter"')


def ncx(book: Book) -> str:
    counter = iter(range(1, 10_000))

    def points(entries: list[TocEntry], indent: str) -> str:
        out = []
        for entry in entries:
            n = next(counter)
            out.append(
                f'{indent}<navPoint id="np-{n}" playOrder="{n}">\n'
                f"{indent}  <navLabel><text>{e(entry.title)}</text></navLabel>\n"
                f'{indent}  <content src="text/{e(entry.href)}"/>\n'
                + (points(entry.children, indent + "  ") + "\n" if entry.children else "")
                + f"{indent}</navPoint>"
            )
        return "\n".join(out)

    depth = 2 if any(entry.children for entry in book.toc) else 1
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{e(book.meta.lang)}">
  <head>
    <meta name="dtb:uid" content="{e(book.meta.identifier)}"/>
    <meta name="dtb:depth" content="{depth}"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{e(book.meta.title)}</text></docTitle>
  <navMap>
{points(book.toc, "    ")}
  </navMap>
</ncx>
"""


def package(book: Book, manifest: list[tuple[str, str, str, str]], spine: list[str], cover_id: str | None) -> str:
    meta = book.meta
    lines = [
        f'<dc:identifier id="bookid">{e(meta.identifier)}</dc:identifier>',
        f'<dc:title id="title">{e(meta.title)}</dc:title>',
        '<meta refines="#title" property="title-type">main</meta>',
    ]
    if meta.subtitle:
        lines += [
            f'<dc:title id="subtitle">{e(meta.subtitle)}</dc:title>',
            '<meta refines="#subtitle" property="title-type">subtitle</meta>',
        ]
    for n, author in enumerate(meta.authors, start=1):
        lines += [
            f'<dc:creator id="creator{n}">{e(author)}</dc:creator>',
            f'<meta refines="#creator{n}" property="role" scheme="marc:relators">aut</meta>',
        ]
    lines.append(f"<dc:language>{e(meta.lang)}</dc:language>")
    if meta.date:
        if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?(T.*)?", meta.date):
            lines.append(f"<dc:date>{e(meta.date)}</dc:date>")
        else:
            warn(f"date is not in YYYY-MM-DD form, left out of the metadata: {meta.date}")
    for tag, value in (("publisher", meta.publisher), ("description", meta.description), ("rights", meta.rights)):
        if value:
            lines.append(f"<dc:{tag}>{e(value)}</dc:{tag}>")
    lines += [f"<dc:subject>{e(subject)}</dc:subject>" for subject in meta.subjects]
    lines.append(f'<meta property="dcterms:modified">{meta.modified.strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>')
    if cover_id:
        lines.append(f'<meta name="cover" content="{cover_id}"/>')

    items = []
    for item_id, href, media_type, properties in manifest:
        props = f' properties="{properties}"' if properties else ""
        items.append(f'    <item id="{item_id}" href="{e(href)}" media-type="{media_type}"{props}/>')
    itemrefs = [f'    <itemref idref="{idref}"/>' for idref in spine]
    guide = []
    if cover_id:
        guide.append('    <reference type="cover" title="Cover" href="text/cover.xhtml"/>')
    guide.append(f'    <reference type="text" title="Start" href="text/{book.chapters[0].filename}"/>')

    metadata = "\n".join("    " + line for line in lines)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{e(meta.lang)}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
{metadata}
  </metadata>
  <manifest>
{chr(10).join(items)}
  </manifest>
  <spine toc="ncx">
{chr(10).join(itemrefs)}
  </spine>
  <guide>
{chr(10).join(guide)}
  </guide>
</package>
"""


def stylesheet(extra_css: str | None) -> str:
    css = files("md2epub").joinpath("book.css").read_text(encoding="utf-8")
    css += "\n/* Code highlighting (Pygments). */\n" + parse.token_css("pre", CODE_STYLE) + "\n"
    css += "\n/* Apple Books night and gray themes. */\n" + parse.token_css(NIGHT, CODE_STYLE_DARK) + "\n"
    if extra_css:
        css += "\n/* User stylesheet. */\n" + extra_css
    return css


def check_xml(name: str, text: str) -> None:
    try:
        etree.fromstring(text.encode("utf-8"))
    except etree.XMLSyntaxError as error:
        raise RuntimeError(f"generated {name} is not well-formed XML: {error}") from error


def write_epub(book: Book, output: Path, extra_css: str | None = None) -> None:
    files_out: dict[str, bytes] = {}
    manifest: list[tuple[str, str, str, str]] = []
    spine: list[str] = []

    def add_text(item_id: str, href: str, text: str, media_type: str = "application/xhtml+xml", props: str = ""):
        if href.endswith((".xhtml", ".opf", ".ncx")):
            check_xml(href, text)
        files_out[f"EPUB/{href}"] = text.encode("utf-8")
        manifest.append((item_id, href, media_type, props))

    cover = book.cover
    if cover is None and book.generate_cover:
        data = render_cover(book.meta.title, book.meta.subtitle, book.meta.authors)
        cover = Image(Path("cover.png"), "cover.png", "image/png", data, WIDTH, HEIGHT)
    cover_id = None
    if cover is not None:
        cover_id = "cover-image"
        files_out[f"EPUB/images/{cover.name}"] = cover.data
        manifest.append((cover_id, f"images/{cover.name}", cover.media_type, "cover-image"))
        add_text("cover", "text/cover.xhtml", cover_page(book, cover), props="svg")
        spine.append("cover")

    add_text("titlepage", "text/title.xhtml", title_page(book))
    spine.append("titlepage")
    add_text("nav", "text/nav.xhtml", nav_page(book, cover is not None), props="nav")
    if len(book.toc) >= TOC_PAGE_MIN_CHAPTERS:
        spine.append("nav")
    for chapter in book.chapters:
        add_text(chapter.id, f"text/{chapter.filename}", chapter_page(book, chapter))
        spine.append(chapter.id)

    add_text("ncx", "toc.ncx", ncx(book), "application/x-dtbncx+xml")
    add_text("css", "styles/book.css", stylesheet(extra_css), "text/css")
    for image in book.images:
        files_out[f"EPUB/images/{image.name}"] = image.data
        manifest.append((Path(image.name).stem, f"images/{image.name}", image.media_type, ""))

    opf = package(book, manifest, spine, cover_id)
    check_xml("package.opf", opf)

    date_time = book.meta.modified.timetuple()[:6]

    def entry(name: str, compress: int) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=date_time)
        info.compress_type = compress
        info.create_system = 3
        info.external_attr = 0o644 << 16
        return info

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(entry("mimetype", zipfile.ZIP_STORED), b"application/epub+zip")
        archive.writestr(entry("META-INF/container.xml", zipfile.ZIP_DEFLATED), CONTAINER.encode("utf-8"))
        archive.writestr(entry("EPUB/package.opf", zipfile.ZIP_DEFLATED), opf.encode("utf-8"))
        for name, data in files_out.items():
            archive.writestr(entry(name, zipfile.ZIP_DEFLATED), data)
