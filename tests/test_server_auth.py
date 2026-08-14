import json

import pytest

from memodi.database import auth_repository
from memodi.database.connection import ensure_schema
from memodi.server import _caller, memodi_logout


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


class FakeRequestContext:
    def __init__(self, request):
        self.request = request


class FakeCtx:
    def __init__(self, headers):
        self.request_context = FakeRequestContext(FakeRequest(headers))


def test_caller_unknown_api_key_returns_not_authenticated():
    ctx = FakeCtx(
        {
            "X-Memodi-Api-Key": "mmd_unknown-but-valid-format-key",
            "X-Memodi-Machine": "some-machine",
        }
    )

    result = _caller(ctx)

    assert isinstance(result, str)
    payload = json.loads(result)
    assert payload["type"] == "not_authenticated"


def test_memodi_logout_unauthenticated_returns_not_authenticated_envelope():
    ctx = FakeCtx(
        {
            "X-Memodi-Api-Key": "mmd_unknown-but-valid-format-key",
            "X-Memodi-Machine": "some-machine",
        }
    )

    result = json.loads(memodi_logout(ctx))

    assert result["type"] == "not_authenticated"


def test_memodi_logout_authenticated_revokes_the_calling_key(registered_workspace):
    ctx = FakeCtx(
        {
            "X-Memodi-Api-Key": registered_workspace["api_key"],
            "X-Memodi-Machine": registered_workspace["machine"],
        }
    )

    result = json.loads(memodi_logout(ctx))

    assert result["revoked"] is True
    assert "email" in result
    assert (
        auth_repository.get_user_by_api_key(registered_workspace["api_key"]) is None
    )
