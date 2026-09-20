"""A truncated transport body must not quarantine a healthy vacancy forever."""
from http.client import IncompleteRead

import pytest

from jobagg.pipelines.sync_source import _is_transient_detail_error


@pytest.mark.parametrize('error',[
    IncompleteRead(b'partial body',100),
    RuntimeError('Connection broken: IncompleteRead(10248 bytes read)'),
])
def test_incomplete_http_body_is_a_transient_detail_failure(error):
    assert _is_transient_detail_error(error)


def test_access_denial_does_not_become_retryable_because_of_nested_transport_text():
    assert not _is_transient_detail_error(RuntimeError('HTTP 403 forbidden; IncompleteRead(10 bytes read)'))


def test_parser_error_still_requires_diagnosis():
    assert not _is_transient_detail_error(ValueError('Expected vacancy detail object'))
