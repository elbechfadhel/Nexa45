"""Mots de passe : argon2id (argon2-cffi). Jamais stockés ni journalisés en clair."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import SecretStr

_HASHER = PasswordHasher()
MIN_PASSWORD_LENGTH = 12
# hash d'un mot de passe jetable : vérifié quand l'utilisateur n'existe pas, pour un temps de réponse
# comparable (ne pas révéler par le temps qu'un email est inconnu)
_DUMMY_HASH = _HASHER.hash("uie-dummy-password-for-timing")


class PasswordError(ValueError):
    pass


def _plain(password: SecretStr | str) -> str:
    return password.get_secret_value() if isinstance(password, SecretStr) else password


def hash_password(password: SecretStr | str) -> str:
    plain = _plain(password)
    if len(plain) < MIN_PASSWORD_LENGTH:
        raise PasswordError(f"Mot de passe trop court (au moins {MIN_PASSWORD_LENGTH} caractères)")
    return _HASHER.hash(plain)


def verify_password(password_hash: str | None, password: SecretStr | str) -> bool:
    """True si le mot de passe correspond ; sans hash (utilisateur inconnu), vérifie un hash factice."""
    try:
        return _HASHER.verify(password_hash or _DUMMY_HASH, _plain(password)) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _HASHER.check_needs_rehash(password_hash)


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if "@" not in value or value.startswith("@") or value.endswith("@") or len(value) > 320:
        raise PasswordError(f"Adresse email invalide : {email!r}")
    return value
