"""Functional tests for the ``httpx2`` package.

httpx2 (https://github.com/pydantic/httpx2) is an API-compatible continuation of httpx and is
instrumented by the same ``httpx`` integration. These tests exercise the integration against real
httpx2 objects using ``httpx2.MockTransport`` so no external HTTP server is required.
"""

import httpx2
import pytest

from ddtrace.contrib.internal.httpx.patch import patch
from ddtrace.contrib.internal.httpx.patch import unpatch
from ddtrace.internal.compat import is_wrapted
from tests.utils import override_config


URL = "http://testserver/status/200"


@pytest.fixture(autouse=True)
def patch_httpx2():
    patch()
    try:
        yield
    finally:
        unpatch()


def _mock_transport(status_code=200, captured_requests=None):
    def handler(request):
        if captured_requests is not None:
            captured_requests.append(request)
        return httpx2.Response(status_code, text="")

    return httpx2.MockTransport(handler)


def _get_http_span(test_spans):
    spans = test_spans.pop()
    http_spans = [s for s in spans if s.name == "http.request"]
    assert len(http_spans) == 1
    return http_spans[0]


def test_patching():
    """When patching, the httpx2 client methods are wrapped, and unwrapped on unpatch."""
    assert is_wrapted(httpx2.Client.send)
    assert is_wrapted(httpx2.AsyncClient.send)

    unpatch()
    assert not is_wrapted(httpx2.Client.send)
    assert not is_wrapted(httpx2.AsyncClient.send)


def test_get_200_sync(test_spans):
    with httpx2.Client(transport=_mock_transport()) as client:
        resp = client.get(URL)
    assert resp.status_code == 200

    span = _get_http_span(test_spans)
    assert span.name == "http.request"
    assert span.get_tag("component") == "httpx"
    assert span.get_tag("span.kind") == "client"
    assert span.get_tag("http.method") == "GET"
    assert span.get_tag("http.status_code") == "200"
    assert span.get_tag("http.url") == URL
    assert span.error == 0


@pytest.mark.asyncio
async def test_get_200_async(test_spans):
    async with httpx2.AsyncClient(transport=_mock_transport()) as client:
        resp = await client.get(URL)
    assert resp.status_code == 200

    span = _get_http_span(test_spans)
    assert span.get_tag("component") == "httpx"
    assert span.get_tag("http.method") == "GET"
    assert span.get_tag("http.status_code") == "200"
    assert span.get_tag("http.url") == URL


def test_get_500_error(test_spans):
    """A 5xx response marks the span as an error."""
    with httpx2.Client(transport=_mock_transport(status_code=500)) as client:
        resp = client.get(URL)
    assert resp.status_code == 500

    span = _get_http_span(test_spans)
    assert span.get_tag("http.status_code") == "500"
    assert span.error == 1


def test_split_by_domain(test_spans):
    """When split_by_domain is enabled, the service name is set to <host>:<port>."""
    with override_config("httpx", {"split_by_domain": True}):
        with httpx2.Client(transport=_mock_transport()) as client:
            resp = client.get(URL)
    assert resp.status_code == 200

    span = _get_http_span(test_spans)
    assert span.service == "testserver"


def test_distributed_tracing_headers():
    """By default, distributed tracing headers are injected into outbound requests."""
    captured = []
    with httpx2.Client(transport=_mock_transport(captured_requests=captured)) as client:
        client.get(URL)

    assert len(captured) == 1
    headers = captured[0].headers
    assert "x-datadog-trace-id" in headers
    assert "x-datadog-parent-id" in headers
    assert "x-datadog-sampling-priority" in headers


def test_distributed_tracing_disabled():
    """When distributed_tracing is disabled, no distributed tracing headers are injected."""
    captured = []
    with override_config("httpx", {"distributed_tracing": False}):
        with httpx2.Client(transport=_mock_transport(captured_requests=captured)) as client:
            client.get(URL)

    assert len(captured) == 1
    headers = captured[0].headers
    assert "x-datadog-trace-id" not in headers
    assert "x-datadog-parent-id" not in headers
    assert "x-datadog-sampling-priority" not in headers
