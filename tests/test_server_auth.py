import json

import pytest

from memodi.database.connection import ensure_schema
from memodi.server import _caller


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
