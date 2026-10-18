"""Saved scope remains an HTTPS transport constraint on every gallery refresh."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from integration.client import EofficeClient, RequestFailure
from integration.clock import TrustedClock
from integration.store import IntegrationStore
from integration.sync import SyncWorker
from test_eoffice_sync import EVENT, Response, SNAPSHOT

SCOPE={'skpd_id':9,'scope_type':'parent','subunit_id':None}


def test_bound_refresh_pauses_on_remote_scope_change_without_sending_or_rewriting_queue(tmp_path,monkeypatch):
    client=EofficeClient('https://server.example','a'*64,scope=SCOPE)
    replies=[Response(json.dumps(SNAPSHOT).encode()),RequestFailure(403)]
    requests=[]
    def open_remote(request,**kwargs):
        requests.append(request)
        reply=replies.pop(0)
        if isinstance(reply,Exception):
            raise reply
        return reply
    monkeypatch.setattr(client._opener,'open',open_remote)
    store=IntegrationStore(tmp_path/'eoffice.sqlite3',bytes(range(32)))
    try:
        worker=SyncWorker(store,client,TrustedClock(store))
        assert worker.run_once().connection=='online'
        body=json.dumps(EVENT).encode()
        store.enqueue(body)
        original=(tmp_path/'eoffice.sqlite3').read_bytes()
        worker.resume()
        status=worker.run_once()
        assert status.auth=='paused'
        assert status.events_sent==0
        assert store.pending()[0].body==body
        assert (tmp_path/'eoffice.sqlite3').read_bytes()==original
        assert store.load_snapshot()==SNAPSHOT
        assert len(requests)==2
        assert all(json.loads(parse_qs(urlsplit(request.full_url).query)['scope'][0])==SCOPE for request in requests)
    finally:
        store.close()


def test_bound_snapshot_retains_header_uncertainty_limit(monkeypatch):
    client=EofficeClient('https://server.example','a'*64,scope=SCOPE)
    clock=iter([10.0,12.1])
    client._monotonic=lambda:next(clock)
    monkeypatch.setattr(client._opener,'open',lambda *args,**kwargs:Response(json.dumps(SNAPSHOT).encode()))
    with pytest.raises(ValueError,match='uncertainty'):
        client.snapshot()


@pytest.mark.parametrize('scope',[
    {**SCOPE,'scope_type':'subunit'},{**SCOPE,'subunit_id':3},
    {**SCOPE,'scope_type':'subunit','subunit_id':0},{**SCOPE,'scope_type':'subunit','subunit_id':True},
    {**SCOPE,'scope_type':'subunit','subunit_id':'3'},{**SCOPE,'skpd_id':True},
])
def test_client_rejects_ambiguous_scope_before_transport(scope):
    with pytest.raises(ValueError):
        EofficeClient('https://server.example','a'*64,scope=scope)
