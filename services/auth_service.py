"""Local admin validation and password recovery."""
import re

from extensions import db
from models import User
from services import ValidationError
from services import audit_service as audit

USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]{3,50}$')
MIN_PASSWORD_LENGTH = 10


def _password_error(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        return f'Password minimal {MIN_PASSWORD_LENGTH} karakter.'


def admin_values(username, name, password):
    username, name = username.strip(), name.strip()
    errors = {}
    if not USERNAME_PATTERN.fullmatch(username):
        errors['username'] = ('Username harus 3-50 karakter: huruf, angka, titik, '
                              'garis bawah, atau tanda minus.')
    if not name:
        errors['name'] = 'Nama lengkap tidak boleh kosong.'
    elif len(name) > 100:
        errors['name'] = 'Nama lengkap maksimal 100 karakter.'
    if error := _password_error(password):
        errors['password'] = error
    if errors:
        raise ValidationError(errors)
    return {'username': username, 'name': name}


def recover_admin_password(username, password):
    user = db.session.execute(db.select(User).filter_by(
        username=username.strip(), role='admin', is_active=True)).scalar_one_or_none()
    if user is None:
        raise ValidationError({'username': 'Akun admin aktif tidak ditemukan.'})
    if error := _password_error(password):
        raise ValidationError({'password': error})
    user.set_password(password)
    audit.log_event(audit.ADMIN_PASSWORD_RESET, entity_type='user', entity_id=user.id,
                    description='Password admin dipulihkan melalui CLI.', commit=False)
    db.session.commit()
    return user
