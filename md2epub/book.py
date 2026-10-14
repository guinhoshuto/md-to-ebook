"""Build the book model: metadata, chapters, footnotes, images and table of contents."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import lxml.html
from lxml import etree
from PIL import Image as PILImage

from . import parse

HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
CONVERTIBLE = (".bmp", ".tif", ".tiff")
UNSAFE = (
    "script", "style", "iframe", "object", "embed", "link", "meta", "form", "input",
    "button", "select", "textarea", "video", "audio", "canvas", "svg", "math",
)
CALLOUT = re.compile(r"^\s*\[!([\w-]+)\]([+-]?)[ \t]*(.*)$")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
CONTENTS_LABEL = {"pt": "Sumário", "es": "Índice", "fr": "Table des matières", "de": "Inhalt"}


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


@dataclass
class Image:
    source: Path
    name: str
    media_type: str
    data: bytes
    width: int | None = None
    height: int | None = None


@dataclass
class TocEntry:
    title: str
    href: str
    children: list["TocEntry"] = field(default_factory=list)


@dataclass
class Chapter:
    filename: str
    title: str | None
    body: list  # top-level lxml elements, in order
    toc: TocEntry | None = None

    @property
    def id(self) -> str:
        return Path(self.filename).stem


@dataclass
class Metadata:
    title: str
    subtitle: str | None
    authors: list[str]
    lang: str
    identifier: str
    modified: dt.datetime
    date: str | None = None
    publisher: str | None = None
    description: str | None = None
    rights: str | None = None
    subjects: list[str] = field(default_factory=list)

    @property
    def contents_label(self) -> str:
        return CONTENTS_LABEL.get(self.lang.split("-")[0], "Contents")


@dataclass
class Book:
    meta: Metadata
    chapters: list[Chapter]
    images: list[Image]
    cover: Image | None
    toc: list[TocEntry]
    generate_cover: bool = False


# ---------------------------------------------------------------- helpers

def text_of(el) -> str:
    """Visible text of an element, without footnote markers, whitespace collapsed."""
    clone = copy.deepcopy(el)
    clone.tail = None
    for sup in clone.xpath('.//sup[contains(@class, "footnote-ref")]'):
        remove_keep_tail(sup)
    return " ".join(clone.text_content().split())


def remove_keep_tail(el) -> None:
    parent = el.getparent()
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def unwrap(el) -> None:
    """Replace an element by its children and text."""
    parent = el.getparent()
    index = parent.index(el)
    prev = el.getprevious()
    if el.text:
        if prev is not None:
            prev.tail = (prev.tail or "") + el.text
        else:
            parent.text = (parent.text or "") + el.text
    children = list(el)
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)
    last = children[-1] if children else el.getprevious()
    if el.tail:
        if last is not None and last is not el:
            last.tail = (last.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"s-{slug}" if slug else "section"
    return slug


def as_list(value) -> list[str]:
    """A front matter value as a list of strings: a YAML list, or a comma-separated string."""
    if value is None or value is False:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def find_vault_root(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if (parent / ".obsidian").is_dir():
            return parent
    return None


# ---------------------------------------------------------------- images

class ImageStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.by_path: dict[Path, Image] = {}
        self.vault = find_vault_root(base_dir)

    def resolve(self, src: str, wiki: bool) -> Path | None:
        candidate = (self.base_dir / unquote(src)).resolve()
        if candidate.is_file():
            return candidate
        if not wiki:
            return None
        name = Path(unquote(src)).name
        for root in filter(None, [self.base_dir, self.vault]):
            matches = sorted(p for p in root.rglob(name) if p.is_file() and ".trash" not in p.parts)
            if matches:
                return matches[0].resolve()
        return None

    def add(self, path: Path) -> Image | None:
        if path not in self.by_path:
            image = load_image(path, f"img-{len(self.by_path) + 1:03d}")
            if image is None:
                return None
            self.by_path[path] = image
        return self.by_path[path]


def load_image(path: Path, stem: str) -> Image | None:
    """Read an image for the book, converting BMP and TIFF to PNG; None (with a warning) when unusable."""
    suffix = path.suffix.lower()
    data = path.read_bytes()
    if suffix in CONVERTIBLE:
        buffer = io.BytesIO()
        PILImage.open(io.BytesIO(data)).save(buffer, "PNG")
        data, suffix = buffer.getvalue(), ".png"
    if suffix not in MEDIA_TYPES:
        warn(f"unsupported image format, skipped: {path}")
        return None
    width = height = None
    if suffix != ".svg":
        try:
            with PILImage.open(io.BytesIO(data)) as im:
                width, height = im.size
        except OSError:
            warn(f"unreadable image, skipped: {path}")
            return None
    return Image(path, f"{stem}{suffix}", MEDIA_TYPES[suffix], data, width, height)


def process_images(body, store: ImageStore) -> None:
    for img in list(body.iter("img")):
        src = (img.get("src") or "").strip()
        wiki = img.attrib.pop("data-wikiembed", None) is not None
        alt = img.get("alt", "")
        img.set("alt", alt)
        if SCHEME.match(src):
            warn(f"remote image kept as a link: {src}")
            link = lxml.html.Element("a", href=src)
            link.text = alt or src
            link.tail = img.tail
            img.getparent().replace(img, link)
            continue
        path = store.resolve(src, wiki)
        image = store.add(path) if path else None
        if image is None:
            if not path:
                warn(f"image not found: {src}")
            span = lxml.html.Element("span", {"class": "missing-image"})
            span.text = f"[{alt or Path(src).name}]"
            span.tail = img.tail
            img.getparent().replace(img, span)
            continue
        img.set("src", f"../images/{image.name}")

    for p in list(body.iter("p")):
        children = list(p)
        if len(children) != 1 or children[0].tag != "img" or (p.text or "").strip() or (children[0].tail or "").strip():
            continue
        img = children[0]
        figure = lxml.html.Element("figure")
        img.tail = None
        figure.append(img)
        caption = img.attrib.pop("title", None) or img.get("alt")
        if caption:
            figcaption = lxml.html.Element("figcaption")
            figcaption.text = caption
            figure.append(figcaption)
        figure.tail = p.tail
        p.getparent().replace(p, figure)


# ---------------------------------------------------------------- DOM passes

def strip_unsafe(body) -> None:
    for el in list(body.iter(*UNSAFE)):
        if el.tag == "input" and el.get("type") == "checkbox":
            box = lxml.html.Element("span", {"class": "task-box"})
            box.text = "☑" if el.get("checked") is not None else "☐"
            box.tail = el.tail
            el.getparent().replace(el, box)
            continue
        warn(f"removed unsupported <{el.tag}> element")
        remove_keep_tail(el)
    etree.strip_elements(body, etree.Comment, etree.ProcessingInstruction, with_tail=False)
    for el in body.iter():
        for attr in [a for a in el.attrib if a.lower().startswith("on")]:
            del el.attrib[attr]


def process_callouts(body) -> None:
    for quote in list(body.iter("blockquote")):
        first = quote[0] if len(quote) else None
        if first is None or first.tag != "p" or not first.text:
            continue
        line, _, rest = first.text.partition("\n")
        match = CALLOUT.match(line)
        if not match:
            continue
        kind = match.group(1).lower()
        title = lxml.html.Element("p", {"class": "callout-title"})
        title.text = match.group(3).strip() or kind.capitalize()
        first.text = rest.lstrip()
        if first.text == "" and len(first) and first[0].tag == "br":
            remove_keep_tail(first[0])
        if not first.text and not len(first):
            quote.remove(first)
        quote.insert(0, title)
        quote.tag = "div"
        quote.set("class", f"callout callout-{kind}")


def extract_footnotes(body) -> dict[str, object]:
    notes: dict[str, object] = {}
    for hr in body.xpath('./hr[contains(@class, "footnotes-sep")]'):
        body.remove(hr)
    for section in body.xpath('./section[contains(@class, "footnotes")]'):
        for li in section.xpath(".//li[@id]"):
            notes[li.get("id")] = li
        body.remove(section)
    return notes


def assign_heading_ids(body) -> None:
    used = {el.get("id") for el in body.iter() if el.get("id")}
    for heading in body.iter(*HEADINGS):
        if heading.get("id"):
            continue
        base = slugify(text_of(heading))
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        heading.set("id", slug)


def choose_structure(body, split_level: int | None) -> tuple[object | None, int | None]:
    """Return (title heading to drop, heading level that starts a chapter)."""
    top = [el for el in body if isinstance(el.tag, str)]
    levels = [int(el.tag[1]) for el in top if el.tag in HEADINGS]
    title_h1 = None
    if levels.count(1) == 1 and levels[0] == 1:
        title_h1 = next(el for el in top if el.tag == "h1")
    if split_level is not None:
        return title_h1 if split_level > 1 else None, split_level
    if title_h1 is not None:
        return title_h1, (2 if 2 in levels else None)
    if levels.count(1) >= 2:
        return None, 1
    if 2 in levels:
        return None, 2
    return None, None


def split_chapters(body, level: int | None) -> list[list]:
    tag = f"h{level}" if level else None
    groups: list[list] = [[]]
    for el in list(body):
        if not isinstance(el.tag, str):
            continue
        if tag and el.tag == tag and groups[-1]:
            groups.append([])
        groups[-1].append(el)
    return [g for g in groups if any(not is_blank(el) for el in g)]


def is_blank(el) -> bool:
    return el.tag in ("p", "div") and not len(el) and not (el.text or "").strip()


def add_lead_in(heading) -> None:
    """Set the first words of a chapter's opening paragraph in small capitals, as printed books do."""
    paragraph = heading.getnext()
    if paragraph is None or paragraph.tag != "p" or not paragraph.text:
        return
    match = re.match(r"(\s*)((?:\S+\s+){0,2}\S+)", paragraph.text)
    if not match:
        return
    span = lxml.html.Element("span", {"class": "lead-in"})
    span.text = match.group(2)
    span.tail = paragraph.text[match.end():]
    paragraph.text = match.group(1) or None
    paragraph.insert(0, span)


def shift_headings(elements: list, shift: int) -> None:
    if shift <= 0:
        return
    for el in elements:
        for heading in el.iter(*HEADINGS):
            heading.tag = f"h{max(1, int(heading.tag[1]) - shift)}"


def place_footnotes(chapters: list[Chapter], notes: dict[str, object]) -> None:
    placed: set[str] = set()
    for chapter in chapters:
        order: list[str] = []
        for el in chapter.body:
            for ref in el.xpath('.//sup[contains(@class, "footnote-ref")]/a[starts-with(@href, "#")]'):
                note_id = ref.get("href")[1:]
                ref.text = re.sub(r"^\[(.*)\]$", r"\1", ref.text or "").split(":")[0]
                ref.set("epub:type", "noteref")
                ref.set("role", "doc-noteref")
                if note_id in notes and note_id not in placed and note_id not in order:
                    order.append(note_id)
        if not order:
            continue
        section = lxml.html.Element("section", {"class": "footnotes", "epub:type": "footnotes"})
        for note_id in order:
            placed.add(note_id)
            section.append(build_note(notes[note_id], note_id))
        chapter.body.append(section)
    for note_id in notes.keys() - placed:
        warn(f"footnote defined but never referenced: {note_id}")


def build_note(li, note_id: str):
    aside = lxml.html.Element(
        "aside", {"id": note_id, "class": "footnote", "epub:type": "footnote", "role": "doc-footnote"}
    )
    backrefs = li.xpath('.//a[contains(@class, "footnote-backref")]')
    first_ref = backrefs[0].get("href") if backrefs else None
    for backref in backrefs:
        remove_keep_tail(backref)
    for child in list(li):
        aside.append(child)
    if li.text and li.text.strip():
        lead = lxml.html.Element("p")
        lead.text = li.text
        aside.insert(0, lead)
    number = re.sub(r"\D", "", note_id) or note_id
    target = aside[0] if len(aside) and aside[0].tag == "p" else aside
    if target is aside:
        target = lxml.html.Element("p")
        aside.insert(0, target)
    marker = lxml.html.Element("a", {"class": "footnote-number"})
    if first_ref:
        marker.set("href", first_ref)
    marker.text = f"{number}."
    marker.tail = " " + (target.text or "").lstrip()
    target.text = None
    target.insert(0, marker)
    for p in aside.iter("p"):
        if p.text is not None and not len(p):
            p.text = p.text.rstrip()
    last = aside.xpath(".//p")[-1]
    if len(last) and last[-1].tail:
        last[-1].tail = last[-1].tail.rstrip()
    elif not len(last) and last.text:
        last.text = last.text.rstrip()
    return aside


def fix_links(chapters: list[Chapter]) -> None:
    """Point #fragment links at the chapter file that holds the target; drop links that cannot work in a book.

    A fragment written the GitHub or Obsidian way (#ação-e-reação) is matched through the same slug as the
    heading ids, so it still lands on the heading.
    """
    owner: dict[str, str] = {}
    for chapter in chapters:
        for el in chapter.body:
            for node in el.iter():
                if node.get("id"):
                    owner.setdefault(node.get("id"), chapter.filename)
    local_files: list[str] = []
    for chapter in chapters:
        for el in chapter.body:
            for a in el.iter("a"):
                href = a.get("href")
                if href is None:
                    continue
                if href.startswith("#"):
                    target = unquote(href[1:])
                    if target not in owner:
                        target = slugify(target)
                    if target not in owner:
                        warn(f"broken internal link removed: {href}")
                        del a.attrib["href"]
                    elif owner[target] != chapter.filename:
                        a.set("href", f"{owner[target]}#{target}")
                    else:
                        a.set("href", f"#{target}")
                elif not SCHEME.match(href) and not href.startswith("//"):
                    local_files.append(href)
                    del a.attrib["href"]
    if local_files:
        shown = ", ".join(local_files[:3]) + (", ..." if len(local_files) > 3 else "")
        warn(f"{len(local_files)} link(s) to local files kept as plain text: {shown}")


def build_toc(chapters: list[Chapter], depth: int) -> list[TocEntry]:
    toc: list[TocEntry] = []
    for chapter in chapters:
        headings = [el for el in chapter.body if el.tag in HEADINGS]
        if chapter.title is None:
            continue
        entry = TocEntry(chapter.title, chapter.filename + "#" + headings[0].get("id"))
        if depth > 1:
            base = int(headings[0].tag[1])
            for heading in headings[1:]:
                if int(heading.tag[1]) == base + 1:
                    entry.children.append(TocEntry(text_of(heading), f"{chapter.filename}#{heading.get('id')}"))
        chapter.toc = entry
        toc.append(entry)
    return toc


# ---------------------------------------------------------------- metadata

def build_metadata(meta: dict, overrides: dict, body_text: str, title_h1, source: Path) -> Metadata:
    merged = {**meta, **{k: v for k, v in overrides.items() if v not in (None, [], "")}}
    title = merged.get("title") or (text_of(title_h1) if title_h1 is not None else None)
    title = str(title or source.stem.replace("-", " ").replace("_", " ").strip().title())
    authors = as_list(merged.get("author") or merged.get("authors") or merged.get("creator"))
    lang = str(merged.get("lang") or merged.get("language") or parse.detect_language(body_text))
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    timestamp = int(epoch) if epoch else int(source.stat().st_mtime)
    modified = dt.datetime.fromtimestamp(max(timestamp, 315532800), dt.timezone.utc).replace(microsecond=0)
    identifier = merged.get("identifier") or merged.get("isbn")
    if identifier:
        identifier = str(identifier)
        if merged.get("isbn") and not identifier.startswith("urn:"):
            identifier = f"urn:isbn:{identifier}"
    else:
        seed = hashlib.sha256(f"{title}\0{'|'.join(authors)}".encode()).hexdigest()
        identifier = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'md2epub:' + seed)}"
    date = merged.get("date")
    subjects = [s.lstrip("#") for s in as_list(merged.get("subjects") or merged.get("tags") or merged.get("keywords"))]
    return Metadata(
        title=title,
        subtitle=str(merged["subtitle"]) if merged.get("subtitle") else None,
        authors=authors,
        lang=lang,
        identifier=identifier,
        modified=modified,
        date=date.isoformat() if isinstance(date, (dt.date, dt.datetime)) else (str(date) if date else None),
        publisher=str(merged["publisher"]) if merged.get("publisher") else None,
        description=str(merged["description"]) if merged.get("description") else None,
        rights=str(merged["rights"]) if merged.get("rights") else None,
        subjects=subjects,
    )


# ---------------------------------------------------------------- entry point

def build_book(
    source: Path,
    overrides: dict | None = None,
    split_level: int | None = None,
    toc_depth: int = 2,
    cover: Path | None | bool = None,
) -> Book:
    """Read a Markdown file and return the book model.

    cover: a path to use as cover, False for no cover, None to take it from the front matter or generate one.
    """
    overrides = overrides or {}
    raw = source.read_text(encoding="utf-8-sig")
    front, markdown = parse.split_front_matter(raw)
    lang_hint = overrides.get("lang") or front.get("lang") or front.get("language")
    lang = str(lang_hint or parse.detect_language(markdown))

    html_text = parse.render(markdown, lang)
    document = lxml.html.document_fromstring(f"<html><body>{html_text}</body></html>")
    body = document.body

    notes = extract_footnotes(body)
    strip_unsafe(body)
    process_callouts(body)
    store = ImageStore(source.parent.resolve())
    process_images(body, store)
    for note in notes.values():
        process_images(note, store)
    assign_heading_ids(body)

    title_h1, level = choose_structure(body, split_level)
    meta = build_metadata(front, {**overrides, "lang": lang}, markdown, title_h1, source)
    if title_h1 is not None:
        body.remove(title_h1)

    chapters: list[Chapter] = []
    for index, group in enumerate(split_chapters(body, level), start=1):
        first = group[0]
        starts_with_heading = level is not None and first.tag == f"h{level}"
        title = text_of(first) if starts_with_heading else None
        if starts_with_heading:
            first.set("class", (first.get("class", "") + " chapter-title").strip())
            add_lead_in(first)
        shift_headings(group, (level or 1) - 1)
        chapters.append(Chapter(f"ch{index:03d}.xhtml", title, group))

    if not chapters:
        empty = lxml.html.Element("p")
        empty.text = ""
        chapters.append(Chapter("ch001.xhtml", None, [empty]))

    place_footnotes(chapters, notes)
    fix_links(chapters)
    toc = build_toc(chapters, toc_depth) or [TocEntry(meta.title, chapters[0].filename)]

    wants_cover = cover is not False and front.get("cover") is not False
    cover_image = resolve_cover(cover, front, source) if wants_cover else None
    return Book(meta, chapters, list(store.by_path.values()), cover_image, toc, wants_cover and cover_image is None)


def resolve_cover(cover, front: dict, source: Path) -> Image | None:
    """The cover image given on the command line or in the front matter; None when absent or unusable."""
    path = cover if isinstance(cover, Path) else None
    if path is None and front.get("cover"):
        path = source.parent / str(front["cover"])
    if path is None:
        return None
    if not path.is_file():
        warn(f"cover image not found, generating one: {path}")
        return None
    return load_image(path.resolve(), "cover")
