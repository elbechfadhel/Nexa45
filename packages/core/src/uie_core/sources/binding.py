"""Binding de SOURCE à schéma fixe : DONNÉES qui disent ce que veulent dire les champs d'une ressource.

Symétrique du binding cible (`uie_core.mapping`) : le binding cible traduit un CDM vers un contrat, le binding
de source traduit un contrat vers les semantic keys d'un domaine, sans découverte ni analyse sémantique (le
schéma est publié par l'éditeur et figé dans le Registry). Aucun nom de système ici : le système est une
donnée du binding.

Un champ = une semantic key et UNE règle fermée :
- `path` : valeur au chemin JSON (`a.b`, `a.list[0].c`) ; un objet ou une liste n'est pas une valeur ;
- `first` : première valeur non vide parmi des chemins ou des concaténations `{join: [chemins], sep}` ;
- `present` : vrai si l'objet existe au chemin (ex. un rôle porté par un objet), faux sinon ;
- `map` (facultatif) : traduction FERMÉE d'une valeur de la source vers une valeur canonique du domaine ; une
  valeur hors traduction est rendue `Unmapped` (signalée par le domaine, jamais devinée).
- `label` (facultatif) : la valeur est l'identifiant d'un enregistrement d'une AUTRE ressource dont le
  libellé est lu pour l'affichage (la valeur, elle, reste l'identifiant : c'est lui qui est décidé et stocké).
Une entité peut aussi déclarer une ressource de DÉTAIL (lue par identifiant et fusionnée), le chemin de la
date qui sert au filtre de période, et des LIGNES imbriquées (liste d'objets, champs relatifs à la ligne).
Rien n'est deviné : une valeur absente reste absente. Chaque chemin est contrôlé contre le contrat du
Registry (`technical_name` = chemin sans indice de liste) avant toute lecture."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

_TOKEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$")
_INDEX = re.compile(r"\[\d+\]")


class SourceBindingError(Exception):
    """Binding de source invalide ou incompatible avec le contrat du Registry."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_path(path: str) -> str:
    if not path or not all(_TOKEN.match(t) for t in path.split(".")):
        raise ValueError(f"invalid path: {path!r}")
    return path


def technical_name(path: str) -> str:
    """Nom du champ dans le Registry : chemin sans indice de liste."""
    return _INDEX.sub("", path)


def value_at(data: Any, path: str) -> Any:
    """Valeur au chemin JSON ; None si un segment manque (jamais d'exception)."""
    cur = data
    for token in path.split("."):
        found = _TOKEN.match(token)
        if found is None or not isinstance(cur, dict):
            return None
        cur = cur.get(found.group(1))
        if found.group(2) is not None:
            idx = int(found.group(2))
            cur = cur[idx] if isinstance(cur, list) and idx < len(cur) else None
    return cur


def _scalar(v: Any) -> Any:
    if isinstance(v, dict | list):
        return None
    if isinstance(v, str):
        return v.strip() or None
    return v


class Unmapped(_Model):
    """Valeur de la source absente de la traduction déclarée : non prise en charge, jamais devinée."""

    raw: Any


class LabelSource(_Model):
    """Ressource où lire le LIBELLÉ d'une référence (identifiant → nom), pour l'affichage seulement."""

    resource: str = Field(min_length=1)
    id: str = "id"
    name: str = "name"

    @model_validator(mode="after")
    def _paths(self) -> LabelSource:
        _check_path(self.id)
        _check_path(self.name)
        return self


class Join(_Model):
    join: list[str] = Field(min_length=1)
    sep: str = " "

    @model_validator(mode="after")
    def _paths(self) -> Join:
        for p in self.join:
            _check_path(p)
        return self

    def evaluate(self, data: Any) -> str | None:
        parts = [str(v) for p in self.join if (v := _scalar(value_at(data, p))) is not None]
        return self.sep.join(parts) or None


class SourceField(_Model):
    key: str = Field(min_length=1)
    path: str | None = None
    first: list[str | Join] | None = None
    present: str | None = None
    map: dict[str, str] | None = None  # valeur de la source → valeur canonique du domaine
    label: LabelSource | None = None  # libellé de la référence, lu dans une autre ressource (affichage)

    @model_validator(mode="after")
    def _one_rule(self) -> SourceField:
        rules = [r for r in (self.path, self.first, self.present) if r is not None]
        if len(rules) != 1:
            raise ValueError(f"{self.key}: exactly one of path, first, present")
        for p in self.paths():
            _check_path(p)
        return self

    def paths(self) -> list[str]:
        out = [p for p in (self.path, self.present) if p]
        for alt in self.first or []:
            out += alt.join if isinstance(alt, Join) else [alt]
        return out

    def evaluate(self, data: Any) -> Any:
        v = self._raw(data)
        if self.map is None or v is None:
            return v
        return self.map[str(v)] if str(v) in self.map else Unmapped(raw=v)

    def _raw(self, data: Any) -> Any:
        if self.present is not None:
            return value_at(data, self.present) is not None
        if self.path is not None:
            return _scalar(value_at(data, self.path))
        for alt in self.first or []:
            v = alt.evaluate(data) if isinstance(alt, Join) else _scalar(value_at(data, alt))
            if v is not None:
                return v
        return None


class SourceItems(_Model):
    """Lignes IMBRIQUÉES d'un enregistrement (liste d'objets) : chemins des champs relatifs à la ligne."""

    role: str = Field(min_length=1)  # rôle d'entité du domaine (ex. DOCUMENT_LINE)
    path: str = Field(min_length=1)  # chemin de la liste dans l'enregistrement
    fields: list[SourceField] = Field(min_length=1)

    @model_validator(mode="after")
    def _paths(self) -> SourceItems:
        _check_path(self.path)
        return self

    def paths(self) -> set[str]:
        """Chemins dans l'enregistrement (préfixés par la liste) pour le contrôle du contrat."""
        return {f"{self.path}.{p}" for f in self.fields for p in f.paths()}

    def evaluate(self, data: Any) -> list[dict[str, Any]]:
        rows = value_at(data, self.path)
        return (
            [{f.key: f.evaluate(r) for f in self.fields} for r in rows if isinstance(r, dict)]
            if isinstance(rows, list)
            else []
        )


class SourceEntity(_Model):
    role: str = Field(min_length=1)  # rôle d'entité du domaine (ex. PRIMARY)
    resource: str = Field(min_length=1)  # ressource du contrat (entité du Registry)
    id: str = "id"  # chemin de l'identifiant de la source (opaque)
    archived: str | None = None  # chemin d'un drapeau « archivé » : enregistrement exclu s'il est vrai
    detail: str | None = None  # ressource lue par identifiant pour compléter la liste (champs fusionnés)
    period: str | None = None  # chemin de la date sur laquelle porte un filtre de période
    fields: list[SourceField] = Field(min_length=1)
    items: SourceItems | None = None

    @model_validator(mode="after")
    def _unique(self) -> SourceEntity:
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{self.resource}: duplicate semantic key")
        _check_path(self.id)
        for p in (self.archived, self.period):
            if p:
                _check_path(p)
        return self

    def paths(self) -> set[str]:
        return {
            self.id,
            *([p for p in (self.archived, self.period) if p]),
            *(p for f in self.fields for p in f.paths()),
            *(self.items.paths() if self.items else set()),
        }

    def resources(self) -> list[str]:
        return [self.resource, *([self.detail] if self.detail else [])]

    def evaluate(self, data: dict[str, Any]) -> tuple[str | None, bool, dict[str, Any]]:
        """(identifiant, archivé, valeurs par semantic key) d'un enregistrement brut."""
        rid = _scalar(value_at(data, self.id))
        archived = bool(self.archived and value_at(data, self.archived) is True)
        values = {f.key: f.evaluate(data) for f in self.fields}
        return (str(rid) if rid is not None else None), archived, values


class SourceRef(_Model):
    system: str = Field(min_length=1)
    product: str | None = None
    interface: str = Field(min_length=1)
    versions: list[str] | None = None  # None : toutes les versions du contrat


class SourceBinding(_Model):
    binding_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(min_length=1)
    domain: str = Field(min_length=1)  # domaine du Core (ex. PARTNER)
    source: SourceRef
    entities: list[SourceEntity] = Field(min_length=1)

    @property
    def checksum(self) -> str:
        data = self.model_dump(mode="json", exclude_defaults=True)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def label_sources(self) -> dict[str, LabelSource]:
        """Clé → ressource de son libellé, pour les champs (et lignes) qui en déclarent un."""
        out: dict[str, LabelSource] = {}
        for e in self.entities:
            for f in [*e.fields, *(e.items.fields if e.items else [])]:
                if f.label is not None:
                    out[f.key] = f.label
        return out

    def entity(self, role: str) -> SourceEntity | None:
        return next((e for e in self.entities if e.role == role), None)

    def check_contract(self, contract: dict[str, set[str]]) -> None:
        """Chaque ressource et chaque chemin existent dans le contrat (ressource → noms de champs)."""
        for e in self.entities:
            names: set[str] = set()
            for res in e.resources():  # liste + détail : leurs champs sont fusionnés
                if res not in contract:
                    raise SourceBindingError(f"{self.name}: unknown resource {res!r} in the contract")
                names |= contract[res]
            missing = sorted({technical_name(p) for p in e.paths()} - names)
            if missing:
                raise SourceBindingError(f"{self.name}: unknown field(s) in {e.resource!r}: {missing}")
        for ls in self.label_sources().values():
            if ls.resource not in contract:
                raise SourceBindingError(f"{self.name}: unknown resource {ls.resource!r} in the contract")
            missing = sorted({technical_name(ls.id), technical_name(ls.name)} - contract[ls.resource])
            if missing:
                raise SourceBindingError(f"{self.name}: unknown field(s) in {ls.resource!r}: {missing}")


class SourceBindingRegistry:
    """Bindings de source découverts (`<domaine>/sources/*.yaml`), par (domaine, système, interface)."""

    _bindings: ClassVar[dict[tuple[str, str, str], SourceBinding]] = {}

    @classmethod
    def register(cls, binding: SourceBinding) -> None:
        cls._bindings[(binding.domain, binding.source.system, binding.source.interface)] = binding

    @classmethod
    def find(cls, domain: str, system: str, version: str, interface: str) -> SourceBinding | None:
        b = cls._bindings.get((domain, system, interface))
        if b is None or (b.source.versions is not None and version not in b.source.versions):
            return None
        return b

    @classmethod
    def domains_for(cls, system: str) -> list[str]:
        return sorted({d for (d, s, _) in cls._bindings if s == system})
