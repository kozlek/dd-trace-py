# httpx2 (https://github.com/pydantic/httpx2) is an API-compatible continuation of httpx and is
# instrumented by the same "httpx" integration. This mirrors test_httpx_patch.py but targets the
# httpx2 module.

from ddtrace.contrib.internal.httpx.patch import get_version
from ddtrace.contrib.internal.httpx.patch import get_versions
from ddtrace.contrib.internal.httpx.patch import patch


try:
    from ddtrace.contrib.internal.httpx.patch import unpatch
except ImportError:
    unpatch = None
from tests.contrib.patch import PatchTestCase
from tests.contrib.patch import emit_integration_and_version_to_test_agent


class TestHttpx2Patch(PatchTestCase.Base):
    __integration_name__ = "httpx"
    __module_name__ = "httpx2"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, httpx2):
        pass

    def assert_not_module_patched(self, httpx2):
        pass

    def assert_not_module_double_patched(self, httpx2):
        pass

    def test_and_emit_get_version(self):
        # The httpx integration instruments more than one module (httpx and httpx2), so the
        # per-module versions are reported through get_versions() and get_version() returns "".
        import httpx2  # noqa: F401  ensure the module is imported so get_versions() reports it

        version = get_version()
        assert isinstance(version, str)
        assert version == ""

        versions = get_versions()
        assert self.__module_name__ in versions
        assert versions[self.__module_name__] != ""
        for module_name, v in versions.items():
            emit_integration_and_version_to_test_agent(self.__integration_name__, v, module_name=module_name)
