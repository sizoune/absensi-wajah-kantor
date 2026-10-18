"""Restricted scopes are explicit and remain bound to server-authorized metadata."""
import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from test_setup_organization import DEVICE_ID, IDENTITY, ORGANIZATIONS, TOKEN, post, wizard
from test_eoffice_sync import SNAPSHOT

SCOPES=['all_skpd','parent','subunit']
SUBUNITS=[{'subunit_id':3,'skpd_id':9,'name':'Subunit A'}, {'subunit_id':4,'skpd_id':9,'name':'Subunit B'}]


def authorize_subunits(replies):
    info=copy.deepcopy(ORGANIZATIONS)
    info['organizations'][0].update(scope_types=SCOPES,subunits=SUBUNITS)
    replies['/api/kiosk/organizations']=info


@pytest.mark.parametrize('scope_type,subunit_id',[('parent',None),('subunit',3),('subunit',4)])
def test_operator_selects_parent_or_named_subunit_and_snapshot_is_bound_to_it(wizard,scope_type,subunit_id):
    app,client,replies,calls=wizard
    authorize_subunits(replies)
    assert post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example').status_code==302
    page=client.get('/setup').get_data(as_text=True)
    assert 'Induk/non-subunit' in page
    assert 'Subunit tertentu' in page
    assert 'Subunit A' in page and 'Subunit B' in page
    scope={'skpd_id':9,'scope_type':scope_type,'subunit_id':subunit_id}
    replies['/api/kiosk/scope']={**IDENTITY,**scope}
    values={'skpd_id':'9','scope_type':scope_type}
    if subunit_id is not None:
        values['subunit_id']=str(subunit_id)
    assert post(client,'/setup/organization',**values).status_code==302
    assert app.config['EOFFICE_SCOPE_TYPE']==scope_type
    assert app.config['EOFFICE_SUBUNIT_ID']==subunit_id
    status=client.get('/setup/organization').json
    assert status['scope_type']==scope_type
    assert status['subunit_id']==subunit_id
    snapshot=next(request for request in reversed(calls) if urlsplit(request.full_url).path.endswith('/snapshot'))
    assert json.loads(parse_qs(urlsplit(snapshot.full_url).query)['scope'][0])==scope


@pytest.mark.parametrize('scope_type,subunit_id',[
    ('all_skpd','3'),('parent','3'),('subunit',''),('subunit','0'),('subunit','5'),
])
def test_forged_or_ambiguous_subunit_is_rejected_before_remote_scope_change(wizard,scope_type,subunit_id):
    app,client,replies,calls=wizard
    authorize_subunits(replies)
    assert post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example').status_code==302
    calls.clear()
    assert post(client,'/setup/organization',skpd_id='9',scope_type=scope_type,subunit_id=subunit_id).status_code==400
    assert app.config.get('EOFFICE_SCOPE_TYPE') is None
    assert calls==[]


def test_cross_skpd_subunit_metadata_is_not_saved(wizard):
    app,client,replies,_=wizard
    authorize_subunits(replies)
    replies['/api/kiosk/organizations']['organizations'][0]['subunits']=[
        {'subunit_id':5,'skpd_id':10,'name':'Foreign Subunit'}]
    assert post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example').status_code==400
    assert app.config['EOFFICE_DEVICE_TOKEN']==''


def test_changed_scope_hides_old_gallery_on_partial_download_then_resumes_after_retry(wizard,tmp_path):
    from app import create_app
    from test_setup import exchange, local_code
    _,client,replies,_=wizard
    authorize_subunits(replies)
    assert post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example').status_code==302
    assert post(client,'/setup/organization',skpd_id='9',scope_type='all_skpd').status_code==302
    previous=(tmp_path/'eoffice.sqlite3').read_bytes()
    replies['/api/kiosk/scope']={**IDENTITY,'scope_type':'parent'}
    replies['/api/kiosk/snapshot']=b'{"protocol_version":1,'
    assert post(client,'/setup/organization',skpd_id='9',scope_type='parent').status_code==400
    status=client.get('/setup/organization').json
    assert status['scope_type']=='parent'
    assert status['snapshot']['available'] is False
    assert client.get('/setup/status').json['steps']['snapshot'] is False
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==previous
    replies['/api/kiosk/snapshot']=SNAPSHOT
    assert post(client,'/setup/snapshot').status_code==302
    restarted=create_app('testing',overrides={'FIRST_SETUP_ENABLED':True,'ATTENDANCE_MODE':'eoffice',
        'SECRET_KEY':None,'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS':{},'WTF_CSRF_ENABLED':True},instance_path=str(tmp_path))
    resumed=restarted.test_client()
    exchange(resumed,local_code(restarted))
    status=resumed.get('/setup/organization').json
    assert status['device_id']==DEVICE_ID
    assert status['scope_type']=='parent' and status['subunit_id'] is None
    assert status['snapshot']['available'] is True


def test_pending_events_block_scope_change_without_rewriting_body_or_gallery(wizard,tmp_path):
    import base64
    from integration.store import IntegrationStore
    app,client,replies,calls=wizard
    authorize_subunits(replies)
    assert post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example').status_code==302
    assert post(client,'/setup/organization',skpd_id='9',scope_type='all_skpd').status_code==302
    key=base64.b64decode(json.loads((tmp_path/'biometric_keys.json').read_text())['keys']['1'])
    store=IntegrationStore(tmp_path/'eoffice.sqlite3',key)
    body=b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab42"}'
    store.enqueue(body)
    store.close()
    previous=(tmp_path/'eoffice.sqlite3').read_bytes()
    calls.clear()
    assert post(client,'/setup/organization',skpd_id='9',scope_type='subunit',subunit_id='3').status_code==400
    assert app.config['EOFFICE_SCOPE_TYPE']=='all_skpd'
    assert calls==[]
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==previous
    store=IntegrationStore(tmp_path/'eoffice.sqlite3',key)
    try:
        assert store.pending()[0].body==body
    finally:
        store.close()
