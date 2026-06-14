import socket

import uvicorn
from typer.testing import CliRunner

from llmprof.cli import _port_available, app

runner = CliRunner()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_port_available_helper():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    busy = s.getsockname()[1]
    try:
        assert _port_available("127.0.0.1", busy) is False
    finally:
        s.close()
    # the just-freed port should be bindable again
    assert _port_available("127.0.0.1", busy) is True


def test_up_exits_when_port_in_use(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMPROF_HOME", str(tmp_path))
    called = {"run": False}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: called.__setitem__("run", True))

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    busy = s.getsockname()[1]
    try:
        result = runner.invoke(app, ["up", "--port", str(busy)])
    finally:
        s.close()

    assert result.exit_code == 1
    assert "already in use" in result.output
    assert called["run"] is False  # never tried to start the server


def test_up_starts_when_port_free(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMPROF_HOME", str(tmp_path))
    recorded = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **k: recorded.update(k))

    result = runner.invoke(app, ["up", "--port", str(_free_port())])
    assert result.exit_code == 0
    assert recorded.get("host") == "127.0.0.1"


def test_port_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMPROF_HOME", str(tmp_path))
    free = _free_port()
    monkeypatch.setenv("LLMPROF_PORT", str(free))
    recorded = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **k: recorded.update(k))

    result = runner.invoke(app, ["up"])  # port comes from LLMPROF_PORT
    assert result.exit_code == 0
    assert recorded.get("port") == free
