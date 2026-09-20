"""Observed public ICC/AfDB requisition headers and date-renderer provenance."""
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import re
import unicodedata


class _Tags(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.parts = [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, value):
        self.parts.append(value)


def _plain(value):
    return unicodedata.normalize('NFC', ' '.join(''.join(_Tags(value).parts).split()))


def public_header(html_text, source_id, external_id):
    if source_id not in {'icc_successfactors_legacy', 'afdb_successfactors_legacy'}:
        raise ValueError('Unsupported public legacy header source')
    matches = list(re.finditer(r'<div\b[^>]*>\s*Requisition ID(?:&nbsp;|\s).*?</div>', html_text, re.I | re.S))
    if len(matches) != 1:
        raise ValueError('Public legacy requisition header is missing or duplicated')
    header = matches[0][0]
    tags = _Tags(header).tags
    if any(tag not in {'div', 'b'} for tag, _ in tags):
        raise ValueError('Unrecognized public header markup')
    bold = list(re.finditer(r'<b\b[^>]*>(.*?)</b>', header, re.I | re.S))
    width = 5 if source_id == 'icc_successfactors_legacy' else 3
    if len(bold) != width:
        raise ValueError('Public requisition header field count differs')
    values = [_plain(match[1]) for match in bold]
    attrs = [attrs for tag, attrs in tags if tag == 'b']
    if values[0] != str(external_id) or attrs[1].get('id') != 'postedOnDate':
        raise ValueError('Public requisition header identity/date binding differs')
    template, offset = [], 0
    for index, match in enumerate(bold):
        template.extend((header[offset:match.start()], '{' + str(index) + '}'))
        offset = match.end()
    template.append(header[offset:])
    expected = 'Requisition ID {0} - Posted {1}' + ''.join(' - {' + str(i) + '}' for i in range(2, width))
    if _plain(''.join(template)) != expected:
        raise ValueError('Public requisition header labels/separators differ')

    inputs, input_html = {}, []
    for match in re.finditer(r'<input\b[^>]*>', html_text, re.I):
        tag = _Tags(match[0]).tags[0][1]
        key = tag.get('id')
        if key not in {'postedOnFastDate', 'pattern', 'locale'}:
            continue
        if key in inputs or tag.get('type', '').lower() != 'hidden' or 'value' not in tag:
            raise ValueError('Public posting-date inputs are ambiguous')
        inputs[key] = tag['value']
        input_html.append(match[0])
    expected_date_shape = ('dd/MM/yyyy', 'en_GB') if width == 5 else ('MM/dd/yyyy', 'en_US')
    if (set(inputs) != {'postedOnFastDate', 'pattern', 'locale'}
            or (inputs['pattern'], inputs['locale']) != expected_date_shape):
        raise ValueError('Public posting-date formatter inputs require review')
    scripts = [match[0] for match in re.finditer(r'<script\b[^>]*>(.*?)</script>', html_text, re.I | re.S)
               if 'var postedOnFastDate=document.getElementById("postedOnFastDate").value;' in match[1]]
    if len(scripts) != 1:
        raise ValueError('Public posting-date formatter is missing or ambiguous')
    script = scripts[0]
    if not all(value in script for value in (
        'var postedOnDate = new Date(postedOnFastDateValue);',
        'new DateFormat(document.getElementById("pattern").value, document.getElementById("locale").value)',
        'postedOnDateField.innerHTML = dateFormatter.format(postedOnDate)',
    )):
        raise ValueError('Public posting-date renderer changed')
    epoch = inputs['postedOnFastDate']
    source_epoch_utc = None
    if re.fullmatch(r'\d{1,16}', epoch):
        try:
            source_epoch_utc = datetime.fromtimestamp(int(epoch) / 1000, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    category = values[2] if width == 5 else None
    return {
        'record_kind': 'detail', 'source_id': source_id, 'external_id': str(external_id),
        'header_html': header, 'header_html_sha256': hashlib.sha256(header.encode()).hexdigest(),
        'source_visible_text_without_client_date': _plain(header),
        'rendered_labels': ['Requisition ID', 'Posted'],
        'ordered_bold_values_before_client_rendering': values,
        'unlabelled_field_interpretations': (['category', 'job_field', 'location'] if width == 5 else ['job_field']),
        'category': category, 'job_field': values[3] if width == 5 else values[2],
        'location': values[4] if width == 5 else None,
        'unambiguous_program_type': category if category in {'Internship', 'Visiting Professional'} else None,
        'posting_date': {
            'rendered_server_value': values[1] or None,
            'rendered_calendar_date': None,
            'unknown_reason': 'Source renders a calendar date using browser-local DateFormat; browser timezone is not provided by this capture.',
            'source_epoch_milliseconds': epoch,
            'source_epoch_utc_claim': source_epoch_utc,
            'pattern': inputs['pattern'], 'locale': inputs['locale'],
            'renderer_uses_browser_local_time': True,
            'normalized_posting_instant_supported': False,
        },
        'date_renderer_inputs': inputs, 'date_renderer_inputs_html': input_html,
        'date_renderer_script': script, 'date_renderer_script_sha256': hashlib.sha256(script.encode()).hexdigest(),
    }
