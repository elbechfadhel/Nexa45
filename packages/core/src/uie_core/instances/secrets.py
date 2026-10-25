"""Stockage des secrets de connexion.

La base ne contient qu'un `secret_ref` opaque. Le contrat `SecretStore` permet de remplacer
l'implémentation (Vault, AWS Secrets Manager, Azure Key Vault) sans toucher au Core :
seul le préfixe du `secret_ref` change (`fernet:`, `vault:`, ...).

Règles : un secret n'est jamais journalisé, jamais renvoyé par l'API, jamais inclus dans un snapshot
ni dans un message d'erreur persisté (`redact`).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Iterable

from pydantic import SecretStr

SECRET_KEY_ENV = "UIE_SECRET_KEY"
REDACTED = "***"


class SecretError(Exception):
    """Erreur de stockage / lecture de secret. Le message ne contient jamais le secret."""


class SecretStore(ABC):
    """Contrat : put → secret_ref (opaque, seul élément persisté) ; get(secret_ref) → secret."""

    scheme: str

    @abstractmethod
    def put(self, secret: SecretStr) -> str: ...

    @abstractmethod
    def get(self, secret_ref: str) -> SecretStr: ...

    def delete(self, secret_ref: str) -> None:  # noqa: B027 — optionnel selon le backend
        """Supprime le secret si le backend le stocke hors du ref (no-op par défaut)."""

    def handles(self, secret_ref: str) -> bool:
        return secret_ref.startswith(f"{self.scheme}:")


class FernetSecretStore(SecretStore):
    """Chiffrement symétrique Fernet (AES-128-CBC + HMAC-SHA256), clé `UIE_SECRET_KEY`.

    Le `secret_ref` est `fernet:v1:<token>` : la base ne contient que le chiffré, jamais le clair ;
    sans la clé (hors base), le ref est inexploitable.
    """

    scheme = "fernet"
    _prefix = "fernet:v1:"

    def __init__(self, key: str | bytes) -> None:
        from cryptography.fernet import Fernet

        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise SecretError(f"{SECRET_KEY_ENV} invalide (clé Fernet urlsafe-base64 de 32 octets)") from exc

    @classmethod
    def from_env(cls) -> FernetSecretStore:
        key = os.environ.get(SECRET_KEY_ENV)
        if not key:
            raise SecretError(
                f"{SECRET_KEY_ENV} absente : générer une clé avec `uv run uie secret gen-key` "
                "et l'ajouter à .env"
            )
        return cls(key)

    @staticmethod
    def generate_key() -> str:
        from cryptography.fernet import Fernet

        return Fernet.generate_key().decode()

    def put(self, secret: SecretStr) -> str:
        return self._prefix + self._fernet.encrypt(secret.get_secret_value().encode()).decode()

    def delete(self, secret_ref: str) -> None:
        """Le chiffré vit dans le ref lui-même : il disparaît avec la ligne instance_connections.
        (Un backend externe — Vault, AWS SM, Azure KV — supprimerait ici l'entrée distante.)"""
        if not secret_ref.startswith(self._prefix):
            raise SecretError("secret_ref non géré par FernetSecretStore")

    def get(self, secret_ref: str) -> SecretStr:
        from cryptography.fernet import InvalidToken

        if not secret_ref.startswith(self._prefix):
            raise SecretError("secret_ref non géré par FernetSecretStore")
        try:
            return SecretStr(self._fernet.decrypt(secret_ref[len(self._prefix) :].encode()).decode())
        except InvalidToken:
            raise SecretError(f"Secret illisible : mauvaise {SECRET_KEY_ENV} ou ref altéré") from None


def default_secret_store() -> SecretStore:
    return FernetSecretStore.from_env()


def redact(text: str | None, secrets: Iterable[SecretStr | str | None]) -> str | None:
    """Masque toute occurrence d'un secret dans un texte (message d'erreur, log, détail)."""
    if text is None:
        return None
    for s in secrets:
        value = s.get_secret_value() if isinstance(s, SecretStr) else s
        if value:
            text = text.replace(value, REDACTED)
    return text
