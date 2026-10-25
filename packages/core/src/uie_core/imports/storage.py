"""Stockage des fichiers déposés (V2c.1) : HORS PostgreSQL, comme les secrets.

La base ne garde qu'une référence opaque (`storage_ref`), jamais exposée. Contrat `FileStore` remplaçable
(stockage objet plus tard) ; implémentation disque : `UIE_IMPORT_DIR` (défaut `var/imports`, ignoré par git),
un sous-dossier par organisation.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Protocol

IMPORT_DIR_ENV = "UIE_IMPORT_DIR"
_PREFIX = "file:v1:"
_REL = re.compile(r"^[0-9a-f-]{36}/[0-9a-f-]{36}\.(csv|xlsx)$")


class FileStoreError(Exception):
    """Référence invalide ou fichier absent."""


class FileStore(Protocol):
    def put(self, organization_id: uuid.UUID, file_id: uuid.UUID, extension: str, data: bytes) -> str: ...

    def get(self, ref: str) -> bytes: ...

    def delete(self, ref: str) -> None: ...


class LocalFileStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def put(self, organization_id: uuid.UUID, file_id: uuid.UUID, extension: str, data: bytes) -> str:
        rel = f"{organization_id}/{file_id}.{extension}"
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return _PREFIX + rel

    def get(self, ref: str) -> bytes:
        path = self._path(self._rel(ref))
        if not path.is_file():
            raise FileStoreError("Fichier importé introuvable")
        return path.read_bytes()

    def delete(self, ref: str) -> None:
        self._path(self._rel(ref)).unlink(missing_ok=True)

    def _rel(self, ref: str) -> str:
        if not ref.startswith(_PREFIX):
            raise FileStoreError("Référence de fichier inconnue")
        return ref[len(_PREFIX) :]

    def _path(self, rel: str) -> Path:
        # référence construite par nous seulement : deux UUID et une extension connue (aucun « .. »)
        if not _REL.match(rel):
            raise FileStoreError("Référence de fichier invalide")
        return self.root / rel


def default_file_store() -> FileStore:
    return LocalFileStore(Path(os.environ.get(IMPORT_DIR_ENV) or "var/imports"))
