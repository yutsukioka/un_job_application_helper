"""Text from explicit HTML structure, preserving adjacency inside inline tags.

This is a fragment renderer, not a CSS layout engine. Callers must select the
public description first; this helper does not establish whole-page coverage.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_SPACE = re.compile(r"\s+")
_BLOCKS = frozenset(
    "address article aside blockquote caption center dd details dialog div dl dt "
    "fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr "
    "legend li main menu nav ol p pre section summary table tbody td tfoot th "
    "thead tr ul".split()
)
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_NON_TEXT = frozenset({"script", "style", "template", "head"})
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))"
    r"\s*(?:!important\s*)?(?:;|$)", re.I,
)


class _FragmentText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []

    @property
    def hidden(self) -> bool:
        return any(hidden for _, hidden in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        hidden = self.hidden or tag in _NON_TEXT or "hidden" in attributes or bool(
            _HIDDEN_STYLE.search(attributes.get("style") or "")
        )
        if not hidden and (tag in _BLOCKS or tag == "br"):
            self.parts.append(" ")
        if tag not in _VOID:
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag: str) -> None:
        hidden = self.hidden
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if not hidden and tag in _BLOCKS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def render_html_text(value: object | None) -> str | None:
    """Collapse whitespace, without inserting spaces at inline boundaries.

    Entity decoding happens once in HTMLParser. Literal escaped angle-bracket
    text and ampersands must not be interpreted again by a downstream cleaner.
    Existing spaces, spelling and punctuation are otherwise retained.
    """
    if value is None:
        return None
    parser = _FragmentText()
    parser.feed(str(value))
    parser.close()
    return _SPACE.sub(" ", "".join(parser.parts)).strip() or None
