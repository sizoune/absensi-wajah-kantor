"""Fault checks for the E2E process fixture; no server or database is started."""
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_eoffice_end_to_end import LiveAPI


@pytest.fixture
def child(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    process = SimpleNamespace(returncode=None, stdin=Mock(), stdout=Mock())
    process.poll = Mock(side_effect=lambda: process.returncode)
    process.kill = Mock(side_effect=lambda: setattr(process, 'returncode', -9))
    process.wait = Mock(side_effect=lambda **kwargs: process.returncode)
    reader = Mock()
    monkeypatch.setattr('test_eoffice_end_to_end.subprocess.Popen', lambda *a, **k: process)
    monkeypatch.setattr('test_eoffice_end_to_end.threading.Thread', lambda *a, **k: reader)
    process.reader = reader
    return process


def assert_reaped(child: SimpleNamespace) -> None:
    child.kill.assert_called_once()
    child.wait.assert_called()
    child.stdin.close.assert_called()
    child.stdout.close.assert_called_once()
    child.reader.join.assert_called_once()
    assert child.returncode == -9


@pytest.mark.parametrize('error', [TimeoutError('close timeout'), ValueError('malformed reply'),
                                  BrokenPipeError('close pipe failed')])
def test_shutdown_handshake_failure_still_reaps_child(child: SimpleNamespace, error: Exception) -> None:
    api = object.__new__(LiveAPI)
    api.process, api.reader = child, child.reader
    api.command = Mock(side_effect=error)
    with pytest.raises(type(error)) as caught:
        api.close()
    assert caught.value is error
    assert_reaped(child)


def test_shutdown_stdin_close_failure_still_reaps_child(child: SimpleNamespace) -> None:
    api = object.__new__(LiveAPI)
    api.process, api.reader = child, child.reader
    api.command = Mock()
    error = BrokenPipeError('stdin close failed')
    child.stdin.close.side_effect = error
    with pytest.raises(BrokenPipeError) as caught:
        api.close()
    assert caught.value is error
    assert_reaped(child)


@pytest.mark.parametrize('ready', [{}, {'base': 'http://127.0.0.1:1'}, {'secret': 'test-only'}])
def test_malformed_startup_readiness_reaps_child(child: SimpleNamespace,
                                               monkeypatch: pytest.MonkeyPatch, ready: dict) -> None:
    monkeypatch.setattr(LiveAPI, '_read', lambda self: ready)
    with pytest.raises(KeyError):
        LiveAPI(StringIO())
    assert_reaped(child)


def test_startup_read_timeout_reaps_child(child: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    error = TimeoutError('startup timeout')
    monkeypatch.setattr(LiveAPI, '_read', Mock(side_effect=error))
    with pytest.raises(TimeoutError) as caught:
        LiveAPI(StringIO())
    assert caught.value is error
    assert_reaped(child)
