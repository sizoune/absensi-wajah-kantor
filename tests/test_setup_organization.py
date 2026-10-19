"""Organization setup over Flask HTTP and the real bounded eOffice transport."""
import copy
import json
from uuid import uuid4
from urllib.parse import urlsplit

import pytest

from test_setup import csrf, exchange, installation, local_code
from test_eoffice_sync import Response, SNAPSHOT

DEVICE_ID='c24c3542-bc56-4aae-8bf5-557980e68870'
TOKEN='a'*64
IDENTITY={'device_id':DEVICE_ID,'skpd_id':9,'scope_type':'all_skpd','subunit_id':None,'purpose':'attendance'}
ORGANIZATIONS={'device':IDENTITY,'organizations':[{'skpd_id':9,'name':'SKPD Uji','scope_types':['all_skpd']}]}


@pytest.fixture
def wizard(tmp_path, monkeypatch):
    replies={'/api/kiosk/organizations':ORGANIZATIONS,'/api/kiosk/scope':IDENTITY,
             '/api/kiosk/snapshot':SNAPSHOT,
             '/api/kiosk/revision':{'policy_revision':SNAPSHOT['policy_revision']}}
    calls=[]
    def open_remote(opener, request, **kwargs):
        calls.append(request)
        value=replies[urlsplit(request.full_url).path]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, bytes):
            return Response(value)
        return Response(json.dumps(value).encode())
    monkeypatch.setattr('urllib.request.OpenerDirector.open', open_remote)
    app=installation(tmp_path)
    client=app.test_client()
    exchange(client,local_code(app))
    client.post('/setup/admin',data={'csrf_token':csrf(client),'username':'operator','password':'PasswordUji123'})
    return app,client,replies,calls


def post(client,path,**values):
    return client.post(path,data={'csrf_token':csrf(client),**values})


def test_operator_saves_secret_token_and_selects_server_authorized_whole_skpd(wizard, tmp_path, caplog):
    app,client,replies,calls=wizard
    page=client.get('/setup').get_data(as_text=True)
    assert 'https://api-e-office.tabalongkab.go.id' in page
    result=post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example')
    assert result.status_code==302
    page=client.get('/setup').get_data(as_text=True)
    assert 'SKPD Uji' in page
    assert TOKEN not in page
    result=post(client,'/setup/organization',skpd_id='9',scope_type='all_skpd')
    assert result.status_code==302
    status=client.get('/setup/organization').json
    assert status['device_id']==DEVICE_ID
    assert status['scope_type']=='all_skpd'
    assert status['skpd_id']==9
    assert status['snapshot']['available'] is True
    assert status['snapshot']['enrollment_count']==len(SNAPSHOT['employees'])
    assert app.config['EOFFICE_DEVICE_TOKEN']==TOKEN
    assert app.config['EOFFICE_DEVICE_ID']==DEVICE_ID
    assert TOKEN not in client.get('/setup/status').get_data(as_text=True)
    assert TOKEN not in client.get('/setup/organization').get_data(as_text=True)
    assert TOKEN not in caplog.text
    assert TOKEN.encode() not in (tmp_path/'app.sqlite3').read_bytes()
    assert SNAPSHOT['employees'][0]['display_name'].encode() not in (tmp_path/'eoffice.sqlite3').read_bytes()
    assert all(request.get_header('Authorization')=='Bearer '+TOKEN for request in calls)
    assert app.extensions['browser_camera']._thread is None


def connect_and_select(client):
    assert post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example').status_code==302
    assert post(client,'/setup/organization',skpd_id='9',scope_type='all_skpd').status_code==302


def test_partial_snapshot_keeps_previous_encrypted_gallery_and_valid_empty_retry_is_saved(wizard, tmp_path):
    _,client,replies,_=wizard
    connect_and_select(client)
    original=(tmp_path/'eoffice.sqlite3').read_bytes()
    replies['/api/kiosk/snapshot']=b'{"protocol_version":1,'
    assert post(client,'/setup/snapshot').status_code==400
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==original
    assert client.get('/setup/organization').json['snapshot']['available'] is True
    empty=copy.deepcopy(SNAPSHOT)
    empty.update(snapshot_id=str(uuid4()),employees=[])
    empty['clock_anchor_id']=empty['snapshot_id']
    replies['/api/kiosk/snapshot']=empty
    assert post(client,'/setup/snapshot').status_code==302
    snapshot=client.get('/setup/organization').json['snapshot']
    assert snapshot['available'] is True
    assert snapshot['empty'] is True
    assert snapshot['enrollment_count']==0
    assert 'belum siap mengenali wajah' in client.get('/setup').get_data(as_text=True)
    assert client.get('/setup/status').json['steps']['snapshot'] is True


def test_changing_device_does_not_present_old_snapshot_as_current_when_download_fails(wizard, tmp_path):
    _,client,replies,_=wizard
    connect_and_select(client)
    original=(tmp_path/'eoffice.sqlite3').read_bytes()
    other_id=str(uuid4())
    replies['/api/kiosk/organizations']={**ORGANIZATIONS,'device':{**IDENTITY,'device_id':other_id}}
    replies['/api/kiosk/scope']={**IDENTITY,'device_id':other_id}
    assert post(client,'/setup/credentials',token='b'*64,base_url='https://server.example').status_code==302
    replies['/api/kiosk/snapshot']=TimeoutError('body with sensitive credential should never be displayed')
    assert post(client,'/setup/organization',skpd_id='9',scope_type='all_skpd').status_code==400
    assert client.get('/setup/organization').json['snapshot']['available'] is False
    assert client.get('/setup/status').json['steps']['snapshot'] is False
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==original


@pytest.mark.parametrize('url',['http://server.example','https://user:secret@server.example',
                              'https://server.example?token=secret','https://server.example:wrong'])
def test_advanced_url_rejects_insecure_or_credential_bearing_addresses(wizard,url):
    app,client,_,calls=wizard
    assert post(client,'/setup/credentials',token=TOKEN,base_url=url).status_code==400
    assert calls==[]
    assert app.config['EOFFICE_DEVICE_TOKEN']==''


def test_invalid_token_does_not_overwrite_saved_credentials_or_expose_upstream_error(wizard,caplog):
    from integration.client import RequestFailure
    app,client,replies,_=wizard
    connect_and_select(client)
    replies['/api/kiosk/organizations']=RequestFailure(401)
    result=post(client,'/setup/credentials',token='rejected-secret',base_url='https://other.example')
    assert result.status_code==403
    assert app.config['EOFFICE_DEVICE_TOKEN']==TOKEN
    assert app.config['EOFFICE_BASE_URL']=='https://server.example'
    assert app.config['EOFFICE_DEVICE_ID']==DEVICE_ID
    assert 'rejected-secret' not in result.get_data(as_text=True)
    assert 'rejected-secret' not in caplog.text


def test_setup_grant_and_csrf_are_required_before_remote_requests(wizard):
    app,client,_,calls=wizard
    other=app.test_client()
    assert post(other,'/setup/credentials',token=TOKEN,base_url='https://server.example').status_code==403
    assert client.post('/setup/credentials',data={'token':TOKEN,'base_url':'https://server.example'}).status_code==302
    assert calls==[]


def test_a_different_device_cannot_take_over_the_existing_queue(wizard,tmp_path):
    from integration.store import IntegrationStore
    import base64
    _,client,replies,_=wizard
    connect_and_select(client)
    key=base64.b64decode(json.loads((tmp_path/'biometric_keys.json').read_text())['keys']['1'])
    store=IntegrationStore(tmp_path/'eoffice.sqlite3',key)
    body=b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab42"}'
    store.enqueue(body)
    store.close()
    original=(tmp_path/'eoffice.sqlite3').read_bytes()
    replies['/api/kiosk/organizations']={**ORGANIZATIONS,'device':{**IDENTITY,'device_id':str(uuid4())}}
    assert post(client,'/setup/credentials',token='b'*64,base_url='https://server.example').status_code==400
    assert client.get('/setup/organization').json['device_id']==DEVICE_ID
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==original
    store=IntegrationStore(tmp_path/'eoffice.sqlite3',key)
    assert store.pending()[0].body==body
    store.close()


def test_selected_scope_and_empty_snapshot_resume_after_restart(wizard,tmp_path):
    from app import create_app
    _,client,replies,_=wizard
    empty=copy.deepcopy(SNAPSHOT)
    empty['employees']=[]
    replies['/api/kiosk/snapshot']=empty
    connect_and_select(client)
    restarted=create_app('testing',overrides={'FIRST_SETUP_ENABLED':True,'ATTENDANCE_MODE':'eoffice',
        'SECRET_KEY':None,'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS':{},'WTF_CSRF_ENABLED':True},instance_path=str(tmp_path))
    resumed=restarted.test_client()
    exchange(resumed,local_code(restarted))
    status=resumed.get('/setup/organization').json
    assert status['device_id']==DEVICE_ID
    assert status['skpd_id']==9
    assert status['scope_type']=='all_skpd'
    assert status['snapshot']['empty'] is True
    assert 'belum siap mengenali wajah' in resumed.get('/setup').get_data(as_text=True)


@pytest.mark.parametrize('corrupt',[
    {'device':{**IDENTITY,'skpd_id':True}},
    {'device':{**IDENTITY,'credential_hash':'must-not-escape'}},
    {'organizations':[{'skpd_id':10,'name':'Forbidden SKPD','scope_types':['all_skpd']}]},
    {'organizations':[{'skpd_id':9,'name':'','scope_types':['all_skpd']}]},
])
def test_malformed_or_contradictory_server_permission_data_is_not_saved(wizard,corrupt):
    app,client,replies,_=wizard
    replies['/api/kiosk/organizations']={**ORGANIZATIONS,**corrupt}
    result=post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example')
    assert result.status_code==400
    assert app.config['EOFFICE_DEVICE_TOKEN']==''
    assert 'must-not-escape' not in result.get_data(as_text=True)


def test_forged_subunit_is_not_silently_accepted_as_whole_skpd(wizard):
    _,client,_,calls=wizard
    assert post(client,'/setup/credentials',token=TOKEN,base_url='https://server.example').status_code==302
    calls.clear()
    assert post(client,'/setup/organization',skpd_id='9',scope_type='all_skpd',subunit_id='3').status_code==400
    assert calls==[]
