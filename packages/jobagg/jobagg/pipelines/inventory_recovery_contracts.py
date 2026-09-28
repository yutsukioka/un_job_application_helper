"""Independent captured-page counts for UNV and UNOPS public inventories."""
from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit, urljoin
from jobagg.pipelines.inventory_api_contracts import require, integer, identifier, jobs_by_id, raw_contains, url_signature
from jobagg.pipelines.inventory_vacancy_contracts import _page


class UNOPSPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.totals = set()
        self.article = False
        self.links = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'list-controls__text__legend' in a.get('class', '').split():
            match = re.fullmatch(r'(\d+) results?', a.get('aria-label', ''))
            require(match is not None, 'UNOPS visible result count missing')
            self.totals.add(int(match[1]))
        if tag == 'article':
            self.article = 'article--result' in a.get('class', '').split()
        if self.article and tag == 'a' and '/JobDetail/' in a.get('href', ''):
            self.links.append(a['href'])
    def handle_endtag(self, tag):
        if tag == 'article':
            self.article = False


def verify_recovery_listing(source, jobs, capture_paths):
    unv = source.id == 'unv_uvp'
    result = {'complete': False, 'method': 'unv_search_pages_v1' if unv else 'unops_public_pages_v1',
              'observed_count': len(jobs), 'scope': 'configured public endpoint and filters',
              'reasons': [], 'capture_paths': []}
    try:
        endpoint = source.extra['api_url' if unv else 'listing_url']
        parsed = jobs_by_id(source, jobs)
        limit = integer(source.extra['page_size'], 'page size')
        require(limit > 0, 'Page size must be positive')
        ids, totals, pages = [], set(), 0
        terminal = False
        for path in capture_paths:
            meta = json.loads(Path(path).read_text())
            if meta.get('phase', {}).get('kind') != 'listing' or urlsplit(meta.get('url','')).path.rstrip('/') != urlsplit(endpoint).path.rstrip('/'):
                continue
            require(not terminal and pages < source.extra['max_pages'], 'Extra page after terminal/cap')
            if unv:
                request = deepcopy(source.extra.get('search_payload') or {})
                request.setdefault('take', limit)
                request['take'], request['skip'] = limit, pages * limit
                payload = _page(path, meta, endpoint, result, request_body=request)
                value = payload.get('value')
                require(isinstance(value, dict), 'UNV search envelope missing')
                total = integer(value.get('total'), 'UNV total')
                rows = value.get('result')
                require(isinstance(rows, list) and len(rows) <= limit, 'Invalid UNV page')
                page_ids = []
                for row in rows:
                    key = identifier(row.get('id') or row.get('doaRequestNo'))
                    require(key in parsed and raw_contains(parsed[key].raw, row), 'UNV listing differs from captured assignment')
                    page_ids.append(key)
            else:
                expected = endpoint + '?' + urlencode({'jobRecordsPerPage': limit, 'jobOffset': pages * limit})
                require(meta.get('method') == 'GET' and meta.get('status_code') == 200
                        and meta.get('body_captured') is True, 'UNOPS requires successful captured GET')
                require(url_signature(meta['url']) == url_signature(expected) == url_signature(meta['response_url']), 'UNOPS page URL differs')
                body = gzip.decompress(Path(meta['artifact']).read_bytes())
                require(hashlib.sha256(body).hexdigest() == meta['body_sha256'], 'UNOPS body hash differs')
                html = UNOPSPage();html.feed(body.decode('utf-8'))
                require(len(html.totals) == 1, 'UNOPS visible total missing or inconsistent')
                total = next(iter(html.totals))
                page_ids = []
                # Cards can repeat the same detail link for title and CTA.
                for link in dict.fromkeys(html.links):
                    url = urljoin(endpoint, link);parts = urlsplit(url)
                    require(parts.hostname == urlsplit(endpoint).hostname, 'UNOPS vacancy host differs')
                    key = parts.path.rstrip('/').rsplit('/',1)[-1]
                    require(key.isdigit() and key in parsed and parsed[key].raw.get('_detail_url') == url, 'UNOPS captured/parsed identity differs')
                    page_ids.append(key)
                require(len(page_ids) <= limit, 'UNOPS page exceeds configured size')
                result['capture_paths'].append({'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()})
            require(len(set(page_ids)) == len(page_ids) and not set(ids).intersection(page_ids), 'Duplicate listing identity across pages')
            stamps = [datetime.fromisoformat(str(meta.get(key, '')).replace('Z', '+00:00'))
                      for key in ('started_at', 'finished_at')]
            require(all(stamp.tzinfo is not None for stamp in stamps) and stamps[0] <= stamps[1],
                    'Invalid listing capture interval')
            result['started_at'] = min(result.get('started_at', stamps[0].isoformat()), stamps[0].isoformat())
            result['finished_at'] = max(result.get('finished_at', stamps[1].isoformat()), stamps[1].isoformat())
            ids.extend(page_ids);totals.add(total);pages += 1
            require(len(totals) == 1 and len(ids) <= total, 'Advertised total changed during pagination')
            terminal = len(ids) == total
            require(terminal or len(page_ids) == limit, 'Partial page before advertised total')
        require(pages > 0 and terminal and set(ids) == set(parsed), 'Truncated or mismatched inventory')
        result.update(complete=True, reported_total=next(iter(totals)), pages=pages)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result['reasons'] = [str(exc)]
    return result
