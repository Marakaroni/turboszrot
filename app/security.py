from __future__ import annotations

import time
import pyotp

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash

from app.models import LoginSecurity


PASSWORD_HASHER = PasswordHasher()
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 10 * 60
LOGIN_LOCK_SECONDS = 15 * 60


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        try:
            return check_password_hash(password_hash, password)
        except ValueError:
            return False


def password_needs_argon2_rehash(password_hash: str) -> bool:
    if not password_hash.startswith("$argon2"):
        return True

    return PASSWORD_HASHER.check_needs_rehash(password_hash)


def validate_strong_password(password: str) -> list[str]:
    errors = []

    if len(password) < 8:
        errors.append("Haslo musi miec minimum 8 znakow.")
    if not any(character.islower() for character in password):
        errors.append("Haslo musi zawierac mala litere.")
    if not any(character.isupper() for character in password):
        errors.append("Haslo musi zawierac wielka litere.")
    if not any(character.isdigit() for character in password):
        errors.append("Haslo musi zawierac cyfre.")
    if not any(not character.isalnum() for character in password):
        errors.append("Haslo musi zawierac znak specjalny.")

    return errors


def get_login_attempt_key(username: str) -> str:
    return username.lower()


def is_login_blocked(engine, login_key: str) -> bool:
    with Session(engine) as session:
        login_security = session.get(LoginSecurity, login_key)
        if login_security is None:
            return False

        if login_security.locked_until <= 0:
            return False

        if time.time() >= login_security.locked_until:
            session.delete(login_security)
            session.commit()
            return False

        return True


def record_failed_login(engine, login_key: str) -> None:
    now = time.time()
    with Session(engine) as session:
        login_security = session.get(LoginSecurity, login_key)

        if login_security is None:
            login_security = LoginSecurity(
                login_key=login_key,
                failed_attempts=1,
                first_failed_at=now,
                locked_until=0,
            )
            session.add(login_security)
            session.commit()
            return

        if now - login_security.first_failed_at > LOGIN_ATTEMPT_WINDOW_SECONDS:
            login_security.failed_attempts = 1
            login_security.first_failed_at = now
            login_security.locked_until = 0
        else:
            login_security.failed_attempts += 1

        if login_security.failed_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
            login_security.failed_attempts = 0
            login_security.first_failed_at = 0
            login_security.locked_until = now + LOGIN_LOCK_SECONDS

        session.commit()


def clear_failed_logins(engine, login_key: str) -> None:
    with Session(engine) as session:
        login_security = session.get(LoginSecurity, login_key)
        if login_security is None:
            return

        session.delete(login_security)
        session.commit()

def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(username: str, secret: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name="Turbo Szrot",
    )


def verify_totp_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False

    cleaned_code = code.strip().replace(" ", "")

    if not cleaned_code.isdigit():
        return False

    try:
        return pyotp.TOTP(secret).verify(cleaned_code, valid_window=1)
    except Exception:
        return False


import os
import base64
from cryptography.fernet import Fernet

_ENCRYPTION_KEY_RAW = os.environ["ENCRYPTION_KEY"]  # ustaw w pliku .env (patrz .env.example)

_key_bytes = _ENCRYPTION_KEY_RAW.encode("utf-8") if isinstance(_ENCRYPTION_KEY_RAW, str) else _ENCRYPTION_KEY_RAW
try:
    if len(base64.urlsafe_b64decode(_key_bytes)) != 32:
        raise ValueError
    _FERNET_KEY = _key_bytes
except Exception:
    _padded = (_key_bytes * 32)[:32]
    _FERNET_KEY = base64.urlsafe_b64encode(_padded)

_fernet = Fernet(_FERNET_KEY)

def encrypt_totp_secret(raw_secret: str) -> str:
    if not raw_secret:
        return raw_secret
    return _fernet.encrypt(raw_secret.encode()).decode()

def decrypt_totp_secret(encrypted_secret: str) -> str:
    if not encrypted_secret:
        return encrypted_secret
    try:
        return _fernet.decrypt(encrypted_secret.encode()).decode()
    except Exception:
        return encrypted_secret
