from html.parser import HTMLParser


class Elements(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.ids = {}
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids[attrs['id']] = (tag, attrs)


def test_native_page_keeps_status_only_ui(client):
    body = client.get('/kiosk/').get_data(as_text=True)
    assert 'browser-camera' not in body
    assert 'js/kiosk.js' in body
    assert 'hidden' not in Elements(body).ids['panel'][1]


def test_preview_page_labels_trial_and_provides_csrf_bound_endpoints(app, client):
    app.config.update(CAMERA_SOURCE='browser', ATTENDANCE_MODE='standalone')
    response = client.get('/kiosk/')
    body = response.get_data(as_text=True)
    ids = Elements(body).ids
    camera = ids['browser-camera'][1]
    assert 'Uji pratinjau kamera — tidak mencatat absensi' in body
    assert camera['data-csrf-token']
    for action in ('start', 'stop', 'challenge', 'frame'):
        assert camera[f'data-{action}-url'] == f'/kiosk/camera/{action}'
    assert ids['browser-camera-start'][1]['type'] == 'button'
    assert 'disabled' in ids['browser-camera-stop'][1]
    assert ids['browser-camera-status'][1]['role'] == 'status'
    assert {'muted', 'playsinline', 'hidden'} <= ids['browser-camera-video'][1].keys()
    assert 'hidden' in ids['panel'][1]
    assert 'js/browser-camera.js' in body and 'js/kiosk.js' in body
    assert response.headers['Cache-Control'] == 'no-store'


def test_eoffice_page_keeps_server_attendance_panel_with_processing_label(app, client):
    app.config.update(CAMERA_SOURCE='browser', ATTENDANCE_MODE='eoffice')
    body = client.get('/kiosk/').get_data(as_text=True)
    assert 'Gambar diproses oleh layanan e-office' in body
    assert 'hidden' not in Elements(body).ids['panel'][1]
    assert 'Uji pratinjau kamera — tidak mencatat absensi' not in body


def test_test_mode_banner_is_visible_with_native_or_browser_camera(app, client):
    app.config.update(ATTENDANCE_MODE='eoffice', EOFFICE_TEST_MODE=True)
    for source in ('native', 'browser'):
        app.config['CAMERA_SOURCE'] = source
        body = client.get('/kiosk/').get_data(as_text=True)
        assert 'Uji pengenalan kamera — tidak mencatat absensi resmi' in body
        assert 'hidden' not in Elements(body).ids['camera-test-banner'][1]
        assert 'hidden' not in Elements(body).ids['panel'][1]
