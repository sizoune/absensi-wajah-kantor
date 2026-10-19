"""Verify signed release metadata and untrusted Windows archives."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app_version import UPDATER_VERSION

REPOSITORY = 'sizoune/absensi-wajah-kantor'
RELEASE_BASE = f'https://github.com/{REPOSITORY}/releases/latest/download/'
MAX_PACKAGE = 2 * 1024**3
MAX_EXTRACTED = 6 * 1024**3
MAX_METADATA = 64 * 1024
CDN_HOSTS = {'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}


class UpdateError(Exception):
    """Expected update failure; callers expose a fixed message, never URLs/secrets."""


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise UpdateError('Versi rilis tidak valid.')
    return tuple(map(int, value.split('.')))


def valid_url(value):
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.username or url.password or url.port not in (None, 443)
                or url.fragment or any(c.isspace() for c in value)):
            raise ValueError
        if url.hostname == 'github.com':
            if not url.path.startswith(f'/{REPOSITORY}/releases/') or url.query:
                raise ValueError
        elif url.hostname not in CDN_HOSTS:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise UpdateError('Sumber pembaruan tidak diizinkan.') from exc
    return value


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        valid_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_release(url):
    valid_url(url)
    # Do not inherit proxy authentication or send the device's bearer credentials.
    opener = build_opener(ProxyHandler({}), ReleaseRedirects())
    return opener.open(Request(url, headers={'User-Agent': 'AbsensiUpdater/1',
                                             'Accept-Encoding': 'identity'}), timeout=20)


def fetch_metadata(url, limit=MAX_METADATA):
    with open_release(url) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise UpdateError('Metadata pembaruan terlalu besar.')
    return raw


def verify_manifest(raw, signature, public_key, current_version, *, allow_equal=False):
    try:
        if len(raw) > MAX_METADATA or len(signature) != 64:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, raw)
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {
                'format_version', 'version', 'platform', 'url', 'size', 'sha256',
                'min_updater_version', 'notes'}:
            raise ValueError
        if (type(value['format_version']) is not int or value['format_version'] != 1
                or value['platform'] != 'windows-x64'
                or type(value['min_updater_version']) is not int
                or not 1 <= value['min_updater_version'] <= UPDATER_VERSION
                or type(value['size']) is not int or not 0 < value['size'] <= MAX_PACKAGE
                or not isinstance(value['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', value['sha256'])
                or not isinstance(value['notes'], str) or len(value['notes']) > 16000):
            raise ValueError
        offered, current = version_tuple(value['version']), version_tuple(current_version)
        if offered < current or offered == current and not allow_equal:
            raise ValueError
        valid_url(value['url'])
    except (InvalidSignature, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise UpdateError('Metadata atau tanda tangan pembaruan tidak valid.') from exc
    return value


def download_package(manifest, destination, progress=lambda received: None):
    destination = Path(destination)
    partial = destination.with_suffix('.partial')
    digest, received = hashlib.sha256(), 0
    try:
        with open_release(manifest['url']) as response, partial.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > manifest['size']:
                    raise UpdateError('Ukuran paket pembaruan tidak sesuai.')
                digest.update(chunk)
                output.write(chunk)
                progress(received)
            output.flush()
            import os
            os.fsync(output.fileno())
        if received != manifest['size'] or digest.hexdigest() != manifest['sha256']:
            raise UpdateError('Paket pembaruan tidak utuh.')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def extract_package(archive, destination, manifest):
    archive, destination = Path(archive), Path(destination)
    if destination.exists():
        raise UpdateError('Folder kandidat sudah ada.')
    if archive.stat().st_size != manifest['size']:
        raise UpdateError('Ukuran arsip tidak sesuai.')
    with archive.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != manifest['sha256']:
            raise UpdateError('Hash arsip tidak sesuai.')
    try:
        with zipfile.ZipFile(archive) as zf:
            entries = zf.infolist()
            if len(entries) > 30000 or sum(e.file_size for e in entries) > MAX_EXTRACTED:
                raise UpdateError('Arsip melampaui batas ekstraksi.')
            seen = set()
            for entry in entries:
                name = entry.orig_filename.rstrip('/')
                parts = name.split('/')
                mode = entry.external_attr >> 16
                if (not name or PurePosixPath(name).is_absolute() or '\\' in name
                        or any(p in ('', '.', '..') or p.endswith((' ', '.'))
                               or re.search(r'[\x00-\x1f<>:"|?*]', p)
                               or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?', p)
                               for p in parts)
                        or stat.S_ISLNK(mode)
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or name.casefold() in seen):
                    raise UpdateError('Entri arsip tidak aman.')
                seen.add(name.casefold())
            destination.mkdir(parents=True)
            for entry in entries:
                target = destination.joinpath(*entry.filename.rstrip('/').split('/'))
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(entry) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, UpdateError) as exc:
        if destination.exists():
            shutil.rmtree(destination)
        raise UpdateError('Arsip pembaruan tidak dapat disiapkan.') from exc
