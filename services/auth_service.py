"""Shared validation for local admin creation through CLI and first setup."""
import re

from services import ValidationError

USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]{3,50}$')
MIN_PASSWORD_LENGTH = 10


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
    if len(password) < MIN_PASSWORD_LENGTH:
        errors['password'] = f'Password minimal {MIN_PASSWORD_LENGTH} karakter.'
    if errors:
        raise ValidationError(errors)
    return {'username': username, 'name': name}
