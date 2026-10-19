import hashlib
import io
import json
import stat
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.update_package import UpdateError, extract_package, verify_manifest, version_tuple, valid_url


def signed(**changes):
    key = Ed25519PrivateKey.generate()
    value = dict(format_version=1, version='1.1.0', platform='windows-x64',
                 url='https://github.com/sizoune/absensi-wajah-kantor/releases/download/v1.1.0/windows-x64.zip',
                 size=100, sha256='a' * 64, min_updater_version=1, notes='Perbaikan')
    value.update(changes)
    body = json.dumps(value).encode()
    return body, key.sign(body), key.public_key().public_bytes_raw()


def test_signature_checked_before_json_and_version_policy():
    body, signature, key = signed()
    assert verify_manifest(body, signature, key, '1.0.0')['version'] == '1.1.0'
    for altered in [body + b' ', b'not json']:
        with pytest.raises(UpdateError):
            verify_manifest(altered, signature, key, '1.0.0')
    for changes in [dict(version='1.0.0'), dict(version='0.9.0'), dict(version='1.1.0-rc1'),
                    dict(platform='linux-x64'), dict(min_updater_version=2), dict(size=True),
                    dict(size=0), dict(size=10**12), dict(sha256='x' * 64), dict(format_version=2),
                    dict(url='https://evil.example/a.zip')]:
        with pytest.raises(UpdateError):
            verify_manifest(*signed(**changes), '1.0.0')


@pytest.mark.parametrize('url', ['http://github.com/a', 'https://github.com.evil/a',
    'https://user@github.com/a', 'https://github.com:444/a', 'file:///etc/passwd',
    'https://github.com/other/repo/releases/download/v1/x.zip',
    'https://github.com/sizoune/absensi-wajah-kantor/releases/download/v1/x.zip#bad'])
def test_untrusted_download_urls_are_rejected(url):
    with pytest.raises(UpdateError):
        valid_url(url)


def test_only_canonical_stable_semver():
    assert version_tuple('1.10.0') > version_tuple('1.9.9')
    for value in ['01.0.0', '../1.0', '1.0', '1.0.0+build', 'v1.0.0']:
        with pytest.raises(UpdateError):
            version_tuple(value)


def archive(entries):
    value = io.BytesIO()
    with zipfile.ZipFile(value, 'w') as z:
        for name, data in entries:
            info = name if isinstance(name, zipfile.ZipInfo) else zipfile.ZipInfo(name)
            if isinstance(name, str):
                info.filename = name  # Preserve malicious wire names on Windows as well as Unix.
            z.writestr(info, data)
    return value.getvalue()


@pytest.mark.parametrize('name', ['../escape', '/abs', 'C:/abs', 'a\\b',
    'CON', 'a/../b', 'a./b', 'file:stream', 'a /b'])
def test_unsafe_archive_names_leave_no_candidate(tmp_path, name):
    raw = archive([(name, b'x')])
    path = tmp_path / 'package.zip'
    path.write_bytes(raw)
    with pytest.raises(UpdateError):
        extract_package(path, tmp_path / 'candidate', dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    assert not (tmp_path / 'candidate').exists()


def test_duplicate_and_symlink_archive_entries_rejected(tmp_path):
    link = zipfile.ZipInfo('link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for entries in [[('a', b'x'), ('A', b'y')], [(link, b'/etc/passwd')]]:
        raw = archive(entries)
        path = tmp_path / 'package.zip'
        path.write_bytes(raw)
        with pytest.raises(UpdateError):
            extract_package(path, tmp_path / 'candidate', dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))


def test_hash_failure_and_successful_extraction(tmp_path):
    raw = archive([('Absensi.exe', b'exe'), ('_internal/data.txt', b'data')])
    path = tmp_path / 'package.zip'
    path.write_bytes(raw)
    manifest = dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(UpdateError):
        extract_package(path, tmp_path / 'candidate', {**manifest, 'sha256': '0' * 64})
    extract_package(path, tmp_path / 'candidate', manifest)
    assert (tmp_path / 'candidate/Absensi.exe').read_bytes() == b'exe'


@pytest.mark.parametrize('body,expected_size,valid', [(b'abc', 3, True), (b'a', 3, False), (b'abcd', 3, False)])
def test_download_is_bounded_and_partial_never_activated(tmp_path, monkeypatch, body, expected_size, valid):
    import services.update_package as package
    monkeypatch.setattr(package, 'open_release', lambda url: io.BytesIO(body))
    destination = tmp_path / 'package.zip'
    metadata = {'url': 'ignored', 'size': expected_size, 'sha256': hashlib.sha256(b'abc').hexdigest()}
    if valid:
        package.download_package(metadata, destination)
        assert destination.read_bytes() == b'abc'
    else:
        with pytest.raises(UpdateError):
            package.download_package(metadata, destination)
        assert not destination.exists()
    assert not list(tmp_path.glob('*.partial'))


def test_network_disconnect_leaves_no_package(tmp_path, monkeypatch):
    import services.update_package as package
    class Broken(io.BytesIO):
        def read(self, size):
            raise OSError('disconnected')
    monkeypatch.setattr(package, 'open_release', lambda url: Broken())
    with pytest.raises(OSError):
        package.download_package({'url': 'ignored', 'size': 3, 'sha256': 'a' * 64}, tmp_path / 'package.zip')
    assert not list(tmp_path.iterdir())


def test_original_zip_name_is_checked_before_windows_normalization(tmp_path, monkeypatch):
    raw = archive([('a\\b', b'x')])
    path = tmp_path / 'package.zip'
    path.write_bytes(raw)
    original = zipfile.ZipFile.infolist
    def normalized(zf):
        entries = original(zf)
        for entry in entries:
            entry.filename = entry.filename.replace('\\', '/')
        return entries
    monkeypatch.setattr(zipfile.ZipFile, 'infolist', normalized)
    with pytest.raises(UpdateError):
        extract_package(path, tmp_path / 'candidate', dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
