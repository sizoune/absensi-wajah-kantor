"""
Status kios yang dibagikan dari layanan kamera ke server web.

Layanan kamera menulis file JSON kecil (atomik) setiap setengah detik.
Server web hanya MEMBACA file ini, sehingga kamera tetap dipegang satu proses
saja (FR-04).

Isi file sengaja minimal: tidak ada gambar, embedding, NIP, atau skor.
Pesan untuk karyawan hanya memuat nama depan (PRD 10.3).

Catatan Windows: Windows menolak mengganti file yang sedang dibuka proses
lain (PermissionError / WinError 5). Karena server web membaca file ini setiap
detik, penulisan dan pembacaan dicoba ulang beberapa kali. Jika tetap gagal,
satu pembaruan dilewati; status bukan data penting dan segera diperbarui lagi.
"""
import json
import os
import time
from pathlib import Path

STALE_SECONDS = 5.0       # layanan dianggap mati jika tidak memperbarui status
MESSAGE_SECONDS = 6.0     # lama pesan tampil di kios

RETRIES = 5
RETRY_DELAY = 0.02        # detik

CAMERA_OK = "ok"
CAMERA_ERROR = "error"
CAMERA_STARTING = "starting"


EOFFICE_MESSAGES = {
    'pending': ('info', 'Menunggu sinkronisasi'),
    'syncing': ('info', 'Memperbarui jadwal. Tetap di depan kamera'),
    'check_in': ('success', 'Berhasil masuk'),
    'check_out': ('success', 'Berhasil pulang'),
    'duplicate': ('info', 'Sudah absen'),
    'outside_schedule': ('warning', 'Di luar jadwal'),
    'uncertain': ('warning', 'Identitas belum dipastikan'),
    'rejected': ('warning', 'Presensi ditolak server. Hubungi operator'),
    'storage': ('error', 'Penyimpanan bermasalah'),
    'operator': ('error', 'Hubungi operator'),
}

EOFFICE_TEST_MESSAGES = {key: EOFFICE_MESSAGES[key] for key in ('pending', 'syncing', 'uncertain', 'rejected', 'storage', 'operator')}
EOFFICE_TEST_MESSAGES['test_recorded'] = ('info', 'Uji tersimpan')


def integration_view(value: object) -> dict[str, str]:
    """Allowlist global health independently of expiring person messages."""
    value = value if isinstance(value, dict) else {}
    labels = {
        'connection': {'online': 'E-office terhubung', 'offline': 'E-office terputus', 'error': 'Koneksi e-office bermasalah'},
        'storage': {'ok': 'Penyimpanan siap', 'error': 'Penyimpanan bermasalah'},
        'auth': {'ok': 'Akses perangkat aktif', 'paused': 'Akses perangkat tertahan'},
        'clock': {'ok': 'Waktu capture siap', 'unavailable': 'Waktu capture belum tersedia'},
    }
    defaults = {'connection': 'error', 'storage': 'error', 'auth': 'paused', 'clock': 'unavailable'}
    health = {key: value.get(key) if isinstance(value.get(key), str) and value.get(key) in options else defaults[key]
              for key, options in labels.items()}
    if health['storage'] == 'error':
        action = 'Hubungi operator untuk memeriksa penyimpanan; jangan hapus antrean.'
    elif health['auth'] == 'paused':
        action = 'Hubungi operator untuk memulihkan akses perangkat.'
    elif health['clock'] == 'unavailable':
        action = 'Hubungi operator untuk memeriksa waktu dan sinkronisasi e-office.'
    elif health['connection'] == 'error':
        action = 'Hubungi operator untuk memeriksa layanan e-office.'
    elif health['connection'] == 'offline':
        action = 'Periksa koneksi jaringan; antrean tersimpan akan dicoba kembali.'
    else:
        action = ''
    return {**health, 'text': ' · '.join(labels[key][health[key]] for key in labels), 'action': action}


class KioskState:
    def __init__(self, path, sleep=time.sleep, replace=os.replace):
        self.path = Path(path)
        self._sleep = sleep
        self._replace = replace

    def publish(self, data, clock=time.time):
        """Menulis status. Mengembalikan True jika berhasil, False jika dilewati."""
        data = dict(data, heartbeat=clock())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

        for attempt in range(RETRIES):
            try:
                self._replace(tmp, self.path)
                return True
            except PermissionError:
                if attempt < RETRIES - 1:
                    self._sleep(RETRY_DELAY)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False

    def read(self):
        for attempt in range(RETRIES):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    return json.load(fh)
            except FileNotFoundError:
                return None
            except (PermissionError, ValueError):
                # Sedang diganti oleh layanan kamera; coba lagi sebentar.
                if attempt < RETRIES - 1:
                    self._sleep(RETRY_DELAY)
            except OSError:
                return None
        return None


def public_view(state, now=None, stale_seconds=STALE_SECONDS, message_seconds=MESSAGE_SECONDS, *, test_mode=None):
    """Ringkasan aman untuk halaman kios (tanpa data internal)."""
    now = time.time() if now is None else now
    if (not state or now - float(state.get("heartbeat", 0)) > stale_seconds
            or test_mode is not None and (state.get('purpose') == 'camera_test') != test_mode):
        return {"online": False, "camera": "offline", "message": None, "hint": None}

    message = state.get("message")
    if message and now - float(message.get("at", 0)) > message_seconds:
        message = None
    if state.get('mode') == 'eoffice':
        test_mode = state.get('purpose') == 'camera_test'
        messages = EOFFICE_TEST_MESSAGES if test_mode else EOFFICE_MESSAGES
        public_message = None
        if message:
            kind, text = messages.get(message.get('status'), messages['operator'])
            first = str(message.get('first_name') or '').split()
            public_message = {'kind': kind, 'text': (first[0] + ': ' if first else '') + text,
                              'id': message.get('id')}
        return {'online': True, 'camera': state.get('camera', CAMERA_STARTING),
                **({'purpose': 'camera_test'} if test_mode else {}),
                'message': public_message, 'hint': None, 'integration': integration_view(state.get('integration'))}
    return {
        "online": True,
        "camera": state.get("camera", CAMERA_STARTING),
        "message": {"kind": message["kind"], "text": message["text"]} if message else None,
        "hint": state.get("hint"),
    }


def admin_view(state, now=None, stale_seconds=STALE_SECONDS):
    """Ringkasan untuk dashboard admin."""
    now = time.time() if now is None else now
    if not state:
        return {"online": False, "detail": "Layanan kamera belum pernah dijalankan."}
    age = now - float(state.get("heartbeat", 0))
    if age > stale_seconds:
        return {"online": False, "detail": f"Tidak ada kabar dari layanan kamera selama {int(age)} detik."}
    return {
        "online": True,
        "camera": state.get("camera"),
        "detail": state.get("detail") or "",
        "employees": state.get("employees", 0),
        **({"recorded_this_run": state.get("recorded", 0),
            "recorded_scope": "Uji sejak layanan dimulai" if state.get('purpose') == 'camera_test' else "Sejak layanan dimulai"}
           if state.get("mode") == "eoffice" else {"recorded_today": state.get("recorded", 0)}),
        "liveness": state.get("liveness") or "off",
        **({"integration": integration_view(state.get("integration"))} if state.get("mode") == "eoffice" else {}),
    }
