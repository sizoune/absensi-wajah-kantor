"""DirectShow discovery balances the thread-local Windows COM boundary."""
from concurrent.futures import ThreadPoolExecutor
import sys
from threading import Barrier, get_ident, local
from types import SimpleNamespace

import pytest

from camera import devices


def test_directshow_discovery_works_on_each_http_worker_and_releases_graph_first(monkeypatch):
    state, ready, threads = local(), Barrier(3), set()

    def initialize():
        state.initialized = True
        threads.add(get_ident())

    def uninitialize():
        assert not state.graph_alive
        state.initialized = False

    class FilterGraph:
        def __init__(self):
            assert getattr(state, 'initialized', False), 'COM was not initialized on this worker'
            state.graph_alive = True

        def get_input_devices(self):
            ready.wait(timeout=3)
            return ['Integrated camera', 'Logitech C270']

        def __del__(self):
            state.graph_alive = False

    monkeypatch.setattr(devices.platform, 'system', lambda: 'Windows')
    monkeypatch.setitem(sys.modules, 'comtypes', SimpleNamespace(
        CoInitialize=initialize, CoUninitialize=uninitialize))
    monkeypatch.setitem(sys.modules, 'pygrabber.dshow_graph', SimpleNamespace(FilterGraph=FilterGraph))

    def discover(_):
        result = devices.list_cameras()
        assert not getattr(state, 'initialized', False)
        return result

    with ThreadPoolExecutor(max_workers=3) as executor:
        assert list(executor.map(discover, range(6))) == [
            ['Integrated camera', 'Logitech C270']] * 6
    assert len(threads) == 3


@pytest.mark.parametrize('failure', ['initialize', 'construct', 'discover'])
def test_com_failures_release_initialized_apartment_and_keep_safe_result(monkeypatch, failure):
    state = {'initialized': False, 'graph_alive': False}

    def initialize():
        if failure == 'initialize':
            raise OSError('COM initialization failed')
        state['initialized'] = True

    def uninitialize():
        assert not state['graph_alive']
        state['initialized'] = False

    class FilterGraph:
        def __init__(self):
            if failure == 'construct':
                raise OSError('DirectShow graph unavailable')
            state['graph_alive'] = True

        def get_input_devices(self):
            raise OSError('DirectShow enumeration unavailable')

        def __del__(self):
            state['graph_alive'] = False

    monkeypatch.setattr(devices.platform, 'system', lambda: 'Windows')
    monkeypatch.setitem(sys.modules, 'comtypes', SimpleNamespace(
        CoInitialize=initialize, CoUninitialize=uninitialize))
    monkeypatch.setitem(sys.modules, 'pygrabber.dshow_graph', SimpleNamespace(FilterGraph=FilterGraph))
    assert devices.list_cameras() is None
    assert state == {'initialized': False, 'graph_alive': False}
