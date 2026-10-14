"""Markdown to HTML: front matter, language, typography, highlighting and Obsidian syntax."""

from __future__ import annotations

import html
import re
from collections import Counter
from itertools import zip_longest

import yaml
from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.token import Token
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

CODE_FORMATTER = HtmlFormatter(nowrap=True, classprefix="tok-")

FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)

# Words that are frequent in one language and rare in the others.
STOPWORDS = {
    "pt-BR": "não você uma com isso também são mais do da os ao é está então muito aqui pelo pela seu sua".split(),
    "en": "the and of is that it with for this you are was not have but from they which".split(),
    "es": "el los las y una con del por es pero muy también está esto como más hay".split(),
    "fr": "le les et est des une pas dans avec qui pour sur ce je vous nous mais".split(),
}

QUOTES = {
    "pt": "“”‘’",
    "en": "“”‘’",
    "es": "«»“”",
    "fr": ["« ", " »", "‹ ", " ›"],
    "de": "„“‚‘",
}

WIKILINK = re.compile(r"(!?)\[\[([^\[\]|]+?)(?:\|([^\[\]]*?))?\]\]")
HIGHLIGHT = re.compile(r"==(?=\S)(.+?)(?<=\S)==")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".tif", ".tiff")


def split_front_matter(text: str) -> tuple[dict, str]:
    """Return the YAML front matter (empty when absent or not a mapping) and the remaining Markdown."""
    match = FRONT_MATTER.match(text)
    if not match:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        return {}, text
    return data, text[match.end():]


def detect_language(text: str) -> str:
    """Guess the book language from stopword counts; English when nothing matches."""
    words = Counter(re.findall(r"[^\W\d_]+", text.lower()))
    scores = {lang: sum(words[w] for w in stop) for lang, stop in STOPWORDS.items()}
    best = max(scores, key=lambda lang: scores[lang])
    return best if scores[best] > 0 else "en"


def highlight_code(code: str, lang: str, _attrs: str = "") -> str:
    """Highlight code, one block-level span per line.

    Each line carries a hanging indent equal to its own indentation plus two characters, so a long line that
    the reading app wraps continues under its own code instead of at the left edge.
    """
    options = {"stripnl": False, "tabsize": 4}
    try:
        lexer = get_lexer_by_name(lang.split()[0], **options) if lang else TextLexer(**options)
    except ClassNotFound:
        lexer = TextLexer(**options)
    source_lines = code.expandtabs(4).split("\n")
    html_lines = highlight(code, lexer, CODE_FORMATTER).split("\n")
    for lines in (source_lines, html_lines):
        if lines and lines[-1] == "":
            lines.pop()
    spans = []
    for source, line in zip_longest(source_lines, html_lines, fillvalue=""):
        hang = len(source) - len(source.lstrip(" ")) + 2
        spans.append(f'<span class="line" style="padding-left:{hang}ch;text-indent:-{hang}ch">{line}\n</span>')
    return "".join(spans)


def render_code_block(self, tokens, idx, options, env) -> str:
    return f"<pre><code>{highlight_code(tokens[idx].content, '')}</code></pre>\n"


def _joined_text(children: list[Token]) -> list[Token]:
    """Merge runs of text and text_special tokens, as the later text_join rule would."""
    merged: list[Token] = []
    for token in children:
        if token.type in ("text", "text_special") and merged and merged[-1].type == "text":
            merged[-1].content += token.content
            continue
        if token.type == "text_special":
            token.type = "text"
        merged.append(token)
    return merged


def _html_token(content: str) -> Token:
    token = Token("html_inline", "", 0)
    token.content = content
    return token


def _text_token(content: str) -> Token:
    token = Token("text", "", 0)
    token.content = content
    return token


def _wikilink_tokens(match: re.Match) -> list[Token]:
    bang, target, alias = match.group(1), match.group(2).strip(), (match.group(3) or "").strip()
    page = re.split(r"[#^]", target)[0].strip()
    if bang and page.lower().endswith(IMAGE_SUFFIXES):
        attrs = f'src="{html.escape(page)}" alt="" data-wikiembed="1"'
        if re.fullmatch(r"\d+(x\d+)?", alias):
            attrs += f' width="{alias.split("x")[0]}"'
        elif alias:
            attrs = attrs.replace('alt=""', f'alt="{html.escape(alias)}"')
        return [_html_token(f"<img {attrs} />")]
    label = alias or page or target.lstrip("#^").strip()
    return [_text_token(label)]


def _split_text(token: Token) -> list[Token]:
    out: list[Token] = []
    pos = 0
    text = token.content
    for match in WIKILINK.finditer(text):
        if match.start() > pos:
            out.append(_text_token(text[pos:match.start()]))
        out.extend(_wikilink_tokens(match))
        pos = match.end()
    if pos < len(text):
        out.append(_text_token(text[pos:]))
    result: list[Token] = []
    for part in out:
        if part.type != "text" or "==" not in part.content:
            result.append(part)
            continue
        pos = 0
        for match in HIGHLIGHT.finditer(part.content):
            if match.start() > pos:
                result.append(_text_token(part.content[pos:match.start()]))
            result += [_html_token("<mark>"), _text_token(match.group(1)), _html_token("</mark>")]
            pos = match.end()
        if pos < len(part.content):
            result.append(_text_token(part.content[pos:]))
    return result


def obsidian_rule(state: StateCore) -> None:
    """Turn [[links]] into their label, ![[image]] into an image and ==text== into <mark>."""
    for block in state.tokens:
        if block.type != "inline" or not block.children:
            continue
        children: list[Token] = []
        for token in _joined_text(block.children):
            if token.type == "text" and ("[[" in token.content or "==" in token.content):
                children.extend(_split_text(token))
            else:
                children.append(token)
        block.children = children


def build_parser(lang: str) -> MarkdownIt:
    quotes = QUOTES.get(lang.split("-")[0].lower(), QUOTES["en"])
    md = MarkdownIt(
        "commonmark",
        {"typographer": True, "quotes": quotes, "html": True, "highlight": highlight_code},
    )
    md.enable(["table", "strikethrough", "replacements", "smartquotes"])
    md.use(footnote_plugin).use(deflist_plugin).use(tasklists_plugin)
    md.core.ruler.after("inline", "obsidian", obsidian_rule)
    md.add_render_rule("code_block", render_code_block)
    return md


def render(markdown: str, lang: str) -> str:
    return build_parser(lang).render(markdown)


def token_css(selector: str | list[str], style: str) -> str:
    """Pygments token colours under selector, without the red box some styles draw around lexer errors."""
    lines = HtmlFormatter(style=style, classprefix="tok-").get_token_style_defs(selector)
    return "\n".join(line for line in lines if ".tok-err " not in line)
