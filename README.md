# md2epub

Turn a Markdown file into a well-typeset EPUB 3 book.

```sh
uv run md2epub notes.md            # writes notes.epub next to the input
uv run md2epub notes.md -o ~/Books/notes.epub --author "Ada Example"
```

To have the `md2epub` command everywhere: `uv tool install --editable .`

## What you get

- **Book typography**: justified, hyphenated text in the book's language; indented paragraphs (only after another
  paragraph); chapter openings with a centred title, a short rule and the first words in small capitals; booktabs
  tables; footnotes at the end of each chapter, which Apple Books and Kobo show as pop-ups.
- **Chapters**: one file per chapter, with a table of contents (EPUB 3 nav plus an NCX for older readers).
- **A cover**: your image, or a generated typographic one (title, subtitle and author on a cloth-coloured
  background picked from the title).
- **Code**: syntax highlighting with Pygments, a night palette for Apple Books' night and gray themes, and long
  lines that wrap under their own indentation (reading apps cannot scroll sideways).
- **Typography fixes**: curly quotes per language (`“”` in Portuguese and English, `«»` in Spanish and French,
  `„“` in German), en and em dashes from `--` and `---`, and `...` as an ellipsis.
- **Obsidian syntax**: `[[Note|alias]]` becomes its label, `![[image.png|300]]` embeds the image (searched in
  the note's folder, then in the whole vault), `> [!tip] Title` callouts become coloured boxes, `==text==` becomes
  a highlight.
- **Everything else from Markdown**: tables, task lists, definition lists, strikethrough, images (as figures with
  captions), raw HTML (scripts, forms and media are removed).
- **Reproducible output**: with `SOURCE_DATE_EPOCH` set, the same input gives the same bytes. Without it, the
  modification date in the metadata is the Markdown file's.

## Metadata

YAML front matter fills the book's metadata; command-line flags override it.

```yaml
---
title: The Quiet Page
subtitle: Notes on making text pleasant to read
author: Ada Example          # or a list: [Ada, Bia]
lang: en                     # default: detected from the text (pt-BR, en, es, fr)
date: 2026-10-13
publisher: Example Press
description: A short book about typography.
rights: CC BY 4.0
tags: [typography, reading]  # becomes dc:subject
isbn: 9780000000000          # or identifier: ...; default: a stable UUID from title and author
cover: images/cover.jpg      # relative to the Markdown file; false for no cover
---
```

Without a `title`, the book takes the single top-level `#` heading, then the file name.

## How chapters are chosen

- One `#` heading at the top and `##` sections below it: the `#` is the book title and each `##` is a chapter.
- Several `#` headings: each `#` is a chapter.
- Only `##` headings: each `##` is a chapter.
- `--split-level N` forces the heading level that starts a chapter.

Text before the first chapter heading becomes an untitled opening section. Inside each chapter file, headings are
renumbered so the chapter title is an `h1`. A contents page appears in the reading order when there are three or
more chapters.

## Options

| Flag | What it does |
|---|---|
| `-o, --output` | Where to write the EPUB (default: next to the input). |
| `--title`, `--subtitle`, `--author` (repeatable), `--lang` | Override the front matter. |
| `--cover IMAGE` / `--no-cover` | Use an image as the cover, or leave the book without one. |
| `--css FILE` | Append your own stylesheet after the built-in one. |
| `--split-level N` | Heading level that starts a chapter. |
| `--toc-depth 1\|2` | Levels in the table of contents (default 2). |

The built-in stylesheet is `md2epub/book.css`.

## Development

```sh
uv sync
uv run pytest
```

The tests build EPUBs and check the archive: the mimetype entry, manifest against files, well-formed XML, every
link and fragment, unique XML-name ids, and reproducible bytes. `tests/test_md2epub.mutations.json` is a mutation
plan (one `de` → `para` swap per entry, each of which must make a test fail) for the `mutar_copia.py` runner:

```sh
python3 ~/obsidian/AI/scripts/mutar_copia.py tests/test_md2epub.mutations.json --raiz "$PWD" --jobs 4
```

## Known pitfalls

- Inline `<svg>` and `<math>` in the Markdown are dropped: the HTML parser cannot keep their XML intact. Put the
  drawing in an `.svg` file and reference it as an image.
- Remote images (`http…`) are not downloaded; they become links. Reading apps do not fetch them.
- Links to other local files (`other.md`) cannot work inside a book; they are kept as plain text, with one warning
  that lists them.
- Kindle ignores the book's fonts unless the reader picks "Publisher font"; that is expected, and the layout still
  holds.
- The tests check structure but do not run EPUBCheck, which needs Java.
