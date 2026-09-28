"""IPU public bilingual vacancy fields and explicit translation identity."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo
import hashlib
import re

from jobagg.normalize import build_job, clean_text

_VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
_BLOCKS = {'p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'table', 'tr'}


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

    def text(self):
        if self.tag in {'script', 'style', 'noscript'}:
            return ''
        encoded = self.attrs.get('data-cfemail')
        if encoded:
            value = bytes.fromhex(encoded)
            email = bytes(b ^ value[0] for b in value[1:]).decode('utf-8')
            if '@' not in email:
                raise ValueError('IPU public contact email could not be decoded')
            return email
        text = ' '.join(c.text() if isinstance(c, _Node) else c for c in self.children)
        return '\n' + text + '\n' if self.tag in _BLOCKS else text


class _Page(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root = _Node('document')
        self.stack = [self.root]
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_ipu_public_page(body: str, requested_url: str, locale: str) -> dict[str, Any]:
    nodes = list(_Page(body).root.walk())
    canonical = {urljoin(requested_url, n.attrs['href']) for n in nodes
                 if n.tag == 'link' and n.attrs.get('rel') == 'canonical' and n.attrs.get('href')}
    if canonical != {requested_url}:
        raise ValueError('IPU returned canonical vacancy differs from request')
    alternates = {n.attrs['hreflang']: urljoin(requested_url, n.attrs['href']) for n in nodes
                  if n.tag == 'link' and n.attrs.get('rel') == 'alternate'
                  and n.attrs.get('hreflang') in {'en', 'fr'} and n.attrs.get('href')}
    if set(alternates) != {'en', 'fr'} or alternates.get(locale) != requested_url:
        raise ValueError('IPU explicit English/French language alternatives missing')
    if any(urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'www.ipu.org' for url in alternates.values()):
        raise ValueError('IPU alternate leaves official public host')
    node_ids = {n.attrs['hreflang']: n.attrs['data-drupal-link-system-path'] for n in nodes
                if n.tag == 'a' and n.attrs.get('hreflang') in alternates
                and urljoin(requested_url, n.attrs.get('href', '')) == alternates[n.attrs['hreflang']]
                and n.attrs.get('data-drupal-link-system-path')}
    if set(node_ids) != {'en', 'fr'} or len(set(node_ids.values())) != 1:
        raise ValueError('IPU public language links do not bind the same Drupal node')
    fields = []
    for node in nodes:
        labels = [c for c in node.attrs.get('class', '').split() if c.startswith('vacancy__')]
        if labels:
            fields.append({'field': labels[0], 'text': clean_text(node.text()) or ''})
    by_field = {f['field']: f['text'] for f in fields}
    required = {'vacancy__node-title', 'vacancy__body', 'vacancy__field-how-to-apply', 'vacancy__field-deadline'}
    if not required <= by_field.keys() or any(not by_field[key] for key in required):
        raise ValueError('IPU public vacancy body/application/deadline fields missing')
    if '[email protected]' in ' '.join(f['text'] for f in fields):
        raise ValueError('IPU public application contact remains unresolved')
    return {'locale': locale, 'public_url': requested_url, 'public_title': by_field['vacancy__node-title'],
            'full_text': '\n\n'.join(f['text'] for f in fields if f['text']), 'fields': fields,
            'raw_response_text': body, 'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
            'translation_mapping': {'alternates': alternates, 'drupal_node': node_ids['en']},
            'public_email_method': 'standard public data-cfemail XOR decoding where present'}


def fetch_ipu_bilingual_detail(adapter, item: dict[str, Any], public_url: str):
    english = parse_ipu_public_page(adapter.fetch_text(public_url), public_url, 'en')
    french_url = english['translation_mapping']['alternates']['fr']
    french = parse_ipu_public_page(adapter.fetch_text(french_url), french_url, 'fr')
    if french['translation_mapping'] != english['translation_mapping']:
        raise ValueError('IPU French page does not reciprocally identify the same English vacancy')
    variants = [english, french]
    description = '\n\n'.join(f"{'English' if v['locale'] == 'en' else 'Français'}\n\n{v['full_text']}" for v in variants)
    # IPU's date-only <time datetime> uses 12Z even when application prose
    # explicitly says 12.00 CEST. Prefer the visible application deadline.
    application = next(f['text'] for f in english['fields'] if f['field'] == 'vacancy__field-how-to-apply')
    match = re.search(r'(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+at\s+(\d{1,2})[.:](\d{2})\s+(CEST|CET)\b', application)
    deadline = item.get('closes_at')
    if match:
        local = datetime.strptime(f'{match[1]} {match[2]}:{match[3]}', '%d %B %Y %H:%M').replace(tzinfo=ZoneInfo('Europe/Zurich'))
        if local.tzname() != match[4]:
            raise ValueError('IPU deadline timezone abbreviation conflicts with public date')
        deadline = local
    job = build_job(adapter.source, title=english['public_title'],
                     external_id=item.get('external_id') or urlsplit(public_url).path.rstrip('/').rsplit('/', 1)[-1],
                     apply_url=public_url, source_url=public_url, description=description,
                     closes_at=deadline, posted_at=item.get('posted_at'),
                     employment_type=next((f['text'] for f in english['fields'] if f['field'] == 'vacancy__type'), None),
                     raw={**item, 'parser': 'ipu_public_bilingual_detail', 'detail_url': public_url,
                          'detail_fetch_url': public_url,
                          'detail_html': english['raw_response_text'], '_jobagg_public_language_variants': variants})
    if match:
        job.closes_at_local = local.replace(tzinfo=None).isoformat()
        job.closes_tz = 'Europe/Zurich'
    return job
