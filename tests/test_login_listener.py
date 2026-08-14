import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

LISTENER = (
    Path(__file__).resolve().parent.parent
    / "plugin"
    / "claude-code"
    / "scripts"
    / "login_listener.py"
)

LOGIN_URL = "https://memodi.example/login"
KEY = "mmd_" + "a" * 32
EMAIL = "user@example.com"


def _spawn(timeout: str = "5") -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(LISTENER), LOGIN_URL],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "MEMODI_NO_BROWSER": "1",
            "MEMODI_LOGIN_TIMEOUT": timeout,
        },
    )


def _read_url_line(proc: subprocess.Popen) -> str:
    line = proc.stderr.readline()
    while line and "Open this URL" not in line:
        line = proc.stderr.readline()
    return proc.stderr.readline().strip()


def _port_and_nonce(url: str) -> tuple[str, str]:
    port = re.search(r"[?&]port=(\d+)", url).group(1)
    nonce = re.search(r"[?&]nonce=([^&\s]+)", url).group(1)
    return port, nonce


def _get(port: str, path: str) -> int:
    try:
        response = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
        return response.status
    except urllib.error.HTTPError as e:
        return e.code


def _callback(port: str, **params: str) -> int:
    return _get(port, "/?" + urllib.parse.urlencode(params))


def _finish(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=10)


def test_stderr_url_line_has_port_and_nonce():
    proc = _spawn()
    try:
        url = _read_url_line(proc)
        assert "port=" in url
        assert "nonce=" in url
    finally:
        _finish(proc)


def test_valid_request_prints_key_and_email_and_exits_zero():
    proc = _spawn()
    try:
        url = _read_url_line(proc)
        port, nonce = _port_and_nonce(url)
        status = _callback(port, key=KEY, nonce=nonce, email=EMAIL)
        assert status == 200
        stdout, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0
        assert stdout == f"{KEY} {EMAIL}\n"
    finally:
        _finish(proc)


def test_wrong_nonce_returns_403_and_process_stays_alive():
    proc = _spawn()
    try:
        url = _read_url_line(proc)
        port, nonce = _port_and_nonce(url)
        status = _callback(port, key=KEY, nonce="totally-wrong-nonce-value")
        assert status == 403
        assert proc.poll() is None

        status = _callback(port, key=KEY, nonce=nonce, email="a@b.com")
        assert status == 200
        stdout, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0
        assert stdout == f"{KEY} a@b.com\n"
    finally:
        _finish(proc)


def test_missing_key_returns_400_and_process_stays_alive():
    proc = _spawn()
    try:
        url = _read_url_line(proc)
        port, nonce = _port_and_nonce(url)
        status = _callback(port, nonce=nonce)
        assert status == 400
        assert proc.poll() is None

        status = _callback(port, key=KEY, nonce=nonce, email="c@d.com")
        assert status == 200
        stdout, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0
        assert stdout == f"{KEY} c@d.com\n"
    finally:
        _finish(proc)


@pytest.mark.parametrize(
    "bad_key",
    [
        KEY + "\n",
        KEY + "\nexport MEMODI_API_KEY=attacker",
        KEY + "\t",
        "\t" + KEY,
        "sk_" + "a" * 32,
        "mmd_short",
        "mmd_" + "a" * 129,
        "mmd_" + "a" * 31 + "!",
    ],
)
def test_malformed_key_returns_400_and_process_stays_alive(bad_key):
    proc = _spawn()
    try:
        url = _read_url_line(proc)
        port, nonce = _port_and_nonce(url)
        status = _callback(port, key=bad_key, nonce=nonce, email=EMAIL)
        assert status == 400
        assert proc.poll() is None
    finally:
        _finish(proc)


@pytest.mark.parametrize(
    "bad_email",
    [
        "user@example.com\n",
        "user@example.com\nexport MEMODI_API_KEY=attacker",
        "user name@example.com",
        "user@example.com\t",
        "no-at-sign",
        "",
    ],
)
def test_malformed_email_returns_400_and_process_stays_alive(bad_email):
    proc = _spawn()
    try:
        url = _read_url_line(proc)
        port, nonce = _port_and_nonce(url)
        status = _callback(port, key=KEY, nonce=nonce, email=bad_email)
        assert status == 400
        assert proc.poll() is None
    finally:
        _finish(proc)


def test_stdout_is_a_single_line_of_two_whitespace_free_fields():
    proc = _spawn()
    try:
        url = _read_url_line(proc)
        port, nonce = _port_and_nonce(url)
        assert _callback(port, key=KEY, nonce=nonce, email=EMAIL) == 200
        stdout, _ = proc.communicate(timeout=10)
        assert stdout.count("\n") == 1
        assert len(stdout.strip().split()) == 2
    finally:
        _finish(proc)


def test_favicon_request_does_not_extend_the_deadline():
    proc = _spawn(timeout="3")
    try:
        url = _read_url_line(proc)
        port, _ = _port_and_nonce(url)
        start = time.monotonic()
        time.sleep(1.5)
        status = _get(port, "/favicon.ico")
        assert status == 400

        stdout, _ = proc.communicate(timeout=10)
        elapsed = time.monotonic() - start

        assert proc.returncode != 0
        assert stdout == ""
        assert elapsed < 3.8
    finally:
        _finish(proc)


def test_no_request_times_out_with_empty_stdout():
    proc = _spawn(timeout="2")
    try:
        _read_url_line(proc)
        stdout, _ = proc.communicate(timeout=10)
        assert proc.returncode != 0
        assert stdout == ""
    finally:
        _finish(proc)


def test_binds_127_0_0_1_not_0_0_0_0_or_localhost():
    text = LISTENER.read_text()
    assert '"127.0.0.1"' in text
    assert '"0.0.0.0"' not in text
    assert '"localhost"' not in text
