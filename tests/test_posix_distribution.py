import hashlib
import io
import os
from pathlib import Path
import tarfile

import pytest

from services.update_package import UpdateError, extract_package, verify_manifest
from test_update_package import signed


def tar_archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, kind, content in entries:
            info = tarfile.TarInfo(name)
            info.mode = 0o755
            if kind == 'file':
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            else:
                info.type = {'link': tarfile.SYMTYPE, 'hardlink': tarfile.LNKTYPE,
                             'device': tarfile.CHRTYPE, 'dir': tarfile.DIRTYPE}[kind]
                info.linkname = content
                archive.addfile(info)
    return output.getvalue()


def unpack(tmp_path, entries):
    raw = tar_archive(entries)
    archive = tmp_path / 'package.tar.gz'
    archive.write_bytes(raw)
    candidate = tmp_path / 'candidate'
    extract_package(archive, candidate, {'platform': 'linux-x64', 'size': len(raw),
                                        'sha256': hashlib.sha256(raw).hexdigest()})
    return candidate


@pytest.mark.parametrize('target', ['macos-arm64', 'macos-x64', 'linux-x64'])
def test_platform_manifest_is_bound_to_running_target(target):
    manifest = signed(platform=target)
    assert verify_manifest(*manifest, '1.0.0', expected_platform=target)['platform'] == target
    with pytest.raises(UpdateError):
        verify_manifest(*manifest, '1.0.0', expected_platform='windows-x64')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX tar executable and symlink semantics')
def test_tar_preserves_executable_and_internal_symlinks(tmp_path):
    candidate = unpack(tmp_path, [('Absensi', 'file', b'executable'),
                                 ('lib/actual.so', 'file', b'library'),
                                 ('lib/alias.so', 'link', 'actual.so'),
                                 ('alias', 'link', 'lib')])
    assert os.access(candidate / 'Absensi', os.X_OK)
    assert (candidate / 'lib/alias.so').is_symlink()
    assert (candidate / 'alias/actual.so').read_bytes() == b'library'


@pytest.mark.parametrize('entries', [
    [('../escape', 'file', b'x')], [('/absolute', 'file', b'x')],
    [('a/../b', 'file', b'x')], [('a\\b', 'file', b'x')],
    [('a', 'file', b'x'), ('A', 'file', b'y')],
    [('link', 'link', '/etc/passwd')], [('link', 'link', '../outside')],
    [('link', 'link', 'missing')], [('a', 'link', 'b'), ('b', 'link', 'a')],
    [('a', 'link', 'lib'), ('a/write', 'file', b'x'), ('lib', 'dir', '')],
    [('link', 'hardlink', 'Absensi')], [('device', 'device', '')],
    [('file', 'file', b'x'), ('file/child', 'file', b'y')],
    [('dir/up', 'link', '..'), ('attack', 'link', 'dir/up/..'),
     ('ATTACK/created', 'link', '../safe')],
    [('é', 'file', b'x'), ('e\u0301', 'file', b'y')],
])
def test_unsafe_tar_leaves_no_candidate(tmp_path, entries):
    with pytest.raises(UpdateError):
        unpack(tmp_path, entries)
    assert not (tmp_path / 'candidate').exists()
    assert not (tmp_path / 'escape').exists()
    assert not (tmp_path / 'created').is_symlink()


def test_tar_limits_are_enforced(tmp_path, monkeypatch):
    import services.update_package as package
    monkeypatch.setattr(package, 'MAX_EXTRACTED', 2)
    with pytest.raises(UpdateError):
        unpack(tmp_path, [('Absensi', 'file', b'large')])
    assert not (tmp_path / 'candidate').exists()


@pytest.mark.parametrize('system,machine,target,relative', [
    ('win32', 'AMD64', 'windows-x64', 'Absensi.exe'),
    ('darwin', 'arm64', 'macos-arm64', 'Absensi.app/Contents/MacOS/Absensi'),
    ('darwin', 'x86_64', 'macos-x64', 'Absensi.app/Contents/MacOS/Absensi'),
    ('linux', 'x86_64', 'linux-x64', 'Absensi'),
])
def test_platform_and_executable_layout(tmp_path, monkeypatch, system, machine, target, relative):
    from maintenance import platforms
    from maintenance.distribution import executable
    monkeypatch.setattr(platforms.sys, 'platform', system)
    monkeypatch.setattr(platforms.platform, 'machine', lambda: machine)
    assert platforms.platform_id() == target
    assert executable(tmp_path, '1.0.0') == tmp_path / 'versions/1.0.0' / relative


def test_unsupported_platform_is_rejected(monkeypatch):
    from maintenance import platforms
    monkeypatch.setattr(platforms.sys, 'platform', 'linux')
    monkeypatch.setattr(platforms.platform, 'machine', lambda: 'aarch64')
    with pytest.raises(UpdateError):
        platforms.platform_id()


@pytest.mark.parametrize('target', ['windows-x64', 'macos-arm64', 'macos-x64', 'linux-x64'])
def test_update_manager_downloads_only_its_platform_metadata(app, tmp_path, monkeypatch, target):
    from maintenance import platforms
    from maintenance.distribution import atomic_json
    import services.app_updates as updates
    from threading import Event
    monkeypatch.setattr(platforms, 'platform_id', lambda: target)
    atomic_json(tmp_path / 'current.json', {'version': '1.0.0'})
    body, signature, key = signed(platform=target)
    urls = []
    def metadata(url, *args):
        urls.append(url)
        return signature if url.endswith('.sig') else body
    monkeypatch.setattr(updates, 'fetch_metadata', metadata)
    manager = updates.UpdateManager(app, tmp_path, key, Event())
    manager._check()
    assert urls == [updates.RELEASE_BASE + platforms.metadata_name(target),
                    updates.RELEASE_BASE + platforms.metadata_name(target) + '.sig']
    assert manager.status()['stage'] == 'available'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX update executable permissions')
@pytest.mark.parametrize('target', ['macos-arm64', 'macos-x64', 'linux-x64'])
def test_install_stages_platform_executable_without_stopping_early(app, tmp_path, monkeypatch, target):
    from maintenance import platforms
    from maintenance.distribution import atomic_json, executable
    import services.app_updates as updates
    from threading import Event
    from test_update_package import signed
    monkeypatch.setattr(platforms, 'platform_id', lambda: target)
    root = tmp_path / 'install'
    atomic_json(root / 'current.json', {'version': '1.0.0'})
    (root / 'updates').mkdir()
    instance = tmp_path / 'instance'
    instance.mkdir()
    app.instance_path = str(instance)
    raw = tar_archive([(platforms.executable_path(target).as_posix(), 'file', b'executable')])
    body, signature, key = signed(platform=target, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / 'updates/offer.json').write_bytes(body)
    (root / 'updates/offer.sig').write_bytes(signature)
    manager = updates.UpdateManager(app, root, key, Event())
    monkeypatch.setattr(updates, 'download_package', lambda metadata, path, progress: path.write_bytes(raw))
    calls = []
    def spawn(args, **kwargs):
        assert not manager.stop_event.is_set()
        assert Path(args[0]) == executable(root, '1.1.0')
        assert Path(args[0]).read_bytes() == b'executable'
        assert os.access(args[0], os.X_OK)
        calls.append(kwargs)
    monkeypatch.setattr(updates.subprocess, 'Popen', spawn)
    manager._busy.acquire()
    manager._job('install')
    assert len(calls) == 1
    assert calls[0]['start_new_session']
    assert manager.stop_event.is_set()
    assert manager.status()['stage'] == 'stopping'
    assert (root / 'current.json').read_text().find('1.0.0') != -1
