from memodi.config import Settings


def test_host_defaults_to_all_interfaces():
    assert Settings().host == "0.0.0.0"


def test_host_reads_env_override(monkeypatch):
    monkeypatch.setenv("MEMODI_HOST", "127.0.0.1")
    assert Settings().host == "127.0.0.1"
