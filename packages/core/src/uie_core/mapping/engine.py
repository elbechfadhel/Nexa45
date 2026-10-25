"""Moteur de mapping générique : vue CDM → enregistrement d'un format cible, validé par le Registry.

1. Résolution (`resolve_binding`) : le binding est confronté au snapshot du format dans le Registry ; toute
position citée doit y exister (sinon `TargetBindingError`), et chaque colonne apporte libellé, type,
longueur, décimales, obligatoire et motif. 2. Application (`map_record`) : sources de la vue, `when`,
`fallback`, `extract`, `map`, emplacements répétés, règles déclarées. 3. Validation de chaque valeur par les
MÉTADONNÉES du Registry : longueur, type numérique, encodage du fichier, motif vérifié sur la forme
sérialisée par l'écrivain du format. Politique : jamais de troncature ; une valeur invalide d'un champ
d'identité ou obligatoire BLOQUE l'enregistrement, une valeur facultative invalide est omise avec un
AVERTISSEMENT.

Aucun nom de système, de format ni de colonne dans ce module.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict

from uie_core.mapping.binding import (
    FieldMapping,
    ParamType,
    RuleKind,
    TargetBinding,
    TargetBindingError,
)
from uie_core.mapping.writer import ValueFormat
from uie_core.registry import FieldDef, SnapshotRef
from uie_core.registry.service import NotFound, RegistryService


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IssueLevel(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


class RecordStatus(StrEnum):
    READY = "READY"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


class TargetIssue(_Model):
    level: IssueLevel
    field: str | None = None  # position du format
    label: str | None = None  # libellé du Registry
    message: str
    rule_id: str | None = None


class TargetRecord(_Model):
    source_ref: str
    role: str | None = None
    values: dict[str, Any]  # position → valeur (avant sérialisation)
    issues: list[TargetIssue] = []

    @property
    def status(self) -> RecordStatus:
        levels = {i.level for i in self.issues}
        if IssueLevel.BLOCKING in levels:
            return RecordStatus.BLOCKED
        return RecordStatus.WARNING if levels else RecordStatus.READY


# --------------------------------------------------------------------------- #
# Résolution contre le Registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ResolvedBinding:
    binding: TargetBinding
    snapshot_id: uuid.UUID
    snapshot_label: str
    format_version: str
    metadata: dict[str, Any]
    fields: list[FieldDef]  # colonnes de l'enregistrement, dans l'ordre du format
    header_fields: list[FieldDef]

    def field(self, name: str) -> FieldDef:
        """Colonne de l'enregistrement (l'en-tête a les mêmes numéros de position : jamais mélangés)."""
        return _index(self.fields)[name]

    def header_field(self, name: str) -> FieldDef:
        return _index(self.header_fields)[name]


def _index(fields: list[FieldDef]) -> dict[str, FieldDef]:
    return {f.technical_name: f for f in fields}


def _ordered(fields: list[FieldDef]) -> list[FieldDef]:
    return sorted(fields, key=lambda f: (f.metadata.get("position") or 0, f.technical_name))


def resolve_binding(
    registry: RegistryService, binding: TargetBinding, version: str | None = None
) -> ResolvedBinding:
    """Version demandée, sinon la plus récente des versions du binding présente dans le Registry."""
    t = binding.target
    candidates = [version] if version else sorted(t.versions, key=_version_key, reverse=True)
    for v in candidates:
        if v not in t.versions:
            raise TargetBindingError(f"Version {v} non prise en charge par le binding {binding.name}")
        try:
            snap = registry.resolve_snapshot(
                SnapshotRef(system=t.system, product=t.product, version=v, interface=t.interface)
            )
        except NotFound:
            continue
        names = [t.entity, *([t.header_entity] if t.header_entity else [])]
        model = registry.load_snapshot_model(snap, entities=names)
        entity = model.entity(t.entity)
        if entity is None:
            raise TargetBindingError(f"Entité {t.entity} absente de {registry.snapshot_label(snap)}")
        header = model.entity(t.header_entity) if t.header_entity else None
        if t.header_entity and header is None:
            raise TargetBindingError(f"Entité {t.header_entity} absente de {registry.snapshot_label(snap)}")
        resolved = ResolvedBinding(
            binding=binding,
            snapshot_id=snap.id,
            snapshot_label=registry.snapshot_label(snap),
            format_version=v,
            metadata=dict(snap.metadata_ or {}),
            fields=_ordered(list(entity.fields)),
            header_fields=_ordered(list(header.fields)) if header else [],
        )
        _check(resolved)
        return resolved
    raise TargetBindingError(
        f"Aucune version de {t.system}/{t.product} ({', '.join(t.versions)}) dans le Registry"
    )


def _version_key(v: str) -> tuple[int, str]:
    return (int(v), v) if v.isdigit() else (0, v)


def _check(r: ResolvedBinding) -> None:
    known = {f.technical_name for f in r.fields}
    unknown = sorted(r.binding.positions() - known)
    if unknown:
        raise TargetBindingError(f"Positions inconnues du Registry ({r.snapshot_label}) : {unknown}")
    header_known = {f.technical_name for f in r.header_fields}
    header_used = set(r.binding.header) | {p.header_field for p in r.binding.profile if p.header_field}
    unknown = sorted(header_used - header_known)
    if unknown:
        raise TargetBindingError(
            f"Positions d'en-tête inconnues du Registry ({r.snapshot_label}) : {unknown}"
        )


# --------------------------------------------------------------------------- #
# Validation par les métadonnées du Registry
# --------------------------------------------------------------------------- #
# Dialectes de motif déclarés par le Registry (métadonnée `pattern_syntax` d'un champ) : « ascii » = classes
# \w, \d, \s limitées à l'ASCII (notation de certains formats) ; absent = expressions Python par défaut.
_SYNTAX_FLAGS = {"ascii": re.ASCII}


@lru_cache(maxsize=1024)
def _pattern(expr: str, syntax: str | None = None) -> re.Pattern[str] | None:
    try:
        return re.compile(expr, _SYNTAX_FLAGS.get(syntax or "", 0))
    except re.error:
        return None  # motif non exprimable en Python (ex. classe vide) : pas de contrôle de motif


def check_value(fd: FieldDef, value: Any, writer: ValueFormat, encoding: str) -> str | None:
    """Raison (phrase simple) pour laquelle la valeur ne peut pas être écrite ici, sinon None."""
    meta = fd.metadata
    text = value if isinstance(value, str) else None
    if fd.data_type.value in ("integer", "decimal") and isinstance(value, str):
        if not re.fullmatch(r"-?\d+([.,]\d+)?", value):
            return "is not a number"
    decimals = meta.get("decimals")
    if isinstance(value, Decimal) and isinstance(decimals, int) and value.as_tuple().exponent < -decimals:
        # jamais d'arrondi silencieux : l'écrivain arrondirait à `decimals`
        if value != value.quantize(Decimal(1).scaleb(-decimals)):
            return f"has more than {decimals} decimals"
    length = meta.get("length")
    if text is not None and isinstance(length, int) and length > 0 and len(text) > length:
        return f"is longer than {length} characters"
    if text is not None and not writer.encodable(text, encoding):
        return f"contains characters that cannot be written in {encoding}"
    allowed = meta.get("enum")
    if isinstance(allowed, list) and allowed and writer.format_cell(fd, value) not in allowed:
        return "is not one of the values accepted by the target"
    expr = meta.get("pattern")
    syntax = meta.get("pattern_syntax")
    compiled = _pattern(expr, syntax if isinstance(syntax, str) else None) if isinstance(expr, str) else None
    if compiled is not None and not compiled.match(writer.format_cell(fd, value)):
        return "does not have the format expected by the target"
    return None


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
def _key(v: Any) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def map_record(
    view: dict[str, Any],
    resolved: ResolvedBinding,
    writer: ValueFormat,
    encoding: str,
    *,
    source_ref: str,
    target_values: dict[str, Any] | None = None,
) -> TargetRecord:
    """Un enregistrement cible à partir de la vue CDM (`target_values` : valeurs fournies par l'appelant, ex.
    un numéro attribué, lues sous `target.<nom>`)."""
    b = resolved.binding
    view = view | {f"target.{k}": v for k, v in (target_values or {}).items() if v is not None}
    values: dict[str, Any] = {}
    issues: list[TargetIssue] = []

    def put(pos: str, value: Any, identity: bool) -> None:
        fd = resolved.field(pos)
        reason = check_value(fd, value, writer, encoding)
        if reason is None:
            values[pos] = value
            return
        blocking = identity or fd.required
        issues.append(
            TargetIssue(
                level=IssueLevel.BLOCKING if blocking else IssueLevel.WARNING,
                field=pos,
                label=fd.label,
                message=f"{fd.label} {reason}" + ("" if blocking else ": left out"),
            )
        )

    for fm in b.fields:
        if fm.when is not None and not fm.when.matches(view):
            continue
        value = _source(fm, view)
        if value is None:
            continue
        value, problem = _transform(fm, value)
        if problem:
            fd = resolved.field(fm.field)
            level = IssueLevel.BLOCKING if fm.identity else IssueLevel.WARNING
            issues.append(
                TargetIssue(level=level, field=fm.field, label=fd.label, message=f"{fd.label} {problem}")
            )
            continue
        put(fm.field, value, fm.identity)

    for rep in b.repeat:
        items = view.get(rep.source) or []
        if rep.require:
            kept = [i for i in items if i.get(rep.require) not in (None, "")]
            if len(kept) < len(items):
                missing = len(items) - len(kept)
                text = f"{missing} {rep.label} not written: {rep.require_label or rep.require} missing"
                issues.append(TargetIssue(level=IssueLevel.WARNING, message=text))
            items = kept
        if len(items) > len(rep.slots):
            issues.append(
                TargetIssue(
                    level=IssueLevel.WARNING,
                    message=f"Only the first {len(rep.slots)} of {len(items)} {rep.label} are written",
                )
            )
        for item, slot in zip(items, rep.slots, strict=False):
            for attr, pos in slot.items():
                v = item.get(attr)
                if v is None:
                    continue
                table = rep.map.get(attr)
                if table is not None:
                    v = table.get(_key(v))
                    if v is None:
                        continue
                put(pos, v, False)

    for fd in resolved.fields:
        if fd.required and fd.technical_name not in values:
            if not any(i.field == fd.technical_name for i in issues):
                issues.append(
                    TargetIssue(
                        level=IssueLevel.BLOCKING,
                        field=fd.technical_name,
                        label=fd.label,
                        message=f"{fd.label} is required and has no value",
                    )
                )

    for om in b.omitted:
        if om.when is not None and not om.when.matches(view):
            continue
        if view.get(om.source) not in (None, "", []):
            reason = om.reason or f"cannot be sent to {b.target.display_name}: left out"
            issues.append(TargetIssue(level=IssueLevel.WARNING, message=f"{om.label} {reason}"))

    for rule in b.rules:
        if rule.when is not None and not rule.when.matches(view):
            continue
        if rule.kind == RuleKind.REQUIRED_UNLESS and rule.field not in values:
            if rule.source is None or _key(view.get(rule.source)) != rule.equals:
                issues.append(
                    TargetIssue(
                        level=IssueLevel(rule.level),
                        field=rule.field,
                        label=resolved.field(rule.field).label,
                        message=rule.message,
                        rule_id=rule.id,
                    )
                )
    # Obligation CONDITIONNELLE déclarée par le Registry (`required_group`) : le champ est obligatoire dès
    # qu'une autre valeur du même groupe est transmise (ex. le pays d'une adresse). Après les règles du
    # binding, pour ne pas doubler un problème déjà signalé sur ce champ.
    for fd in resolved.fields:
        group = fd.metadata.get("required_group")
        if not isinstance(group, str) or fd.technical_name in values:
            continue
        if any(i.field == fd.technical_name and i.level == IssueLevel.BLOCKING for i in issues):
            continue  # déjà bloquant (règle du binding) ; un simple « écarté » ne suffit pas
        if any(name.startswith(group + ".") for name in values):
            issues.append(
                TargetIssue(
                    level=IssueLevel.BLOCKING,
                    field=fd.technical_name,
                    label=fd.label,
                    message=f"{fd.label} is required and has no value",
                )
            )
    return TargetRecord(source_ref=source_ref, role=view.get("role"), values=values, issues=issues)


def _source(fm: FieldMapping, view: dict[str, Any]) -> Any:
    if fm.constant is not None:
        return fm.constant
    value = view.get(fm.source or "")
    if (value is None or value == "") and fm.fallback:
        value = view.get(fm.fallback)
    return None if value == "" else value


def _transform(fm: FieldMapping, value: Any) -> tuple[Any, str | None]:
    if fm.date_format is not None:
        if not isinstance(value, date):
            return None, "is not a date"
        value = value.strftime(fm.date_format)
    if fm.extract is not None:
        found = re.match(fm.extract.regex, str(value))
        if not found:
            return None, "does not have the expected form"
        value = found.group(fm.extract.group)
    if fm.map is not None:
        mapped = fm.map.get(_key(value))
        if mapped is None:
            return None, f"value « {value} » is not supported by the target"
        value = mapped
    return value, None


# --------------------------------------------------------------------------- #
# En-tête et profil
# --------------------------------------------------------------------------- #
def header_values(
    resolved: ResolvedBinding,
    profile: dict[str, Any],
    now: datetime | None,
    *,
    context: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Valeurs de la ligne d'en-tête (positions non déclarées : vides). `now` None : les valeurs générées
    (horodatage) sont omises, pour comparer deux contenus hors métadonnées volatiles. `context` : valeurs
    de l'export (ex. bornes de la période). `params` : spécifications des paramètres hérités d'un autre
    profil."""
    specs = dict(params or {}) | {p.name: p for p in resolved.binding.profile}
    ctx = context or {}
    out: dict[str, Any] = {}
    for pos, hv in resolved.binding.header.items():
        if hv.constant is not None:
            out[pos] = hv.constant
        elif hv.snapshot == "format_version":
            out[pos] = resolved.format_version
        elif hv.snapshot is not None:
            out[pos] = resolved.metadata.get(hv.snapshot)
        elif hv.generated == "timestamp_ms":
            if now is not None:
                out[pos] = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
        elif hv.profile is not None:
            out[pos] = profile_header_value(specs[hv.profile], profile.get(hv.profile))
        elif hv.context is not None:
            value = ctx.get(hv.context)
            out[pos] = value.strftime(hv.date_format or "%Y%m%d") if isinstance(value, date) else value
        elif hv.template is not None:
            out[pos] = hv.template.format(**ctx)
    return {k: v for k, v in out.items() if v not in (None, "")}


def profile_header_value(param: Any, value: Any) -> Any:
    if value is None or value == "":
        return None
    if param.type == ParamType.DATE:
        d = value if isinstance(value, date) else date.fromisoformat(str(value))
        return d.strftime(param.header_format or "%Y%m%d")
    return str(value)
