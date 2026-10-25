"""Catalogue des systèmes CONNUS (identifiés, sans schéma) importé dans le Registry existant.

Donnée (`known_systems.yaml`), validée ici ; écrite par `RegistryService.register_known_system` (statut
`known` : ni produit, ni version, ni snapshot). Aucun second Registry : le Wizard et la console lisent la même
table `systems`. Rien n'est supposé vérifié : un schéma n'existe que par un loader et un snapshot publié."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from uie_core.identity import Context
from uie_core.registry import RegistryService

CATALOG = Path(__file__).with_name("known_systems.yaml")


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KnownSystem(_Spec):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=50)
    name: str = Field(max_length=200)
    vendor: str | None = Field(default=None, max_length=200)
    category: Literal["erp", "accounting", "ecommerce", "crm", "hr"]
    description: str = Field(max_length=160)


class KnownSystemCatalog(_Spec):
    catalog_version: str
    systems: list[KnownSystem] = Field(min_length=1)


def load_catalog(path: Path | None = None) -> KnownSystemCatalog:
    data = yaml.safe_load((path or CATALOG).read_text(encoding="utf-8"))
    catalog = KnownSystemCatalog.model_validate(data)
    codes = [s.code for s in catalog.systems]
    if len(codes) != len(set(codes)):
        raise ValueError("Code de système en double dans le catalogue")
    return catalog


def import_known_systems(session: Session, ctx: Context, path: Path | None = None) -> int:
    """Importe (idempotent) les systèmes connus ; renvoie leur nombre. Un système qui a déjà un schéma publié
    n'est pas modifié."""
    registry = RegistryService(session, ctx)
    catalog = load_catalog(path)
    for s in catalog.systems:
        registry.register_known_system(
            s.code, s.name, category=s.category, vendor=s.vendor, description=s.description
        )
    return len(catalog.systems)
