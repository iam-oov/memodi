import socket

from memodi.config import settings
from memodi.tools.context import client_context


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


class FakeRequestContext:
    def __init__(self, request):
        self.request = request


class FakeCtx:
    def __init__(
        self,
        request_context=None,
        raise_lookup_error=False,
        raise_value_error=False,
    ):
        self._request_context = request_context
        self._raise_lookup_error = raise_lookup_error
        self._raise_value_error = raise_value_error

    @property
    def request_context(self):
        if self._raise_lookup_error:
            raise LookupError("no request context available")
        if self._raise_value_error:
            raise ValueError("Context is not available outside of a request")
        return self._request_context


def test_headers_take_precedence_over_env(monkeypatch):
    monkeypatch.setattr(settings, "user_api_key", "env-key")
    monkeypatch.setattr(settings, "machine", "env-machine")

    headers = {
        "X-Memodi-Api-Key": "header-key",
        "X-Memodi-Machine": "header-machine",
    }
    ctx = FakeCtx(FakeRequestContext(FakeRequest(headers)))

    result = client_context(ctx)

    assert result == {"api_key": "header-key", "machine": "header-machine"}


def test_missing_header_with_request_present_stays_none(monkeypatch):
    monkeypatch.setattr(settings, "user_api_key", "env-key")
    monkeypatch.setattr(settings, "machine", "env-machine")
    monkeypatch.setattr(socket, "gethostname", lambda: "server-hostname")

    headers = {"X-Memodi-Machine": "header-machine"}
    ctx = FakeCtx(FakeRequestContext(FakeRequest(headers)))

    result = client_context(ctx)

    assert result == {"api_key": None, "machine": "header-machine"}


def test_env_fallback_when_ctx_is_none(monkeypatch):
    monkeypatch.setattr(settings, "user_api_key", "env-key")
    monkeypatch.setattr(settings, "machine", "env-machine")

    result = client_context(None)

    assert result == {"api_key": "env-key", "machine": "env-machine"}


def test_hostname_fallback_when_no_machine_anywhere(monkeypatch):
    monkeypatch.setattr(settings, "user_api_key", None)
    monkeypatch.setattr(settings, "machine", None)
    monkeypatch.setattr(socket, "gethostname", lambda: "fake-hostname")

    result = client_context(None)

    assert result == {"api_key": None, "machine": "fake-hostname"}


def test_safe_when_request_context_raises_lookup_error(monkeypatch):
    monkeypatch.setattr(settings, "user_api_key", "env-key")
    monkeypatch.setattr(settings, "machine", "env-machine")

    ctx = FakeCtx(raise_lookup_error=True)

    result = client_context(ctx)

    assert result == {"api_key": "env-key", "machine": "env-machine"}


def test_safe_when_request_context_raises_value_error(monkeypatch):
    monkeypatch.setattr(settings, "user_api_key", "env-key")
    monkeypatch.setattr(settings, "machine", "env-machine")

    ctx = FakeCtx(raise_value_error=True)

    result = client_context(ctx)

    assert result == {"api_key": "env-key", "machine": "env-machine"}


def test_safe_when_request_is_none(monkeypatch):
    monkeypatch.setattr(settings, "user_api_key", None)
    monkeypatch.setattr(settings, "machine", None)
    monkeypatch.setattr(socket, "gethostname", lambda: "fake-hostname")

    ctx = FakeCtx(FakeRequestContext(None))

    result = client_context(ctx)

    assert result == {"api_key": None, "machine": "fake-hostname"}
