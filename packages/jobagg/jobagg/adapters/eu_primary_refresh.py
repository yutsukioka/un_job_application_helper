"""Refresh reviewed EUIPO/EUDA PDFs only after observing identical current bytes."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import re
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

from jobagg.adapters.eu_primary_public import render_public_notice


class PrimaryPDFReviewRequired(ValueError):
    """Current primary bytes need a full page review before detail completion."""


def supports_reference(identity):
    return bool(re.fullmatch(r'ext-\d{2}-\d{2}-ad-\d+-(?:cpd|boa)|ca\d{6}', str(identity)))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a' and values.get('href'):
            self.links.append(values['href'])


def current_primary_url(html_text, wrapper_url, identity):
    """Select only an exact public vacancy-reference PDF anchor on its wrapper."""
    wrapper = urlsplit(wrapper_url)
    if wrapper.scheme != 'https' or wrapper.netloc != wrapper.hostname or wrapper.query or wrapper.fragment:
        raise ValueError('Reviewed primary wrapper URL has an unreviewed origin/suffix')
    if identity.startswith('ext-'):
        if wrapper.netloc != 'www.euipo.europa.eu' or wrapper.path.rstrip('/') != '/en/about-us/the-office/who-we-are/employer-of-choice/vacancies':
            raise ValueError('EUIPO primary requires the public vacancy directory')
        prefix = identity.rsplit('-', 1)[0].upper() + '-'
        host = 'euipo.europa.eu'
        path_prefix = '/tunnel-web/secure/webdav/guest/document_library/contentPdfs/about_euipo/vacancies/'
    elif re.fullmatch(r'ca\d{6}', identity):
        if wrapper.netloc != 'www.euda.europa.eu' or wrapper.path != f'/calls/{identity[2:6]}/{identity}_en':
            raise ValueError('EUDA wrapper changes the public vacancy reference')
        prefix = f'ca.{identity[2:6]}.{identity[6:8]}-call-for-applications-'
        host, path_prefix = 'www.euda.europa.eu', '/system/files/documents/'
    else:
        raise ValueError('Primary PDF reference layout is not reviewed')
    parser = _Links()
    parser.feed(html_text)
    candidates = set()
    for href in parser.links:
        absolute = urljoin(wrapper_url, href)
        parsed = urlsplit(absolute)
        if (parsed.scheme == 'https' and parsed.netloc == host and not parsed.query and not parsed.fragment
                and parsed.path.startswith(path_prefix) and parsed.path.rsplit('/', 1)[-1].startswith(prefix)
                and parsed.path.lower().endswith('.pdf')):
            candidates.add(absolute)
    if len(candidates) != 1:
        raise PrimaryPDFReviewRequired('Current wrapper lacks one exact vacancy-reference primary PDF anchor')
    return candidates.pop()


def reviewed_previous_item(db, job):
    """Pass an already bound DB observation as old evidence, never as fresh data."""
    if job.source_id != 'eu_careers_static' or not supports_reference(job.external_id):
        return job.raw
    previous = db.get_job(job.identity_key())
    if not previous:
        return job.raw
    from jobagg.db import JobDatabase
    raw = previous.get('raw', {})
    resolution = raw.get('_eu_official_field_resolution', {})
    if resolution.get('provider') not in {'euipo_reviewed_primary_pdf', 'euda_reviewed_primary_pdf'}:
        return job.raw
    if not JobDatabase._eu_bound_public_detail(raw, previous):
        raise PrimaryPDFReviewRequired('Stored primary PDF observation has an invalid source/body binding')
    return {**job.raw, '_reviewed_primary_previous_record': deepcopy(previous)}


def reviewed_extraction_admissible(proof, units):
    """Require the retained import's full-page review, not merely a body hash."""
    if not isinstance(proof, dict) or not isinstance(units, list) or not units:
        return False
    review, ref = proof.get('visual_review'), proof.get('reviewed_document')
    count = len(units)
    if (not isinstance(review, dict) or not isinstance(ref, dict)
            or proof.get('page_count') != count or review.get('all_pages_reviewed') is not True
            or review.get('content_sha256') != proof.get('content_sha256')
            or review.get('text_sha256') != proof.get('extracted_text_sha256')
            or review.get('pages_actually_viewed') != list(range(1, count + 1))):
        return False
    rendered = review.get('rendered_page_sha256')
    if (not isinstance(rendered, dict) or set(rendered) != {str(n) for n in range(1, count + 1)}
            or any(not re.fullmatch(r'[0-9a-f]{64}', str(v)) for v in rendered.values())):
        return False
    for path_key, hash_key in (('path', 'sha256'), ('retrieval_metadata', 'retrieval_metadata_sha256')):
        path = ref.get(path_key)
        if (not isinstance(path, str) or not PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
                or not re.fullmatch(r'[0-9a-f]{64}', str(ref.get(hash_key)))):
            return False
    try:
        start = datetime.fromisoformat(proof['original_retrieval_started_at'])
        end = datetime.fromisoformat(proof['original_retrieval_finished_at'])
        return bool(start.tzinfo and end.tzinfo and start <= end)
    except (KeyError, TypeError, ValueError):
        return False


def refresh_reviewed_notice(adapter, item, *, identity, summary_url, summary_html, wrapper_url, wrapper_html):
    primary_url = current_primary_url(wrapper_html, wrapper_url, identity)
    response = adapter._eu_get_official(primary_url)
    if str(response.url or primary_url) != primary_url or not response.content.startswith(b'%PDF-'):
        raise PrimaryPDFReviewRequired('Current primary response is not the exact official PDF')
    current_sha = hashlib.sha256(response.content).hexdigest()
    previous = item.get('_reviewed_primary_previous_record')
    if not isinstance(previous, dict):
        raise PrimaryPDFReviewRequired('Current primary PDF requires a full page review; no previously bound extraction is available')
    from jobagg.db import JobDatabase
    raw = previous.get('raw', {})
    if (previous.get('source_id') != adapter.source.id or str(previous.get('external_id')) != identity
            or previous.get('source_url') != summary_url or previous.get('apply_url') != primary_url
            or not JobDatabase._eu_bound_public_detail(raw, previous)):
        raise PrimaryPDFReviewRequired('Previous extraction does not bind this source/reference/current PDF URL')
    proof = raw['public_primary_document_proof']
    if not reviewed_extraction_admissible(proof, raw.get('public_primary_page_units')):
        raise PrimaryPDFReviewRequired('Stored primary extraction lacks a bound full-page visual review')
    if proof['content_sha256'] != current_sha:
        raise PrimaryPDFReviewRequired('Current primary PDF bytes changed; full page review required before detail completion')
    job = render_public_notice(adapter.source, external_id=identity, summary_url=summary_url,
        primary_url=primary_url, page_units=deepcopy(raw['public_primary_page_units']),
        document_proof=deepcopy(proof), required_attachment_urls=deepcopy(raw['required_attachment_urls']),
        source_conflicts=deepcopy(raw.get('reviewed_source_content_conflicts', [])))
    job.raw.update(summary_html=summary_html, official_wrapper_html=wrapper_html, official_wrapper_url=wrapper_url,
        _eu_primary_refresh_observation={
            'processed_at': datetime.now(timezone.utc).isoformat(),
            'current_response_url': primary_url, 'current_response_content_sha256': current_sha,
            'previous_document_proof_sha256': raw['_eu_official_field_resolution']['document_proof_sha256'],
            'extraction_reused_only_after_current_byte_equality': True,
            'prior_capture_and_visual_review_timestamps_unchanged': True,
            'wrapper_text_metadata_reconciled': False,
            'whole_public_page_scope_verified': False,
            'scope': 'Fresh primary PDF byte identity only. Current wrapper HTML is retained; amendments outside the identical PDF require separate reconciliation.',
            'whole_job_complete': False,
        })
    return job
