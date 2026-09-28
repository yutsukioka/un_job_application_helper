"""Observed All-category radio and vacancy-table contract for icddr,b."""

from html.parser import HTMLParser
import re

from jobagg.utils import clean_html


class AllBoard(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.radios = []
        self.rows = []
        self.row = None
        self.cell = None
        self.table = 0
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "employee_type_id":
            self.radios.append(attrs)
        if tag == "table":
            self.table += 1
        if tag == "tr" and self.table:
            self.row = []
        if tag == "td" and self.row is not None:
            self.cell = []
        if self.cell is not None:
            self.cell.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.cell is not None:
            self.cell.append(f"</{tag}>")
        if tag == "td" and self.cell is not None:
            self.row.append("".join(self.cell))
            self.cell = None
        if tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None
        if tag == "table":
            self.table = max(0, self.table - 1)

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)


def no_continuation(text):
    for control in re.findall(r"<(?:a|button)\b[^>]*>.*?</(?:a|button)>", text, re.I | re.S):
        label = (clean_html(control) or "").strip().casefold()
        if re.search(
            r"(?:[?&](?:page|offset|start)=|rel=[\"\']next[\"\']|class=[\"\'][^\"\']*\b(?:pagination|paginate_button)\b)",
            control,
            re.I,
        ) or re.fullmatch(r"(?:next|load more|show more|older|next page|[›»])(?:\s*[›»])?", label):
            raise ValueError("Public table exposes unhandled pagination/continuation")


def all_board_links(text, base_url):
    from jobagg.adapters.icddrb import _vacancy_links

    board = AllBoard(text)
    if not board.radios:
        return None
    if {r.get("value") for r in board.radios} != {"1", "2", "3"} or len(board.radios) != 3:
        raise ValueError("icddr,b category controls changed")
    if [r.get("value") for r in board.radios if "checked" in r] != ["1"]:
        return None
    no_continuation(text)
    if board.table or board.row is not None or board.cell is not None:
        raise ValueError("icddr,b table unclosed")
    result = []
    for row in board.rows:
        links = _vacancy_links("".join(row), base_url)
        if len(links) != 1 or len(row) < 3:
            raise ValueError("icddr,b All table has an unidentified row")
        result.extend(links)
    if not result:
        raise ValueError("icddr,b empty All table lacks a verified empty-state contract")
    if len({x["href"] for x in result}) != len(result) or len(
        _vacancy_links(text, base_url)
    ) != len(result):
        raise ValueError("icddr,b duplicate or unaccounted vacancy links")
    return result
