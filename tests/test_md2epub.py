"""End-to-end tests: build EPUBs from small Markdown sources and inspect the archive."""

from __future__ import annotations

import io
import os
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from lxml import etree
from PIL import Image

from md2epub.cli import main
from md2epub.parse import detect_language

NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "x": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "svg": "http://www.w3.org/2000/svg",
    "xlink": "http://www.w3.org/1999/xlink",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample.md"


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1760000000")


def build(tmp_path: Path, markdown: str, *args: str, name: str = "book.md", mtime: int | None = None):
    source = tmp_path / name
    source.write_text(markdown, encoding="utf-8")
    if mtime is not None:
        os.utime(source, (mtime, mtime))
    output = tmp_path / "out.epub"
    assert main([str(source), "-o", str(output), *args]) == 0
    return zipfile.ZipFile(output)


def xml(archive: zipfile.ZipFile, name: str):
    return etree.fromstring(archive.read(name))


def opf(archive: zipfile.ZipFile):
    return xml(archive, "EPUB/package.opf")


def manifest(archive: zipfile.ZipFile) -> dict[str, etree._Element]:
    return {item.get("id"): item for item in opf(archive).iterfind(".//opf:manifest/opf:item", NS)}


def chapters(archive: zipfile.ZipFile) -> list[str]:
    return sorted(n for n in archive.namelist() if re.fullmatch(r"EPUB/text/ch\d{3}\.xhtml", n))


def text(el) -> str:
    return " ".join("".join(el.itertext()).split())


TWO_CHAPTERS = """# My Book

## One

First chapter.[^a] See [later](#two).

## Two

Second chapter cites a note.[^b] Back to [one](#one).

[^a]: Note in chapter one.
[^b]: Note in chapter two.
"""


# ------------------------------------------------------------------ package rules

def test_mimetype_is_first_and_stored(tmp_path):
    archive = build(tmp_path, SAMPLE.read_text())
    first = archive.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED
    assert archive.read("mimetype") == b"application/epub+zip"
    container = xml(archive, "META-INF/container.xml")
    rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    assert rootfile.get("full-path") == "EPUB/package.opf"


def test_manifest_lists_exactly_the_files_in_the_archive(tmp_path):
    archive = build(tmp_path, SAMPLE.read_text(), "--cover", str(SAMPLE.parent / "page-grid.svg"))
    items = manifest(archive)
    listed = {posixpath.join("EPUB", item.get("href")) for item in items.values()}
    packaged = set(archive.namelist()) - {"mimetype", "META-INF/container.xml", "EPUB/package.opf"}
    assert listed == packaged
    spine = [ref.get("idref") for ref in opf(archive).iterfind(".//opf:spine/opf:itemref", NS)]
    assert set(spine) <= set(items)
    assert [i for i, item in items.items() if "nav" in (item.get("properties") or "").split()] == ["nav"]
    assert [i for i, item in items.items() if item.get("properties") == "cover-image"] == ["cover-image"]


def test_every_document_is_well_formed_xml(tmp_path):
    archive = build(tmp_path, SAMPLE.read_text())
    documents = [n for n in archive.namelist() if n.endswith((".xhtml", ".opf", ".ncx", ".xml"))]
    assert len(documents) >= 9
    for name in documents:
        etree.fromstring(archive.read(name))


def test_every_link_and_fragment_resolves(tmp_path):
    archive = build(tmp_path, SAMPLE.read_text() + "\n" + TWO_CHAPTERS.replace("# My Book", ""))
    names = set(archive.namelist())

    def check(doc: str, href: str) -> None:
        parts = urlsplit(href)
        if parts.scheme:
            return
        target = posixpath.normpath(posixpath.join(posixpath.dirname(doc), unquote(parts.path))) if parts.path else doc
        assert target in names, f"{doc}: {href}"
        if parts.fragment:
            ids = xml(archive, target).xpath("//@id")
            assert parts.fragment in ids, f"{doc}: {href}"

    checked = 0
    for doc in (n for n in names if n.endswith(".xhtml")):
        tree = xml(archive, doc)
        refs = tree.xpath("//x:a/@href | //x:img/@src | //x:link/@href | //svg:image/@xlink:href", namespaces=NS)
        for href in refs:
            check(doc, href)
            checked += 1
    for src in xml(archive, "EPUB/toc.ncx").xpath("//ncx:content/@src", namespaces=NS):
        check("EPUB/toc.ncx", src)
        checked += 1
    assert checked >= 20


def test_ids_are_unique_xml_names(tmp_path):
    markdown = "# Book\n\n## 1. Start\n\ntext\n\n## 1. Start\n\nagain\n\n## Ação & Reação\n\nfim\n"
    archive = build(tmp_path, markdown)
    for doc in chapters(archive):
        ids = xml(archive, doc).xpath("//@id")
        assert len(ids) == len(set(ids)), doc
        for value in ids:
            assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value), value
    all_ids = [i for doc in chapters(archive) for i in xml(archive, doc).xpath("//@id")]
    assert "acao-reacao" in all_ids
    assert len(all_ids) == len(set(all_ids))


def test_output_is_byte_for_byte_reproducible(tmp_path):
    first = build(tmp_path, SAMPLE.read_text(), mtime=1_000_000_000)
    data = Path(first.filename).read_bytes()
    second = build(tmp_path, SAMPLE.read_text(), mtime=1_100_000_000)
    assert Path(second.filename).read_bytes() == data
    modified = opf(second).xpath('//opf:meta[@property="dcterms:modified"]/text()', namespaces=NS)
    assert modified == ["2025-10-09T08:53:20Z"]  # SOURCE_DATE_EPOCH wins over the file time


# ------------------------------------------------------------------ structure

def test_single_top_heading_becomes_the_title(tmp_path):
    archive = build(tmp_path, "# My Book\n\nAn opening paragraph.\n\n" + TWO_CHAPTERS.split("\n", 2)[2])
    assert opf(archive).findtext(".//dc:title", namespaces=NS) == "My Book"
    files = chapters(archive)
    assert len(files) == 3  # untitled opening + two chapters
    headings = [text(h) for doc in files for h in xml(archive, doc).iterfind(".//x:h1", NS)]
    assert headings == ["One", "Two"]
    toc = xml(archive, "EPUB/text/nav.xhtml").xpath('//x:nav[@id="toc"]/x:ol/x:li/x:a', namespaces=NS)
    assert [text(a) for a in toc] == ["One", "Two"]


def test_several_top_headings_each_start_a_chapter(tmp_path):
    archive = build(tmp_path, "# A\n\nx\n\n## A1\n\ny\n\n# B\n\nz\n", name="my-field_notes.md")
    assert opf(archive).findtext(".//dc:title", namespaces=NS) == "My Field Notes"
    assert len(chapters(archive)) == 2
    top = xml(archive, "EPUB/text/nav.xhtml").xpath('//x:nav[@id="toc"]/x:ol/x:li', namespaces=NS)
    assert [text(li.find("x:a", NS)) for li in top] == ["A", "B"]
    assert [text(a) for a in top[0].iterfind("x:ol/x:li/x:a", NS)] == ["A1"]
    assert len(xml(archive, chapters(archive)[0]).findall(".//x:h2", NS)) == 1


def test_split_level_override(tmp_path):
    archive = build(tmp_path, "# A\n\nx\n\n## A1\n\ny\n\n# B\n\nz\n", "--split-level", "2")
    assert len(chapters(archive)) == 2  # "A" and its text open untitled; A1 onwards is the second
    assert opf(archive).findtext(".//dc:title", namespaces=NS) == "Book"


def test_footnote_lands_in_the_chapter_that_cites_it(tmp_path):
    archive = build(tmp_path, TWO_CHAPTERS)
    one, two = (xml(archive, doc) for doc in chapters(archive))
    note_b = two.xpath('//x:aside[@id="fn2"]', namespaces=NS)
    assert len(note_b) == 1 and note_b[0].get(EPUB_TYPE) == "footnote"
    assert "Note in chapter two." in text(note_b[0])
    assert "↩" not in text(note_b[0])
    assert not one.xpath('//x:aside[@id="fn2"]', namespaces=NS)
    ref = two.xpath('//x:a[@href="#fn2"]', namespaces=NS)[0]
    assert ref.get(EPUB_TYPE) == "noteref" and text(ref) == "2"


def test_links_across_chapters_point_at_the_right_file(tmp_path):
    archive = build(tmp_path, TWO_CHAPTERS)
    one, two = (xml(archive, doc) for doc in chapters(archive))
    assert one.xpath('//x:a[text()="later"]/@href', namespaces=NS) == ["ch002.xhtml#two"]
    assert two.xpath('//x:a[text()="one"]/@href', namespaces=NS) == ["ch001.xhtml#one"]


def test_front_matter_and_flags(tmp_path):
    markdown = "---\ntitle: From YAML\nauthor: [Ana, Bia]\nlang: fr\ntags: ['#poesia']\n---\n\nTexte.\n"
    archive = build(tmp_path, markdown, "--title", "From Flag")
    package = opf(archive)
    assert package.findtext(".//dc:title", namespaces=NS) == "From Flag"
    assert [c.text for c in package.iterfind(".//dc:creator", NS)] == ["Ana", "Bia"]
    assert package.findtext(".//dc:language", namespaces=NS) == "fr"
    assert package.findtext(".//dc:subject", namespaces=NS) == "poesia"


# ------------------------------------------------------------------ text

def test_language_detection():
    assert detect_language("Isso não é uma frase em inglês, você sabe. Ela está aqui para o teste.") == "pt-BR"
    assert detect_language("This is the sentence that we use for the test, and it is English.") == "en"
    assert detect_language("Esta es la frase que usamos para la prueba, y está en español con los datos.") == "es"
    assert detect_language("1234 5678") == "en"


def test_quotes_follow_the_language(tmp_path):
    pt = build(tmp_path, 'Ela disse "não é você", e isso é uma coisa que está aqui.\n')
    assert "“não é você”" in pt.read(chapters(pt)[0]).decode()
    es = build(tmp_path, 'Ella dijo "es la casa" y los datos están con el resto del texto.\n')
    assert "«es la casa»" in es.read(chapters(es)[0]).decode()


def test_code_is_highlighted(tmp_path):
    archive = build(tmp_path, "```python\ndef f():\n    return 1\n```\n")
    page = archive.read(chapters(archive)[0]).decode()
    assert "<span class=\"tok-k\">def</span>" in page
    assert "pre .tok-k" in archive.read("EPUB/styles/book.css").decode()


def test_scripts_and_event_handlers_are_removed(tmp_path, capsys):
    archive = build(tmp_path, '<script>alert(1)</script>\n\n<p onclick="go()">Hi</p>\n')
    page = archive.read(chapters(archive)[0]).decode()
    assert "<script" not in page and "onclick" not in page and "Hi" in page
    assert "removed unsupported <script>" in capsys.readouterr().err


# ------------------------------------------------------------------ images and cover

def png(path: Path, size=(40, 30), color="red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def test_local_image_is_packaged_as_a_figure(tmp_path, capsys):
    png(tmp_path / "pics" / "dot.png")
    archive = build(tmp_path, "Intro.\n\n![A red dot](pics/dot.png)\n\nGone: ![x](nope.png)\n")
    page = xml(archive, chapters(archive)[0])
    figure = page.find(".//x:figure", NS)
    assert figure.find("x:img", NS).get("src") == "../images/img-001.png"
    assert text(figure.find("x:figcaption", NS)) == "A red dot"
    assert manifest(archive)["img-001"].get("media-type") == "image/png"
    assert len(page.findall(".//x:img", NS)) == 1
    assert "image not found: nope.png" in capsys.readouterr().err


def test_obsidian_syntax(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    png(tmp_path / "attachments" / "Pasted image.png")
    notes = tmp_path / "notes"
    notes.mkdir()
    markdown = (
        "See [[Other Note|the alias]] and [[Plain]].\n\n"
        "![[Pasted image.png|200]]\n\n"
        "> [!warning] Careful\n> Mind the gap.\n\n"
        "This is ==important== text.\n"
    )
    archive = build(notes, markdown)
    page = xml(archive, chapters(archive)[0])
    body = text(page.find(".//x:section", NS))
    assert "See the alias and Plain." in body
    img = page.find(".//x:img", NS)
    assert img.get("src") == "../images/img-001.png" and img.get("width") == "200"
    callout = page.find('.//x:div[@class="callout callout-warning"]', NS)
    assert text(callout.find("x:p", NS)) == "Careful"
    assert "Mind the gap." in text(callout)
    assert text(page.find(".//x:mark", NS)) == "important"


def test_generated_cover(tmp_path):
    archive = build(tmp_path, "---\ntitle: A Title\nauthor: Someone\n---\n\nText.\n")
    with Image.open(io.BytesIO(archive.read("EPUB/images/cover.png"))) as cover:
        assert cover.size == (1600, 2560)
    spine = [ref.get("idref") for ref in opf(archive).iterfind(".//opf:spine/opf:itemref", NS)]
    assert spine[:2] == ["cover", "titlepage"]


def test_cover_from_file_and_no_cover(tmp_path):
    picture = png(tmp_path / "front.png", size=(60, 90), color="blue")
    given = build(tmp_path, "Text.\n", "--cover", str(picture))
    assert given.read("EPUB/images/cover.png") == picture.read_bytes()
    bare = build(tmp_path, "Text.\n", "--no-cover")
    assert "cover-image" not in manifest(bare)
    assert not [n for n in bare.namelist() if "cover" in n]


def test_chapter_opens_with_a_small_caps_lead_in(tmp_path):
    archive = build(tmp_path, "# Book\n\n## One\n\nThe very first words here.\n\nSecond paragraph.\n")
    page = xml(archive, chapters(archive)[0])
    lead = page.findall('.//x:span[@class="lead-in"]', NS)
    assert [text(span) for span in lead] == ["The very first"]
    assert text(lead[0].getparent()) == "The very first words here."


def test_wrapped_code_lines_hang_under_their_own_indent(tmp_path):
    archive = build(tmp_path, "```python\n\ndef f():\n    return 1\n```\n\n    indented block\n")
    page = xml(archive, chapters(archive)[0])
    fenced, indented = page.findall(".//x:pre", NS)
    lines = fenced.findall(".//x:span[@class='line']", NS)
    assert [text(line) for line in lines] == ["", "def f():", "return 1"]
    assert lines[2].get("style") == "padding-left:6ch;text-indent:-6ch"
    assert lines[1].get("style") == "padding-left:2ch;text-indent:-2ch"
    assert [text(line) for line in indented.findall(".//x:span[@class='line']", NS)] == ["indented block"]


def test_github_style_fragments_and_local_links(tmp_path, capsys):
    markdown = "# Book\n\n## Ação e Reação\n\ntext\n\n## Two\n\nBack to [it](#ação-e-reação), see [doc](docs/a.md) and [b](b.md).\n"
    archive = build(tmp_path, markdown)
    two = xml(archive, chapters(archive)[1])
    assert two.xpath('//x:a[text()="it"]/@href', namespaces=NS) == ["ch001.xhtml#acao-e-reacao"]
    assert two.xpath('//x:a[text()="doc"]/@href', namespaces=NS) == []
    assert "2 link(s) to local files kept as plain text: docs/a.md, b.md" in capsys.readouterr().err
