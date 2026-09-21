"""A transport failure before any HTML arrives cannot be a parser failure."""
import socket
from urllib.error import URLError

import pytest

from jobagg.pipelines.sync_source import _classify_run_failure, _is_transient_detail_error


@pytest.mark.parametrize('failure', [
    socket.gaierror(8, 'nodename nor servname provided, or not known'),
    URLError(socket.gaierror(-2, 'Name or service not known')),
    OSError('getaddrinfo failed'),
])
def test_dns_failures_follow_bounded_transient_recovery(failure):
    assert _classify_run_failure(failure) == 'transient_error'
    assert _is_transient_detail_error(failure)


def test_http_access_denial_and_malformed_content_keep_distinct_classifications():
    assert _classify_run_failure(RuntimeError('HTTP 403 Forbidden')) == 'blocked'
    assert not _is_transient_detail_error(RuntimeError('HTTP 403 Forbidden'))
    assert _classify_run_failure(ValueError('JSON expecting value')) == 'parser_error'
    assert not _is_transient_detail_error(ValueError('JSON expecting value'))
