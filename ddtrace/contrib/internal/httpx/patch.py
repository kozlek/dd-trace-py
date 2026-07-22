from __future__ import annotations

from collections.abc import Iterator
import sys
from types import ModuleType
from typing import TYPE_CHECKING
from typing import Any
from typing import Awaitable
from typing import Optional

from wrapt import BoundFunctionWrapper
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_binary
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u

from .utils import httpx_url_to_str


if TYPE_CHECKING:
    import httpx


# ``httpx2`` (https://github.com/pydantic/httpx2) is an API-compatible continuation of
# ``httpx``. Both packages expose the same ``Client``/``AsyncClient`` classes and ``URL``
# interface, so a single set of wrappers instruments either module. Both are patched under
# the shared ``httpx`` integration whenever they are imported.
_HTTPX_MODULE_NAMES = ("httpx", "httpx2")


config._add(
    "httpx",
    {
        "distributed_tracing": asbool(env.get("DD_HTTPX_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(env.get("DD_HTTPX_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
    },
)


def _httpx_modules() -> Iterator[ModuleType]:
    # Only yield modules that are already imported. ``patch()`` is invoked from an import hook
    # right after one of these modules is imported, so the relevant module is always present in
    # ``sys.modules``. This intentionally avoids importing the sibling module (e.g. importing
    # ``httpx`` just because ``httpx2`` was imported), which would both add unnecessary import
    # overhead and re-trigger the import hooks.
    for module_name in _HTTPX_MODULE_NAMES:
        module = sys.modules.get(module_name)
        if module is not None:
            yield module


def get_version() -> str:
    # This integration patches more than one module, so versions are reported via get_versions().
    return ""


def get_versions() -> dict[str, str]:
    return {module.__name__: getattr(module, "__version__", "") for module in _httpx_modules()}


def _supported_versions() -> dict[str, str]:
    return {"httpx": ">=0.25", "httpx2": ">=2"}


def _get_service_name(request: httpx.Request) -> Optional[str]:
    if config.httpx.split_by_domain:
        if hasattr(request.url, "netloc"):
            return ensure_text(request.url.netloc, errors="backslashreplace")

        service = ensure_binary(request.url.host)
        if request.url.port:
            service += b":" + ensure_binary(str(request.url.port))
        return ensure_text(service, errors="backslashreplace")
    return ext_service(None, config.httpx)


def _wrapped_sync_send_single_request(
    wrapped: "BoundFunctionWrapper[..., httpx.Response]",
    instance: httpx.Client,
    args: tuple[httpx.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx.Response]:
    req: httpx.Request = get_argument_value(args, kwargs, 0, "request")
    with core.context_with_event(
        event=HttpClientSendEvent(
            request_url=httpx_url_to_str(req.url),
            request_method=req.method,
            request_headers=req.headers,
            request_body=lambda: req.content,
        ),
        context_name_override=HttpClientEvents.HTTPX_SEND_REQUEST.value,
    ) as ctx:
        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.set_response(resp)


async def _wrapped_async_send_single_request(
    wrapped: "BoundFunctionWrapper[..., Awaitable[httpx.Response]]",
    instance: httpx.AsyncClient,
    args: tuple[httpx.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx.Response]:
    req: httpx.Request = get_argument_value(args, kwargs, 0, "request")
    with core.context_with_event(
        event=HttpClientSendEvent(
            request_url=httpx_url_to_str(req.url),
            request_method=req.method,
            request_headers=req.headers,
            request_body=lambda: req.content,
        ),
        context_name_override=HttpClientEvents.HTTPX_SEND_REQUEST.value,
    ) as ctx:
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.set_response(resp)


async def _wrapped_async_send(
    wrapped: "BoundFunctionWrapper[..., Awaitable[httpx.Response]]",
    instance: httpx.AsyncClient,
    args: tuple[httpx.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx.Response]:
    req: httpx.Request = get_argument_value(args, kwargs, 0, "request")  # type: ignore

    with core.context_with_event(
        HttpClientRequestEvent(
            http_operation="http.request",
            service=_get_service_name(req),
            component=config.httpx.integration_name,
            request_method=req.method,
            request_headers=req.headers,
            integration_config=config.httpx,
            request_url=httpx_url_to_str(req.url),
            query=ensure_text(req.url.query),
            target_host=req.url.host,
        ),
        context_name_override=HttpClientEvents.HTTPX_REQUEST.value,
    ) as ctx:
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.set_response(resp)


def _wrapped_sync_send(
    wrapped: "BoundFunctionWrapper[..., httpx.Response]",
    instance: httpx.AsyncClient,
    args: tuple[httpx.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx.Response]:
    req: httpx.Request = get_argument_value(args, kwargs, 0, "request")  # type: ignore

    with core.context_with_event(
        HttpClientRequestEvent(
            component=config.httpx.integration_name,
            http_operation="http.request",
            service=_get_service_name(req),
            request_method=req.method,
            request_headers=req.headers,
            integration_config=config.httpx,
            request_url=httpx_url_to_str(req.url),
            query=ensure_text(req.url.query),
            target_host=req.url.host,
        ),
        context_name_override=HttpClientEvents.HTTPX_REQUEST.value,
    ) as ctx:
        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.set_response(resp)


def _patch(httpx_module: ModuleType) -> None:
    if getattr(httpx_module, "_datadog_patch", False):
        return

    httpx_module._datadog_patch = True

    _w(httpx_module.Client, "send", _wrapped_sync_send)
    _w(httpx_module.AsyncClient, "send", _wrapped_async_send)
    _w(httpx_module.Client, "_send_single_request", _wrapped_sync_send_single_request)
    _w(httpx_module.AsyncClient, "_send_single_request", _wrapped_async_send_single_request)


def patch() -> None:
    for httpx_module in _httpx_modules():
        _patch(httpx_module)


def _unpatch(httpx_module: ModuleType) -> None:
    if not getattr(httpx_module, "_datadog_patch", False):
        return

    httpx_module._datadog_patch = False

    _u(httpx_module.AsyncClient, "send")
    _u(httpx_module.Client, "send")
    _u(httpx_module.Client, "_send_single_request")
    _u(httpx_module.AsyncClient, "_send_single_request")


def unpatch() -> None:
    for httpx_module in _httpx_modules():
        _unpatch(httpx_module)
