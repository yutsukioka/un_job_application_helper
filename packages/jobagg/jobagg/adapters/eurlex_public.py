"""Complete, identity-bound English EUR-Lex public vacancy notices."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

_VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
_BLOCK = {'br', 'div', 'p', 'tr', 'td', 'h1', 'h2', 'h3', 'li', 'section'}
_HEADINGS = ('We are', 'We propose', 'We look for (selection criteria)', 'Management skills',
             'Specialist skills and experience', 'Personal qualities',
             'Applicants must (eligibility requirements)', 'Selection and appointment',
             'Equal opportunities', 'Conditions of employment', 'Important information for candidates',
             'Protection of personal data', 'Independence and declaration of interests',
             'Application procedure', 'Closing date')


def _text(value):
    return ' '.join(unicodedata.normalize('NFC', value).split())


class _Document(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.value = value
        self.lines = [0]
        for match in re.finditer('\n', value):
            self.lines.append(match.end())
        self.depth = self.found = 0
        self.start = self.end = None
        self.parts, self.paragraphs, self.open_paragraphs = [], [], []
        self.anchors, self.footnotes, self.citations = [], set(), set()
        self.anchor_spans, self.open_anchors = [], []
        self.feed(value)
        self.close()
        if self.found != 1 or self.depth or self.start is None or self.end is None:
            raise ValueError('EUR-Lex requires one closed public document1 container')

    def source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'):
            self.anchors.append(attrs['href'])
            self.open_anchors.append((self.source_offset(), attrs['href']))
        if attrs.get('id') == 'document1':
            if self.depth:
                raise ValueError('Nested EUR-Lex public document containers')
            self.depth, self.start = 1, self.source_offset()
            self.found += 1
            return
        if not self.depth:
            return
        if tag in {'script', 'style', 'noscript'}:
            raise ValueError('Executable or hidden content inside public notice needs review')
        if tag not in _VOID:
            self.depth += 1
        if tag in _BLOCK:
            self.parts.append('\n')
        if tag == 'p':
            self.open_paragraphs.append((self.depth, attrs.get('class', '').split(), []))
        ident = attrs.get('id', '')
        match = re.fullmatch(r'(ntr|ntc)(\d+)-.+', ident)
        if match:
            (self.footnotes if match[1] == 'ntr' else self.citations).add(int(match[2]))

    def handle_endtag(self, tag):
        if tag == 'a' and self.open_anchors:
            start, href = self.open_anchors.pop()
            self.anchor_spans.append((href, start, self.value.index('>', self.source_offset()) + 1))
        if not self.depth:
            return
        if tag == 'p' and self.open_paragraphs and self.open_paragraphs[-1][0] == self.depth:
            _, classes, parts = self.open_paragraphs.pop()
            self.paragraphs.append((classes, _text(''.join(parts))))
        if tag not in _VOID:
            self.depth -= 1
        if tag in _BLOCK:
            self.parts.append('\n')
        if self.depth == 0:
            self.end = self.value.index('>', self.source_offset()) + 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)
            for _, _, parts in self.open_paragraphs:
                parts.append(data)


def render_public_notice(source: OrganizationSource, html_text: str, *, page_url: str,
                         external_id: str, summary_url: str, expected_title: str,
                         summary_metadata: dict | None = None):
    url = urlsplit(page_url)
    notice = re.fullmatch(r'/eli/C/(\d{4})/(\d+)/oj', url.path)
    summary = urlsplit(summary_url)
    if (source.id != 'eu_careers_static' or url.scheme != 'https'
            or url.netloc != 'eur-lex.europa.eu' or url.query or url.fragment or not notice
            or not re.fullmatch(r'com-\d{4}-\d+', external_id)
            or summary.scheme != 'https' or summary.netloc != 'eu-careers.europa.eu'
            or summary.query or summary.fragment
            or not re.fullmatch(r'/en/job-opportunities/[a-z0-9-]+/' + re.escape(external_id), summary.path)):
        raise ValueError('EUR-Lex vacancy source and observed URL are not bound')
    parsed = _Document(html_text)
    body = _text(''.join(parsed.parts))
    paragraphs = parsed.paragraphs
    titles = [text for classes, text in paragraphs if 'oj-doc-ti' in classes]
    expected_reference = external_id.replace('-', '/').upper()
    expected_notice = 'C/' + notice[1] + '/' + notice[2]
    notices = [text for classes, text in paragraphs if 'oj-hd-uniq' in classes]
    dates = [text for classes, text in paragraphs if 'oj-hd-date' in classes]
    headings = [text for classes, text in paragraphs if 'oj-ti-grseq-1' in classes]
    if (len(titles) != 4 or titles[3] != expected_reference or notices != [expected_notice]
            or headings != list(_HEADINGS) or parsed.footnotes != parsed.citations or not parsed.footnotes):
        raise ValueError('EUR-Lex identity, complete section structure, or footnotes differ')
    title = re.fullmatch(r'Publication of a vacancy for the function of (.+)', titles[1])
    contract = re.fullmatch(r'\((Temporary Agent) [–-] Grade (AD \d{1,2})\)', titles[2])
    if not title or _text(expected_title).casefold() != title[1].casefold() or not contract:
        raise ValueError('EUR-Lex public role/contract heading differs from the listing')
    if len(dates) != 1:
        raise ValueError('EUR-Lex public publication calendar is not unique')
    publication_date = datetime.strptime(dates[0], '%d.%m.%Y').date().isoformat()
    locations = [m[1] for _, text in paragraphs if (m := re.fullmatch(r'The place of employment is (.+)\.', text))]
    deadlines = [text for _, text in paragraphs if text.startswith('The closing date for registration is ')]
    if len(locations) != 1 or len(deadlines) != 1:
        raise ValueError('EUR-Lex public location or deadline paragraph is not unique')
    cutoff = re.fullmatch(r'The closing date for registration is (\d{1,2} [A-Za-z]+ \d{4}), '
                          r'12\.00 noon Brussels time, following which registration is no longer possible\.', deadlines[0])
    local = (datetime.strptime(cutoff[1], '%d %B %Y').replace(hour=12, tzinfo=ZoneInfo('Europe/Brussels'))
             if cutoff else None)
    document_html = html_text[parsed.start:parsed.end]
    anchors = list(dict.fromkeys(urljoin(page_url, href) for href in parsed.anchors))
    required = []
    for link in anchors:
        parts = urlsplit(link)
        uri = parse_qs(parts.query).get('uri', [])
        match = re.fullmatch(r'OJ:C_(\d{4})(\d+)', uri[0]) if len(uri) == 1 else None
        if (parts.scheme == 'https' and parts.hostname == 'eur-lex.europa.eu'
                and re.fullmatch(r'/legal-content/[A-Z]{2}/TXT/PDF/', parts.path) and match
                and match[1] == notice[1] and int(match[2]) == int(notice[2])):
            required.append(link)
    required.extend(link for link in anchors if urlsplit(link).hostname == 'commission.europa.eu'
                    and '/document/download/' in urlsplit(link).path and 'filename=' in urlsplit(link).query
                    and link.casefold().endswith('.pdf'))
    # Preserve exact served primary anchors outside document1 for later link
    # discovery/reparsing, without adding them to the public body text.
    extra_anchors = list(dict.fromkeys(html_text[start:end] for href, start, end in parsed.anchor_spans
                                      if urljoin(page_url, href) in required
                                      and not (parsed.start <= start < parsed.end)))
    detail_html = document_html + ''.join('\n' + value for value in extra_anchors)
    resolution = {
        'record_kind': 'detail', 'provider': 'eurlex_official_public_notice',
        'external_id': external_id, 'public_reference': expected_reference, 'public_notice': expected_notice,
        'public_title': title[1], 'public_organization': titles[0], 'public_contract_type': contract[1],
        'official_grade': contract[2], 'public_location': locations[0],
        'public_publication_date': dates[0], 'publication_calendar_date': publication_date,
        'posting_time_resolved': False, 'posting_time_unknown_reason': 'Public journal supplies a calendar date only.',
        'public_deadline': deadlines[0], 'utc_resolved': local is not None,
        'deadline_timezone_basis': 'Explicit noon Brussels time; Europe/Brussels civil timezone.' if local else None,
        'official_vacancy_url': page_url, 'summary_url': summary_url,
        'document_html_sha256': hashlib.sha256(document_html.encode()).hexdigest(),
        'description_sha256': hashlib.sha256(body.encode()).hexdigest(),
        'public_headings': headings, 'footnote_numbers': sorted(parsed.footnotes),
    }
    job = build_job(source, title=title[1], external_id=external_id, apply_url=page_url,
                    source_url=summary_url, location=locations[0], department=None,
                    employment_type=contract[1], posted_at=None,
                    closes_at=local.astimezone(timezone.utc) if local else None, description=body,
                    raw={'parser': 'eu_official_detail', 'external_id': external_id,
                         'detail_url': summary_url, 'detail_fetch_url': page_url,
                         'official_vacancy_url': page_url, 'official_notice_text': body,
                         'detail_html': detail_html, 'summary_metadata': summary_metadata or {},
                         'identity_verification': 'official_link_and_title_or_reference',
                         'grade': contract[2], 'institution': titles[0],
                         'required_attachment_urls': list(dict.fromkeys(required)),
                         '_eu_official_field_resolution': resolution})
    # Preserve natural inline joins and literal prose; do not parse text as HTML.
    job.description = body
    job.closes_at_local = local.isoformat() if local else None
    job.closes_tz = 'Europe/Brussels' if local else None
    return job
