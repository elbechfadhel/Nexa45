"""Assistant de migration (AI Migration Wizard) : une question à la fois, puis la structure cible et un Excel.

    réponses ──► faits du Registry ──► LLM (question suivante | structure cible) ──► validation ──►
        confrontation au Registry (vérifié / proposition IA) ──► étape ou proposition ──► fichier Excel

Registry d'abord : les systèmes et versions CONNUS sont proposés comme choix vérifiés (ceux de l'IA qui les
doublonnent sont retirés) ; la structure proposée est confrontée au snapshot du Registry quand la cible et sa
version y sont, champ par champ. L'IA ne complète que ce que le Registry ne sait pas, et ce qu'elle propose
reste marqué comme tel : jamais présenté comme un format officiellement compatible.

Sans état côté serveur : le client renvoie ses réponses à chaque étape. Appels limités : une réponse déjà
obtenue pour les mêmes réponses est reprise du cache du processus (retour arrière, rechargement de la
page)."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from uie_core.db.base import utcnow
from uie_core.domains.pack import DomainPack, DomainPackError, DomainPackRegistry, ImportDefault
from uie_core.identity.context import Context, TenantContext, ensure_context
from uie_core.imports import ColumnProposalBatch, mask_example, read_table
from uie_core.imports.matching import MATCHED_THRESHOLD as AI_MATCH_RULE
from uie_core.imports.matching import header_key, rule_key
from uie_core.imports.records import LearnedColumnMapping
from uie_core.imports.service import NOT_RELEVANT
from uie_core.registry import SnapshotRef
from uie_core.registry.service import KNOWN_STATUS, NotFound, RegistryService
from uie_core.wizard.format_flow import FormatFlow
from uie_core.wizard.formats import FormatTarget, format_target
from uie_core.wizard.formats import structure as format_structure
from uie_core.wizard.lookup import MAX_CHECK, InstanceTarget, ReferenceFinder, ReferenceOption
from uie_core.wizard.mapping import (
    ColumnHints,
    ColumnStatus,
    FileMapping,
    MappingDecision,
    PreparedFile,
    PreviousChoice,
    RecordIdentity,
    RecordSheet,
    UnitSettings,
    WizardColumnProposer,
    WizardColumnsRequest,
    map_columns,
    prepare,
    prepared_filename,
    records_key,
    render_prepared,
)
from uie_core.wizard.missing import (
    MAX_PER_ROW,
    FileColumn,
    Fill,
    FillError,
    MissingInfo,
    MissingKind,
    MissingQuestion,
    RowRef,
    deduce_choice,
    kind_of,
    plain_label,
    reference_by,
    resolve_fills,
    rule_question,
    set_by_target,
    with_ai,
    with_previous,
    with_system_values,
)
from uie_core.wizard.model import (
    MAX_QUESTIONS,
    SAMPLE_ROWS,
    AllowedValue,
    ChoiceSource,
    ConfigItem,
    ItemStatus,
    TargetField,
    TargetProposal,
    Verification,
    WizardAnswer,
    WizardChoice,
    WizardDataTypeUnknown,
    WizardError,
    WizardInputError,
    WizardQuestion,
    WizardStep,
    WizardUnavailable,
    proposal_from_raw,
    question_from_raw,
)
from uie_core.wizard.proposer import (
    AnswerFact,
    MigrationWizardProposer,
    MissingFieldFact,
    RegistrySystemFact,
    WizardDataTypeProposer,
    WizardDataTypeRequest,
    WizardMissingProposer,
    WizardMissingRequest,
    WizardRaw,
    WizardRequest,
)
from uie_core.wizard.quality import BARCODE_KINDS, PRICE_KINDS, REFERENCE_KINDS, check_reference, is_barcode
from uie_core.wizard.quick import (
    CATEGORY_KEY,
    CATEGORY_NAME_KEY,
    DATA_TYPES,
    DEFAULTABLE_KINDS,
    MAX_BLOCKING,
    PARENT_CATEGORY_KEY,
    UNIT_KEYS,
    QuickQuestion,
    QuickStart,
    TargetOption,
    base_unit_first,
    currency_question,
    data_type_mismatch,
    data_type_options,
    infer_data_type,
    records_question,
    registry_structure,
    target_default_question,
    target_for_data_type,
    units_question,
    with_created_count,
    with_default,
)
from uie_core.wizard.records import cached_batch, store_batch
from uie_core.wizard.text import say
from uie_core.wizard.units import is_package
from uie_core.wizard.values import (
    ISO_4217,
    LEFT_OUT_BY_CHOICE,
    ValueIssue,
    ValueRuleProposer,
    detect_currency,
    is_money,
    scan_currencies,
    suggest_rules,
)

log = logging.getLogger("uie.wizard")
T = TypeVar("T")

REGISTRY_PREFIX = "registry:"  # système (ou version) du Registry avec schéma publié
KNOWN_PREFIX = "known:"  # système connu du Registry, sans schéma
INSTANCE_PREFIX = "instance:"  # instance (système connecté) de l'organisation
DATA_TYPE_MIN = 0.6  # confiance minimale du LLM pour le type de données d'un fichier
TARGET_TOPIC = "target_system"
# système connecté choisi comme cible (« instance:<id> ») : seul lu pour ses valeurs réelles ; libellé
# générique, jamais envoyé au LLM
INSTANCE_TOPIC = "connected_system"
VERSION_TOPICS = frozenset({"version", "release", "target_version"})
AI_NOTE = "Check this structure against the official import template of the target system before any import."
# catégorie du Registry → libellé de groupe affiché
_CATEGORY = {
    "erp": "ERP",
    "accounting": "Accounting",
    "ecommerce": "E-commerce",
    "crm": "CRM",
    "hr": "HR & payroll",
}
_CACHE_SIZE = 256
_cache: OrderedDict[str, Any] = OrderedDict()


def _cached(key: str) -> Any:
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    return None


def _remember(key: str, value: Any) -> None:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)


def clear_cache() -> None:
    _cache.clear()


def _topic_label(topic: str) -> str:
    return topic.replace("_", " ").strip().capitalize() or topic


def shared(answers: list[WizardAnswer]) -> list[WizardAnswer]:
    """Réponses transmises au LLM : jamais celle du système connecté choisi (identifiant interne)."""
    return [a for a in answers if a.topic != INSTANCE_TOPIC]


IDENTITY_KEY, NAME_KEY = "sku", "name"  # clés du vocabulaire : référence interne et nom d'un enregistrement


class MigrationWizardService:
    def __init__(
        self,
        session: Session,
        ctx: Context,
        proposer: MigrationWizardProposer | None,
        references: ReferenceFinder | None = None,
        exports: Any | None = None,
    ) -> None:
        self.ctx = ensure_context(ctx)
        self.s = session
        self.registry = RegistryService(session, self.ctx)
        self.proposer = proposer
        self.references = references  # valeurs réelles d'un système connecté (catégories, unités…)
        # services d'export des formats de fichier (profils, comptes, historique) : `partners`, `accounting`,
        # `profiles` ; None : les cibles format restent des structures d'import
        self.exports = exports

    def _format(self, answers: list[WizardAnswer]) -> FormatTarget | None:
        """Cible FORMAT DE FICHIER (binding fichier du système, du produit et de la version choisis)."""
        if self.exports is None:
            return None
        for a in answers:
            if a.topic in VERSION_TOPICS and a.value.startswith(REGISTRY_PREFIX):
                parts = a.value[len(REGISTRY_PREFIX) :].split(":")
                if len(parts) == 3:
                    name = next((x.label for x in answers if x.topic == TARGET_TOPIC), parts[0])
                    product = next(
                        (c.label for c in self._version_choices(parts[0]) if c.value == a.value), ""
                    )
                    return format_target(parts[0], parts[1], parts[2], _target_label(name, product))
        return None

    @property
    def available(self) -> bool:
        return self.proposer is not None

    # ------------------------------------------------------------------ #
    # Faits du Registry
    # ------------------------------------------------------------------ #
    def registry_facts(self) -> list[RegistrySystemFact]:
        out = []
        for system in self.registry.list_systems():
            versions: list[str] = []
            interfaces: set[str] = set()
            for product in system.products:
                for v in sorted(product.versions, key=lambda x: _version_key(x.version)):
                    if not any(i.snapshots for i in v.interfaces):
                        continue
                    versions.append(_version_label(system, product, v))
                    interfaces |= {i.type for i in v.interfaces}
            meta = system.metadata_ or {}
            description = meta.get("description")
            if versions or system.status == KNOWN_STATUS:
                out.append(
                    RegistrySystemFact(
                        code=system.code,
                        name=system.name,
                        versions=versions,
                        interfaces=sorted(interfaces),
                        has_schema=bool(versions),
                        category=system.category,
                        description=description if isinstance(description, str) else None,
                    )
                )
        # systèmes avec schéma publié d'abord, puis les systèmes seulement connus (ordre du nom)
        return sorted(out, key=lambda f: (not f.has_schema, f.name.casefold()))

    def _version_choices(self, code: str) -> list[WizardChoice]:
        return [c for c, _ in self._registry_versions(code)]

    def _registry_versions(self, code: str) -> list[tuple[WizardChoice, bool]]:
        """Versions publiées d'un système du Registry : (choix, est un FORMAT de fichier du système)."""
        for system in self.registry.list_systems():
            if system.code != code:
                continue
            out = []
            for product in system.products:
                for v in sorted(product.versions, key=lambda x: _version_key(x.version)):
                    if not any(i.snapshots for i in v.interfaces):
                        continue
                    choice = WizardChoice(
                        value=f"{REGISTRY_PREFIX}{system.code}:{product.code}:{v.version}",
                        label=_version_label(system, product, v),
                        source=ChoiceSource.REGISTRY,
                    )
                    out.append((choice, _is_format(system, product, v)))
            return out
        return []

    @staticmethod
    def _target_code(answers: list[WizardAnswer]) -> str | None:
        for a in answers:
            if a.topic != TARGET_TOPIC:
                continue
            for prefix in (REGISTRY_PREFIX, KNOWN_PREFIX):
                if a.value.startswith(prefix):
                    return a.value[len(prefix) :].split(":")[0]
        return None

    @staticmethod
    def _system_choices(facts: list[RegistrySystemFact]) -> list[WizardChoice]:
        """Systèmes du Registry : avec schéma (vérifié) d'abord, puis seulement connus (sans schéma)."""
        return [
            WizardChoice(
                value=f"{REGISTRY_PREFIX if f.has_schema else KNOWN_PREFIX}{f.code}",
                label=f.name,
                # une version qui n'est que le nom du système (son API) n'apprend rien : pas en description
                description=f.description or (", ".join(v for v in f.versions[:4] if v != f.name) or None),
                group=_CATEGORY.get(f.category or ""),
                source=ChoiceSource.REGISTRY if f.has_schema else ChoiceSource.KNOWN,
            )
            for f in facts
        ]

    def _target_question(self, facts: list[RegistrySystemFact]) -> WizardQuestion:
        """Première question SANS appel au LLM : systèmes du Registry ; « Other system » pour tout autre."""
        return WizardQuestion(
            id=TARGET_TOPIC,
            topic=TARGET_TOPIC,
            title="Which target system will receive your data?",
            help="Systems with a verified schema come first. Choose “Other system” if yours is not listed.",
            choices=self._system_choices(facts),
            allow_other=True,
            other_label="Other system",
        )

    def _request(self, answers: list[WizardAnswer], mode: str = "simple") -> WizardRequest:
        facts = self.registry_facts()
        code = self._target_code(answers)
        return WizardRequest(
            answers=shared(answers),
            registry=facts,
            target=next((f for f in facts if f.code == code), None),
            max_questions=MAX_QUESTIONS,
            sample_rows=SAMPLE_ROWS,
            mode="expert" if mode == "expert" else "simple",
        )

    # ------------------------------------------------------------------ #
    # Appels au LLM (avec cache)
    # ------------------------------------------------------------------ #
    def _call(self, kind: str, request: BaseModel, parse: Callable[[Any], T]) -> T:
        """Réponse VALIDÉE (`parse`) ; seule une réponse valide est mise en cache (jamais une erreur)."""
        if self.proposer is None:
            raise WizardUnavailable("The AI assistant is not configured")
        identity = getattr(self.proposer, "cache_identity", type(self.proposer).__name__)
        payload = json.dumps([kind, identity, request.model_dump(mode="json")], sort_keys=True)
        key = hashlib.sha256(payload.encode()).hexdigest()
        hit = _cached(key)
        if hit is not None:
            return parse(hit)
        call = {
            "question": self.proposer.wizard_question,
            "target": self.proposer.wizard_target,
            "missing": getattr(self.proposer, "wizard_missing", None),
            "data_type": getattr(self.proposer, "wizard_data_type", None),
        }[kind]
        if call is None:
            raise WizardError("The AI assistant cannot answer this")
        raw: WizardRaw = call(request)
        m = raw.meta
        log.info(
            "wizard %s: tokens in=%s out=%s cost_usd=%s latency_ms=%s error=%s",
            kind,
            m.input_tokens,
            m.output_tokens,
            m.cost_usd,
            m.latency_ms,
            raw.error,
        )
        if raw.error is not None:
            raise WizardError(f"The AI assistant could not answer ({raw.error})")
        parsed = parse(raw.data)  # lève WizardError si la forme est invalide : rien n'est gardé
        _remember(key, raw.data)
        return parsed

    # ------------------------------------------------------------------ #
    # Étapes
    # ------------------------------------------------------------------ #
    def next(self, answers: list[WizardAnswer], mode: str = "simple") -> WizardStep:
        """Question suivante d'après les réponses données (ou fin : structure cible). Le mode (simple /
        expert) ne change que la formulation des questions suivantes : les réponses données sont gardées."""
        if self.proposer is None:  # sans IA, le parcours ne pourrait pas aller au-delà de la 1re question
            raise WizardUnavailable("The AI assistant is not configured")
        if len(answers) >= MAX_QUESTIONS:
            return WizardStep(done=True, answered=len(answers), remaining=0)
        request = self._request(answers, mode)
        if not answers and request.registry:
            # le Registry sait déjà quels systèmes existent : aucun appel au LLM pour cette question
            question = self._target_question(request.registry)
            return WizardStep(done=False, question=question, answered=0, remaining=5)
        done, question, remaining = self._call(
            "question", request, lambda raw: question_from_raw(raw, len(answers))
        )
        if done or question is None:
            if not answers:
                raise WizardError("The AI assistant ended before asking anything")
            return WizardStep(done=True, answered=len(answers), remaining=0)
        asked = {a.question_id for a in answers}
        if question.id in asked:  # identifiant déjà utilisé : rendu unique (les réponses restent distinctes)
            question = question.model_copy(update={"id": f"{question.id}_{len(answers) + 1}"})
        question = self._with_registry(question, request)
        return WizardStep(done=False, question=question, answered=len(answers), remaining=remaining)

    def _with_registry(self, q: WizardQuestion, request: WizardRequest) -> WizardQuestion:
        """Choix connus du Registry en premier, vérifiés ; les doublons de l'IA sont retirés."""
        if q.topic == TARGET_TOPIC:
            known = self._system_choices(request.registry)
            names = [f.name.casefold() for f in request.registry] + [
                f.code.casefold() for f in request.registry
            ]
            ai = [c for c in q.choices if not any(n in c.label.casefold() for n in names)]
            return q.model_copy(update={"choices": known + ai})
        if q.topic in VERSION_TOPICS and request.target is not None and request.target.has_schema:
            versions = self._version_choices(request.target.code)
            if versions:
                return q.model_copy(update={"choices": versions})
        return q

    def propose(self, answers: list[WizardAnswer]) -> TargetProposal:
        """Structure cible proposée, confrontée au Registry (même structure quel que soit le mode)."""
        if not answers:
            raise WizardError("Answer at least one question first")
        proposal = self._call("target", self._request(answers), proposal_from_raw)
        return self.verify(answers, proposal)

    # ------------------------------------------------------------------ #
    # Fichier du client (jamais stocké) : correspondances, puis fichier préparé
    # ------------------------------------------------------------------ #
    def _context(self, answers: list[WizardAnswer], proposal: TargetProposal) -> dict[str, str]:
        target = next((a.label for a in answers if a.topic == TARGET_TOPIC), "")
        version = next((a.label for a in answers if a.topic in VERSION_TOPICS), "")
        return {"system": target, "version": version, "data_type": proposal.title}

    def map_file(
        self,
        answers: list[WizardAnswer],
        proposal: TargetProposal,
        data: bytes,
        file_name: str,
        previous: list[PreviousChoice] | None = None,
    ) -> tuple[TargetProposal, FileMapping]:
        """Correspondances proposées entre les colonnes du fichier et la structure (vérifiée à nouveau) ; les
        choix déjà retenus pour le même fichier (`previous`) sont réappliqués : une structure régénérée
        ne fait jamais perdre une correspondance (ni redemander à l'IA une colonne déjà réglée)."""
        p = self.verify(answers, proposal)
        columns = (
            _CachedColumns(self.proposer, self.s) if isinstance(self.proposer, WizardColumnProposer) else None
        )
        return p, map_columns(
            p,
            data,
            file_name,
            context=self._context(answers, p),
            proposer=columns,
            previous=previous,
            hints=self._column_hints(answers, p),
        )

    def _column_hints(self, answers: list[WizardAnswer], p: TargetProposal) -> ColumnHints | None:
        """Vocabulaire du domain pack dont la portée contient l'entité de la structure (champ → clé
        sémantique déclarée) et mémoire apprise de l'organisation pour ce système ; None : aucun pack."""
        code, target = self._target_code(answers), self._target_version(answers)
        if not code or target is None or not p.entity:
            return None
        fmt = self._format(answers)
        if fmt is not None:  # colonnes du tableau de source fichier : la clé est le nom du champ
            learned, not_relevant = self._learned_headers(code)
            return ColumnHints(
                spec=fmt.table.semantic_spec({f.name for f in p.fields}),
                field_keys={f.name: f.name for f in p.fields},
                learned=learned,
                not_relevant=not_relevant,
            )
        for domain in DomainPackRegistry.domains_for(code):
            try:
                pack = DomainPackRegistry.resolve(domain, code, target[1])
            except DomainPackError:
                continue
            spec = pack.semantics()
            if spec is None or p.entity not in pack.scope_entities():
                continue
            prefix = f"{p.entity}."
            keys = {k[len(prefix) :]: v for k, v in spec.field_keys.items() if k.startswith(prefix)}
            field_keys = {
                f.name: keys[f.name.split("/", 1)[0]] for f in p.fields if f.name.split("/", 1)[0] in keys
            }
            learned, not_relevant = self._learned_headers(code)
            return ColumnHints(spec=spec, field_keys=field_keys, learned=learned, not_relevant=not_relevant)
        return None

    def learn_choices(
        self,
        answers: list[WizardAnswer],
        proposal: TargetProposal,
        data: bytes,
        file_name: str,
        decisions: list[MappingDecision],
        confirmed: list[int],
    ) -> int:
        """Choix EXPLICITES de l'utilisateur (confirmer, corriger, « non importée ») → mémoire de SON
        organisation, comme les imports (V2c.2 / V2c.2b) : en-tête normalisé → clé sémantique du champ choisi
        (rien si le vocabulaire donne déjà ce résultat seul : une ancienne mémoire est alors retirée) ;
        « non importée » → « pas utile » pour CE système ; une correspondance retire ce « pas utile ». Un
        champ sans clé sémantique dans le domain pack ne peut pas être mémorisé. Rend le nombre
        d'écritures."""
        if not isinstance(self.ctx, TenantContext):
            return 0
        p = self.verify(answers, proposal)
        hints = self._column_hints(answers, p)
        code = self._target_code(answers)
        if hints is None or not code:
            return 0
        system_id = next((s.id for s in self.registry.list_systems() if s.code == code), None)
        _, df = read_table(data, file_name)
        headers = [str(c).strip() for c in df.columns]
        org = self.ctx.organization_id
        rows = list(
            self.s.scalars(
                select(LearnedColumnMapping).where(
                    LearnedColumnMapping.organization_id == org,
                    LearnedColumnMapping.system_id.is_(None) | (LearnedColumnMapping.system_id == system_id),
                )
            )
        )
        mapped = {r.header_key: r for r in rows if r.decision != NOT_RELEVANT}
        ignored = {r.header_key: r for r in rows if r.decision == NOT_RELEVANT}
        keys = sorted(set(hints.field_keys.values()))
        written = 0
        for d in decisions:
            if d.position not in set(confirmed) or not 0 <= d.position < len(headers):
                continue
            key = header_key(headers[d.position])
            if not key:
                continue
            if d.target is None:
                if key not in ignored and system_id is not None:
                    row = LearnedColumnMapping(
                        organization_id=org, header_key=key, decision=NOT_RELEVANT, system_id=system_id
                    )
                    self.s.add(row)
                    ignored[key] = row
                    written += 1
                continue
            semantic = hints.field_keys.get(d.target)
            if semantic is None:
                continue
            if key in ignored:
                self.s.delete(ignored.pop(key))
                written += 1
            rule = rule_key(headers[d.position], hints.spec, keys)
            row = mapped.get(key)
            if rule is not None and rule[0] == semantic and rule[1] >= AI_MATCH_RULE:
                if row is not None:  # déjà couvert par le vocabulaire : la mémoire est inutile
                    self.s.delete(mapped.pop(key))
                    written += 1
                continue
            if row is None:
                mapped[key] = LearnedColumnMapping(organization_id=org, header_key=key, semantic_key=semantic)
                self.s.add(mapped[key])
                written += 1
            elif row.semantic_key != semantic:
                row.semantic_key, row.updated_at = semantic, utcnow()
                written += 1
        self.s.flush()
        return written

    def _learned_headers(self, code: str) -> tuple[dict[str, str], frozenset[str]]:
        """Mémoire des imports de l'organisation (V2c.2 / V2c.2b) : en-tête normalisé → clé sémantique
        choisie ; en-têtes jugés « pas utiles » pour CE système. Rien hors d'un contexte d'organisation."""
        if not isinstance(self.ctx, TenantContext):
            return {}, frozenset()
        system_id = next((s.id for s in self.registry.list_systems() if s.code == code), None)
        rows = self.s.scalars(
            select(LearnedColumnMapping).where(
                LearnedColumnMapping.organization_id == self.ctx.organization_id,
                LearnedColumnMapping.system_id.is_(None) | (LearnedColumnMapping.system_id == system_id),
            )
        )
        learned: dict[str, str] = {}
        ignored: set[str] = set()
        for r in rows:
            if r.decision == NOT_RELEVANT:
                ignored.add(r.header_key)
            elif r.semantic_key:
                learned[r.header_key] = r.semantic_key
        return learned, frozenset(ignored)

    def prepare_file(
        self,
        answers: list[WizardAnswer],
        proposal: TargetProposal,
        data: bytes,
        file_name: str,
        decisions: list[MappingDecision],
        fills: list[Fill] | None = None,
        choices: dict[str, str] | None = None,
        confirmed: list[int] | None = None,
        generate: bool = False,
    ) -> tuple[PreparedFile, bytes, str]:
        """Fichier préparé : structure cible + valeurs du client copiées + valeurs complétées et VALIDÉES
        (aucun appel au LLM). Une valeur non vérifiable exige une confirmation ; son avertissement est
        gardé ; une valeur lue dans le système connecté est vérifiée. `confirmed` : positions des colonnes
        dont l'utilisateur a EXPLICITEMENT confirmé ou corrigé la correspondance → mémoire de
        l'organisation. Cible format de fichier : `generate` produit le fichier du format (numéros de compte
        réservés, pièces journalisées) ; l'aperçu n'enregistre rien."""
        p = self.verify(answers, proposal)
        if confirmed:
            self.learn_choices(answers, p, data, file_name, decisions, confirmed)
        fmt = self._format(answers)
        if fmt is not None:
            return FormatFlow(self, fmt).prepare(
                answers, p, data, file_name, decisions, choices or {}, generate
            )
        values: dict[str, list[str]] = {}
        completed: list[str] = []
        warnings: list[str] = []
        auto, auto_fills = self._deduced(answers, p, decisions, fills or [])
        all_fills = auto_fills + list(fills or [])
        if all_fills:
            _, df = read_table(data, file_name)
            known = self._known_values(answers, p, all_fills)
            values, completed, warnings = resolve_fills(p.fields, all_fills, df.height, known)
            # appliqué d'office (déduction du serveur, ou valeur vérifiée appliquée par le navigateur) :
            # « rempli d'après vos réponses », jamais « complété par vous »
            by_name = {f.name: f for f in p.fields}
            for fill in fills or []:
                if (
                    fill.auto
                    and fill.common
                    and fill.field in by_name
                    and fill.field not in {a["field"] for a in auto}
                ):
                    f = by_name[fill.field]
                    label = next((x.label for x in f.allowed if x.value == fill.common), fill.common)
                    auto.append(
                        {
                            "field": f.name,
                            "label": plain_label(f),
                            "value": label,
                            "explanation": "Deduced from your previous answers.",
                        }
                    )
            completed = [n for n in completed if n not in {a["field"] for a in auto}]
        summary, rows, pairs = prepare(
            p,
            data,
            file_name,
            decisions,
            values,
            completed,
            warnings,
            choices,
            records=self.record_sheets(answers, p),
            units=self.unit_settings(answers, p),
            currency_from_system=bool(self._chosen(answers)),
            noun=data_type_of(answers, p).lower(),
            import_defaults=self.import_defaults(answers, p),
            target_label=self._target_label(answers),
        )
        if isinstance(self.proposer, ValueRuleProposer):
            # l'IA peut SUGGÉRER une option d'une question ouverte (validée) : jamais une valeur, ni appliquée
            summary = summary.model_copy(
                update={
                    "value_questions": suggest_rules(
                        summary.value_questions,
                        self.proposer,
                        {f.name: f for f in p.fields},
                        {
                            q.key: [o.examples[0][0] for o in q.options if o.examples]
                            for q in summary.value_questions
                        },
                    )
                }
            )
        summary = summary.model_copy(
            update={
                "data_type": data_type_of(answers, p),
                "auto_filled": auto,
                **self._readiness(answers, p, summary, rows, {t: c for c, t in pairs}),
            }
        )
        prepared = render_prepared(p, summary, rows, pairs, identity=self.record_identity(answers, p))
        return summary, prepared, prepared_filename(p)

    def record_identity(self, answers: list[WizardAnswer], p: TargetProposal) -> RecordIdentity | None:
        """Identifiant externe des lignes du fichier préparé : champs de la structure qui portent les clés
        « sku » (référence interne) et « name » du domain pack. None : aucun pack."""
        hints = self._column_hints(answers, p)
        if hints is None or not p.entity:
            return None
        by_key: dict[str, str] = {}
        for name, key in hints.field_keys.items():
            if "/" not in name:
                by_key.setdefault(key, name)
        if not ({IDENTITY_KEY, NAME_KEY} & set(by_key)):
            return None
        return RecordIdentity(
            entity=p.entity, key_field=by_key.get(IDENTITY_KEY), name_field=by_key.get(NAME_KEY)
        )

    def missing_info(
        self,
        answers: list[WizardAnswer],
        proposal: TargetProposal,
        data: bytes,
        file_name: str,
        decisions: list[MappingDecision],
        mode: str = "simple",
        fills: list[Fill] | None = None,
        choices: dict[str, str] | None = None,
        ai: bool = True,
    ) -> MissingInfo:
        """Une question par champ obligatoire encore vide (+ champs à valeurs connues qui se DÉDUISENT des
        réponses précédentes) : règles, valeurs du Registry et du système connecté d'abord ; le LLM (un seul
        appel) ne fait que formuler simplement, expliquer et déduire une valeur parmi les valeurs admises."""
        p = self.verify(answers, proposal)
        if self._format(answers) is not None:
            return MissingInfo(questions=[], rows=[], row_count=0)
        # réponses déjà VALIDÉES : appliquées d'abord (jamais redemandées) ; invalides → simplement reposées
        done: dict[str, list[str]] = {}
        _, auto_fills = self._deduced(answers, p, decisions, fills or [])
        if fills or auto_fills:
            _, df = read_table(data, file_name)
            for fill in auto_fills + list(fills or []):
                try:
                    values, _, _ = resolve_fills(
                        p.fields, [fill], df.height, self._known_values(answers, p, [fill])
                    )
                except FillError:
                    continue
                done |= values
        summary, rows, _ = prepare(
            p,
            data,
            file_name,
            decisions,
            done,
            choices=choices,
            records=self.record_sheets(answers, p),
            units=self.unit_settings(answers, p),
            currency_from_system=bool(self._chosen(answers)),
            noun=data_type_of(answers, p).lower(),
        )
        by_name = {f.name: f for f in p.fields}
        mapped = {d.target for d in decisions if d.target} | set(done)
        required = [by_name[n] for n in summary.missing_required]
        optional = [
            f
            for f in p.fields
            if not f.required
            and f.name not in mapped
            and kind_of(f) in (MissingKind.CHOICE, MissingKind.BOOLEAN)
        ]
        questions = self._questions(answers, p, required, optional, mode, fills or [], ai=ai)
        # donnée propre à chaque ligne sans colonne : le fichier a peut-être cette colonne (non importée) → on
        # demande laquelle ; jamais une valeur unique pour toutes les lignes, jamais une suggestion de valeur
        candidates = self._unmapped_columns(data, file_name, decisions)
        if candidates:
            for f in required:
                if kind_of(f) in PER_ROW_KINDS and f.name not in done:
                    questions[f.name] = questions[f.name].model_copy(
                        update={
                            "file_columns": candidates,
                            "choices": [],
                            "suggested": None,
                            "explanation": None,
                            "free_text": False,
                            "title": f"Which column of your file contains {plain_label(f)}?",
                            "help": "Each row needs its own value: choose the column that holds it. A "
                            "single value for every row is never proposed.",
                        }
                    )
        out = [questions[f.name] for f in required]
        # un champ facultatif n'est proposé que s'il se déduit des réponses déjà données (jamais redemandé)
        out += [
            questions[f.name].model_copy(update={"optional": True})
            for f in optional
            if questions[f.name].suggested
        ]
        labels = []
        if summary.rows <= MAX_PER_ROW:
            for i, row in enumerate(rows):
                label = next((v for v in row if v), "") or f"Row {i + 1}"
                labels.append(RowRef(index=i, label=f"{i + 1}. {label}"[:120]))
        return MissingInfo(
            questions=out,
            rows=labels,
            row_count=summary.rows,
            created={c.field: len(c.rows) for c in summary.created_records},
        )

    def _questions(
        self,
        answers: list[WizardAnswer],
        p: TargetProposal,
        required: list[TargetField],
        optional: list[TargetField],
        mode: str,
        fills: list[Fill],
        ai: bool = True,
    ) -> dict[str, MissingQuestion]:
        """Questions des champs à compléter (avant ou après le fichier, mêmes règles) : règles, valeurs du
        Registry et du système connecté d'abord ; le LLM (un seul appel) ne fait que formuler, expliquer et
        déduire une valeur parmi les valeurs admises ; réponses déjà données reprises, jamais redemandées en
        double (même sorte d'enregistrement : `same_as`)."""
        questions = {f.name: rule_question(f) for f in required + optional}
        options: dict[str, list[tuple[str, str]]] = {}
        for f in required:
            options[f.name] = [(o.value, o.label) for o in self._system_options(answers, f)]
            if options[f.name]:
                questions[f.name] = with_system_values(questions[f.name], options[f.name])
        raw = self._missing_ai(answers, p, required + optional, mode) if ai else {}
        simple = mode != "expert"
        for f in required + optional:
            if f.name in raw:
                questions[f.name] = with_ai(questions[f.name], f, raw[f.name], simple=simple)
        # réponses déjà données sans valeur écrite (réponse métier, « je ne sais pas ») : gardées et résolues
        connected = self._target_connected(answers)
        for fill in fills:
            if fill.field in questions and (fill.hint or fill.unknown) and fill.field in options:
                questions[fill.field] = with_previous(
                    questions[fill.field], fill, options[fill.field], system_connected=connected
                )
        # même sorte d'enregistrement (ex. deux unités) : la réponse au premier est reprise pour l'autre
        first_of: dict[str, str] = {}
        for f in required:
            if f.reference and f.relation:
                if f.relation in first_of and not questions[f.name].suggested:
                    questions[f.name] = questions[f.name].model_copy(update={"same_as": first_of[f.relation]})
                first_of.setdefault(f.relation, f.name)
        return questions

    # ------------------------------------------------------------------ #
    # Mode Simple « fichier d'abord »
    # ------------------------------------------------------------------ #
    def quick_start(
        self,
        target_value: str,
        data: bytes,
        file_name: str,
        mode: str = "simple",
        data_type: str | None = None,
    ) -> QuickStart:
        """Démarrage par le fichier, en UNE réponse : cible (instance de l'organisation : version et devise de
        la société lues ; ou système / version du Registry), type de données d'après les en-têtes (règles,
        LLM seulement si elles ne tranchent pas), structure construite depuis le snapshot du Registry (aucun
        appel à `propose` quand la cible et sa version y sont), correspondances, puis au plus 3 questions
        bloquantes avec une valeur présélectionnée (règles, Registry, système connecté : aucun appel au LLM
        pour elles). Le reste du parcours (aperçu, fichier préparé) reprend les réponses rendues."""
        target, code, version_value, label = self._quick_target(target_value)
        fact = next((f for f in self.registry_facts() if f.code == code), None)
        verified = bool(fact and fact.has_schema)
        answers = [
            WizardAnswer(
                question_id=TARGET_TOPIC,
                topic=TARGET_TOPIC,
                question="Which target system will receive your data?",
                value=f"{REGISTRY_PREFIX if verified else KNOWN_PREFIX}{code}",
                label=fact.name if fact else code,
                source=ChoiceSource.REGISTRY if verified else ChoiceSource.KNOWN,
            )
        ]
        if version_value:
            answers.append(
                WizardAnswer(
                    question_id="version",
                    topic="version",
                    question="Which version?",
                    value=version_value,
                    label=version_value.rsplit(":", 1)[-1],
                    source=ChoiceSource.REGISTRY,
                )
            )
        if target is not None:
            answers.append(
                WizardAnswer(
                    question_id=INSTANCE_TOPIC,
                    topic=INSTANCE_TOPIC,
                    question="Which of your systems?",
                    value=target_value,
                    label="Your connected system",
                    source=ChoiceSource.REGISTRY,
                )
            )
        _, df = read_table(data, file_name)
        fmt = self._format(answers)
        if fmt is not None:  # format de fichier (DATEV…) : fichier → CDM → binding du format
            return FormatFlow(self, fmt).quick_start(answers, label, data, file_name, df, target, data_type)
        options = data_type_options(DomainPackRegistry.domains_for(code))
        mismatch = (
            None if data_type else data_type_mismatch([str(c).strip() for c in df.columns], options, label)
        )
        if mismatch:  # fichier d'un autre type que ceux que la cible reçoit : dit, jamais forcé
            raise WizardDataTypeUnknown(mismatch, options)
        if data_type is not None:  # choisi par l'utilisateur (type non reconnu au premier essai)
            if data_type not in options:
                raise WizardDataTypeUnknown("Choose one of the kinds of data this target takes", options)
            data_type_from = "user"
        elif len(options) == 1:  # la cible ne reçoit qu'un type de données : rien à demander
            data_type, data_type_from = options[0], "target"
        else:
            data_type, data_type_from = (
                infer_data_type([str(c).strip() for c in df.columns], options),
                "rules",
            )
            if data_type not in options:
                data_type = None
        if data_type is None:
            try:
                data_type, data_type_from = self._data_type_ai(answers, df, options), "ai"
            except WizardInputError:
                raise WizardDataTypeUnknown(
                    "We could not tell what kind of data your file holds: choose it", options
                ) from None
        answers.append(
            WizardAnswer(
                question_id=DATA_TYPE_TOPIC,
                topic=DATA_TYPE_TOPIC,
                question="What kind of data does your file hold?",
                value=data_type.casefold().replace(" ", "_"),
                label=data_type,
                source=ChoiceSource.FILE if data_type_from == "rules" else ChoiceSource.AI,
            )
        )
        proposal = self._registry_proposal(answers, data_type, label)
        structure_from = "registry"
        if proposal is None:
            proposal, structure_from = self.propose(answers), "ai"
        p, mapping = self.map_file(answers, proposal, data, file_name)
        decisions = [
            MappingDecision(position=c.position, target=c.target)
            for c in mapping.columns
            if c.target and c.status in (ColumnStatus.MATCHED, ColumnStatus.AI_MATCH, ColumnStatus.PREVIOUS)
        ]
        choices = {"currency": target.currency} if target and target.currency else {}
        currency_q = None
        if "currency" not in choices:
            found, currency_q = self._file_currency(p, data, file_name, decisions, data_type.lower())
            if found:
                choices["currency"] = found  # une seule devise, certaine : déduite du fichier
        # 1. décisions sur les valeurs (devise d'abord : jamais un prix laissé de côté sans le dire)
        asked: list[QuickQuestion] = [currency_q] if currency_q is not None else []
        mapped = {d.target for d in decisions}
        for spec in self.record_sheets(answers, p).values():
            if spec.field in mapped and not self._records_known(answers, p, spec, data, file_name, decisions):
                asked.append(records_question(spec, label))
        package = self._package_units(answers, p, data, file_name, decisions)
        if package is not None:
            asked.append(package)
        # 2. champs encore à compléter, une fois les réponses présélectionnées appliquées
        preset = {**choices, **{q.decision: q.default for q in asked if q.decision and q.default}}
        info = self.missing_info(answers, p, data, file_name, decisions, mode, choices=preset, ai=False)
        # nombre d'enregistrements à créer, lu dans la préparation présélectionnée (« Create 1,643 … »)
        asked = [
            with_created_count(q, info.created[q.question.field])
            if q.decision == records_key(q.question.field) and q.question.field in info.created
            else q
            for q in asked
        ]
        defaulted: list[QuickQuestion] = []
        units = self.unit_settings(answers, p)
        for q in info.questions:
            if q.optional:
                continue
            if units and q.from_system and q.field in units.unit_fields:
                q = base_unit_first(q, units.unit_names.get("unit"))  # noqa: PLW2901
            quick = with_default(q)
            if quick.default is None and q.kind in DEFAULTABLE_KINDS:
                quick = target_default_question(q, label)  # la cible applique sa propre valeur par défaut
            # valeur d'un champ que le fichier ne donne pas, présélectionnable (type d'article, unité par
            # défaut de la cible) : appliquée et listée, jamais demandée ; seules les décisions sur les
            # valeurs du fichier et les champs sans présélection restent des questions
            (defaulted if quick.default else asked).append(quick)
        # 3. au plus MAX_BLOCKING questions ; au-delà, la réponse présélectionnée est appliquée et listée
        applied = [*defaulted, *(q for q in asked[MAX_BLOCKING:] if q.default)]
        choices |= {q.decision: q.default for q in applied if q.decision and q.default}
        fills = [
            Fill(field=q.question.field, common=q.default, confirmed=True)
            for q in applied
            if not q.decision and q.default
        ]
        return QuickStart(
            target_label=label,
            target=target,
            data_type=data_type,
            data_type_from=data_type_from,
            structure_from=structure_from,
            answers=answers,
            proposal=p,
            mapping=mapping,
            decisions=decisions,
            choices=choices,
            questions=asked[:MAX_BLOCKING],
            applied=applied,
            fills=fills,
            more_questions=sum(1 for q in asked[MAX_BLOCKING:] if not q.default),
        )

    def record_sheets(self, answers: list[WizardAnswer], p: TargetProposal) -> dict[str, RecordSheet]:
        """Champs des lignes qui désignent une catégorie (clé « category » du domain pack) et comment créer
        les catégories manquantes : entité liée (Registry), champ du nom (« category_name ») et du parent
        (« parent_category ») déclarés par le pack pour cette entité. {} : aucun pack."""
        hints = self._column_hints(answers, p)
        if hints is None or self._format(answers) is not None:
            return {}
        out: dict[str, RecordSheet] = {}
        for f in p.fields:
            if hints.field_keys.get(f.name) != CATEGORY_KEY or not f.relation:
                continue
            prefix = f"{f.relation}."
            keys = {v: k[len(prefix) :] for k, v in hints.spec.field_keys.items() if k.startswith(prefix)}
            if CATEGORY_NAME_KEY in keys:
                out[f.name] = RecordSheet(
                    field=f.name,
                    label=f.label,
                    entity=f.relation,
                    name_field=keys[CATEGORY_NAME_KEY],
                    parent_field=keys.get(PARENT_CATEGORY_KEY),
                )
        return out

    def _domain_pack(self, answers: list[WizardAnswer], p: TargetProposal) -> DomainPack | None:
        """Domain pack du système cible dont la portée contient l'entité de la structure ; None : aucun."""
        code, target = self._target_code(answers), self._target_version(answers)
        if not code or target is None or not p.entity:
            return None
        for domain in DomainPackRegistry.domains_for(code):
            try:
                pack = DomainPackRegistry.resolve(domain, code, target[1])
            except DomainPackError:
                continue
            if p.entity in pack.scope_entities():
                return pack
        return None

    def import_defaults(self, answers: list[WizardAnswer], p: TargetProposal) -> dict[str, ImportDefault]:
        """Valeur que l'import de fichier de la cible donne à un champ sans valeur (domain pack) ; {} :
        inconnue."""
        pack = self._domain_pack(answers, p)
        return pack.import_defaults() if pack is not None else {}

    @staticmethod
    def _target_label(answers: list[WizardAnswer]) -> str:
        """« Odoo 17 » : système et version choisis (libellés des réponses)."""
        system = next((a.label for a in answers if a.topic == TARGET_TOPIC), "")
        version = next((a.label for a in answers if a.topic in VERSION_TOPICS), "")
        return f"{system} {version}".strip() or "The target"

    def unit_settings(self, answers: list[WizardAnswer], p: TargetProposal) -> UnitSettings | None:
        """Unités de la cible (domain pack) : noms des unités, champs qui désignent une unité (clés « uom » /
        « uom_purchase » du pack) et unité des champs chiffrés de la structure."""
        pack = self._domain_pack(answers, p)
        hints = self._column_hints(answers, p)
        if pack is None or hints is None:
            return None
        names = {f.name for f in p.fields}
        return UnitSettings(
            unit_names=pack.unit_names(),
            unit_fields=[n for n, k in hints.field_keys.items() if k in UNIT_KEYS and n in names],
            field_units={k: v for k, v in pack.field_units().items() if k in names},
        )

    def _package_units(
        self,
        answers: list[WizardAnswer],
        p: TargetProposal,
        data: bytes,
        file_name: str,
        decisions: list[MappingDecision],
    ) -> QuickQuestion | None:
        """Une colonne d'unités contient des unités d'emballage (Set, Packung…) que la cible ne connaît pas :
        une question, « compter chacune comme une pièce » présélectionné."""
        settings = self.unit_settings(answers, p)
        if settings is None or "unit" not in settings.unit_names:
            return None
        by_target = {d.target: d.position for d in decisions if d.target}
        _, df = read_table(data, file_name)
        for name in settings.unit_fields:
            if name not in by_target:
                continue
            values = df.get_column(df.columns[by_target[name]]).drop_nulls().to_list()
            found = list(dict.fromkeys(str(v).strip() for v in values if is_package(str(v))))
            if found:
                label = next(f.label for f in p.fields if f.name == name)
                return units_question(name, label, found, settings.unit_names["unit"])
        return None

    def _records_known(
        self,
        answers: list[WizardAnswer],
        p: TargetProposal,
        spec: RecordSheet,
        data: bytes,
        file_name: str,
        decisions: list[MappingDecision],
    ) -> bool:
        """Toutes les valeurs du fichier existent-elles dans le système CONNECTÉ ? Sans connexion : non (rien
        n'est supposé)."""
        if not self._target_connected(answers):
            return False
        field = next((f for f in p.fields if f.name == spec.field), None)
        known = self._all_options(answers, field) if field else None
        if not known:
            return False
        position = next(d.position for d in decisions if d.target == spec.field)
        _, df = read_table(data, file_name)
        values = {str(v).strip() for v in df.get_column(df.columns[position]).drop_nulls().to_list()}
        return values <= {o.value for o in known}

    def _file_currency(
        self,
        p: TargetProposal,
        data: bytes,
        file_name: str,
        decisions: list[MappingDecision],
        noun: str = "rows",
    ) -> tuple[str | None, QuickQuestion | None]:
        """Devise des prix d'après le fichier, quand un montant est importé : colonne de devises, sinon
        devises citées dans les valeurs (toutes colonnes). Une seule devise certaine → (devise, None) ; sinon
        (None, question avec la plus fréquente, ou EUR, présélectionnée) ; aucun montant importé → (None,
        None)."""
        targets = {d.target for d in decisions if d.target}
        if not any(is_money(f) for f in p.fields if f.name in targets):
            return None, None
        _, df = read_table(data, file_name)
        names = [str(c).strip() for c in df.columns]
        cells = [["" if v is None else str(v) for v in df.get_column(c).to_list()] for c in df.columns]
        column = detect_currency(names, cells)
        codes = dict(column.counts) if column else dict(scan_currencies(cells))
        certain = [c for c in codes if c in ISO_4217]
        if len(codes) == 1 and certain:
            return certain[0], None
        return None, currency_question(codes, noun)

    def quick_targets(self) -> list[TargetOption]:
        """Cibles du démarrage par le fichier, dans l'ordre : instances de l'organisation dont le système et
        la version ont un schéma au Registry (connectées d'abord), puis chaque version du Registry, puis les
        systèmes connus sans schéma. Aucune lecture d'un système connecté."""
        facts = self.registry_facts()
        names = {f.code: f.name for f in facts}
        out: list[TargetOption] = []
        lister = getattr(self.references, "instance_choices", None)
        for inst in lister() if lister is not None else []:
            value = f"{REGISTRY_PREFIX}{inst.system}:{inst.product}:{inst.version}"
            choice = next((c for c in self._version_choices(inst.system) if c.value == value), None)
            if choice is None:
                continue  # version absente du Registry : pas une cible du démarrage rapide
            out.append(
                TargetOption(
                    value=f"{INSTANCE_PREFIX}{inst.id}",
                    label=_target_label(names.get(inst.system, inst.system), choice.label),
                    group="instance",
                    detail=inst.name,
                    connected=inst.connected,
                    receives=self._receives(inst.system, inst.product, inst.version),
                )
            )
        for f in (f for f in facts if f.has_schema):
            versions = self._registry_versions(f.code)
            last = {}  # produit → dernière version (choix triés par version croissante)
            for c, _ in versions:
                last[c.value.split(":")[2]] = c.value
            for c, is_format in versions:
                _, _, product, version = c.value.split(":")
                out.append(
                    TargetOption(
                        value=c.value,
                        label=_target_label(f.name, c.label),
                        group="registry",
                        system=f.code,
                        system_name=f.name,
                        # format de fichier seulement : le système lui-même ou son API n'est pas un format
                        format=c.label if is_format else None,
                        product=product,
                        product_name=c.label.removesuffix(f" {version}") if is_format else None,
                        version=version,
                        latest=last[product] == c.value,
                        receives=self._receives(f.code, product, version),
                    )
                )
        every = list(DATA_TYPES)
        out += [
            TargetOption(value=f"{KNOWN_PREFIX}{f.code}", label=f.name, group="known", receives=every)
            for f in sorted((f for f in facts if not f.has_schema), key=lambda f: f.name.casefold())
        ]
        return out

    def detect_target(self, data: bytes, file_name: str, targets: list[str]) -> tuple[str | None, str | None]:
        """Format choisi dès le dépôt du fichier, parmi les cibles du Registry proposées à l'écran
        (« registry:<système>:<produit>:<version> ») : (cible, type de données reconnu). Type d'après
        les en-têtes, par les RÈGLES seulement (aucun appel au LLM) ; cible = la seule qui reçoit ce type.
        Type pas sûr, ou plusieurs cibles possibles : (None, type ou None), rien n'est présélectionné. Rien
        n'est stocké."""
        _, df = read_table(data, file_name)
        looks = infer_data_type([str(c) for c in df.columns])
        receives = {}
        for value in targets:
            parts = value[len(REGISTRY_PREFIX) :].split(":") if value.startswith(REGISTRY_PREFIX) else []
            if len(parts) == 3:
                receives[value] = self._receives(*parts)
        return target_for_data_type(looks, receives), looks

    def _receives(self, system: str, product: str, version: str) -> list[str]:
        """Types de données qu'une cible reçoit : ceux du tableau de source pour un format de fichier, sinon
        ceux des domain packs du système."""
        fmt = format_target(system, product, version, system) if self.exports is not None else None
        if fmt is not None:
            return list(fmt.table.data_types)
        return data_type_options(DomainPackRegistry.domains_for(system))

    def _quick_target(self, value: str) -> tuple[InstanceTarget | None, str, str | None, str]:
        """(instance, code du système, valeur de version du Registry, libellé) d'une cible :
        « instance:<id> », « registry:<système>[:<produit>:<version>] » (sans version : la plus récente
        du Registry) ou « known:<système> » (sans schéma : structure proposée par l'IA)."""
        target: InstanceTarget | None = None
        if value.startswith(INSTANCE_PREFIX):
            finder = getattr(self.references, "instance_target", None)
            if finder is None:
                raise WizardInputError("Connected systems cannot be read here")
            try:
                instance_id = uuid.UUID(value[len(INSTANCE_PREFIX) :])
            except ValueError:
                raise WizardInputError("Unknown target system") from None
            target = finder(instance_id)
            value = f"{REGISTRY_PREFIX}{target.system}:{target.product}:{target.version}"
        if value.startswith(KNOWN_PREFIX):
            code = value[len(KNOWN_PREFIX) :]
            name = next((f.name for f in self.registry_facts() if f.code == code), None)
            if name is None:
                raise WizardInputError("Unknown target system")
            return None, code, None, name
        if not value.startswith(REGISTRY_PREFIX):
            raise WizardInputError("Choose a target system of the Registry")
        parts = value[len(REGISTRY_PREFIX) :].split(":")
        versions = self._version_choices(parts[0])
        if len(parts) == 3:
            choice = next((c for c in versions if c.value == value), None)
        elif len(parts) == 1:
            choice = versions[-1] if versions else None
        else:
            choice = None
        if choice is None:
            raise WizardInputError("This target version is not in the Registry")
        name = next((f.name for f in self.registry_facts() if f.code == parts[0]), parts[0])
        return target, parts[0], choice.value, _target_label(name, choice.label)

    def _data_type_ai(self, answers: list[WizardAnswer], df: Any, options: list[str]) -> str:
        """Type de données par le LLM quand les règles ne tranchent pas : seulement parmi `options`, avec une
        confiance suffisante ; sinon l'erreur est dite (jamais un type deviné)."""
        if not isinstance(self.proposer, WizardDataTypeProposer):
            raise WizardInputError("We could not tell what kind of data your file holds")
        target = self._target_version(answers)
        request = WizardDataTypeRequest(
            system=next((a.label for a in answers if a.topic == TARGET_TOPIC), ""),
            version=target[1] if target else "",
            columns=[
                {
                    "header": str(name).strip()[:120],
                    "examples": [
                        mask_example(str(v))
                        for v in df.get_column(name).drop_nulls().unique(maintain_order=True).to_list()[:3]
                    ],
                }
                for name in df.columns[:80]
            ],
            options=options,
        )

        def parse(raw: Any) -> str | None:
            if not isinstance(raw, dict):
                raise WizardError("The AI answer is not readable")
            found, confidence = raw.get("data_type"), raw.get("confidence")
            ok = found in options and isinstance(confidence, int | float) and confidence >= DATA_TYPE_MIN
            return str(found) if ok else None

        found = self._call("data_type", request, parse)
        if found is None:
            raise WizardInputError("We could not tell what kind of data your file holds")
        return found

    def _registry_proposal(
        self, answers: list[WizardAnswer], data_type: str, label: str
    ) -> TargetProposal | None:
        """Structure depuis le snapshot du Registry (domain pack du type de données) ; None : à proposer."""
        target = self._target_version(answers)
        domain = DATA_TYPES.get(data_type)
        if target is None or domain is None:
            return None
        system, version = target
        try:
            pack = DomainPackRegistry.resolve(domain, system, version)
        except DomainPackError:
            return None
        snap = self._snapshot(answers)
        if snap is None:
            return None
        model = self.registry.load_snapshot_model(snap, entities=pack.scope_entities())
        return registry_structure(pack.resolve(model), model, data_type, label)

    def early_info(
        self,
        answers: list[WizardAnswer],
        proposal: TargetProposal,
        mode: str = "simple",
        fills: list[Fill] | None = None,
    ) -> MissingInfo:
        """Questions PRÉVISIBLES, posées avant le fichier (« Your answers ») : champs obligatoires de la
        structure dont la valeur ne dépend pas des lignes (valeurs admises, oui / non, enregistrement de la
        cible). Ni les champs déduits des réponses, ni ceux que la cible calcule (Registry), ni ceux déjà
        répondus ne sont demandés. `fills` (réponses données) est validé ici (FillError → 422) ; appliqué au
        fichier ensuite, il ne remplit que les cellules vides (le fichier passe toujours avant)."""
        p = self.verify(answers, proposal)
        given = list(fills or [])
        if given:
            resolve_fills(p.fields, given, 1, self._known_values(answers, p, given))
        _, auto = self._deduced(answers, p, [], given)
        answered = {f.field for f in given} | {f.field for f in auto}
        required = [
            f
            for f in p.fields
            if f.required
            and f.name not in answered
            and not set_by_target(f, p.fields)
            and kind_of(f) not in PER_ROW_KINDS
        ]
        questions = self._questions(answers, p, required, [], mode, given)
        return MissingInfo(questions=[questions[f.name] for f in required], rows=[], row_count=0)

    @staticmethod
    def _unmapped_columns(data: bytes, file_name: str, decisions: list[MappingDecision]) -> list[FileColumn]:
        """Colonnes du fichier AVEC des valeurs qui ne vont dans aucun champ (exemples courts)."""
        _, df = read_table(data, file_name)
        mapped = {d.position for d in decisions if d.target}
        out = []
        for pos, name in enumerate(df.columns):
            if pos in mapped:
                continue
            values = [str(v).strip() for v in df.get_column(name).drop_nulls().to_list() if str(v).strip()]
            if values:
                examples = [v if len(v) <= 60 else v[:59] + "…" for v in list(dict.fromkeys(values))[:3]]
                out.append(FileColumn(position=pos, header=str(name).strip(), examples=examples))
        return out

    def _target_version(self, answers: list[WizardAnswer]) -> tuple[str, str] | None:
        """Système et version du Registry choisis (schéma publié) ; sinon None."""
        for a in answers:
            if a.topic in VERSION_TOPICS and a.value.startswith(REGISTRY_PREFIX):
                parts = a.value[len(REGISTRY_PREFIX) :].split(":")
                if len(parts) == 3:
                    return parts[0], parts[2]
        return None

    def _system_options(self, answers: list[WizardAnswer], f: TargetField) -> list[ReferenceOption]:
        """Valeurs réelles d'une référence, lues dans le système connecté de l'organisation (sinon [])."""
        target = self._target_version(answers)
        if self.references is None or target is None or not f.reference or not f.relation:
            return []
        attribute = f.name.split("/", 1)[1] if "/" in f.name else None
        chosen = self._chosen(answers)
        return self.references.options(target[0], target[1], f.relation, attribute, **chosen) or []

    def _all_options(self, answers: list[WizardAnswer], f: TargetField) -> list[ReferenceOption] | None:
        """Valeurs existantes d'une référence pour VÉRIFIER celles du fichier (lecture large) ; None : rien
        lu."""
        target = self._target_version(answers)
        if self.references is None or target is None or not f.reference or not f.relation:
            return None
        attribute = f.name.split("/", 1)[1] if "/" in f.name else None
        read = getattr(self.references, "all_options", None) or self.references.options
        return read(target[0], target[1], f.relation, attribute, **self._chosen(answers))

    def _readiness(
        self,
        answers: list[WizardAnswer],
        p: TargetProposal,
        summary: PreparedFile,
        rows: list[list[str]],
        columns: dict[str, str],
    ) -> dict[str, Any]:
        """Contrôles RÉELLEMENT effectués et statut qui en découle : « ready » (prêt pour l'import)
        seulement si la structure est vérifiée par le Registry, aucun obligatoire ne manque, aucune valeur
        saisie n'est non vérifiée, chaque référence a été trouvée dans le système connecté, codes-barres et
        prix sont complets ; sinon « prepared » (fichier préparé, à revoir) ou « to_complete ». Les
        références introuvables ou non vérifiées deviennent des anomalies avec leurs lignes (`rows` : toutes
        les lignes, mêmes rangs que le fichier ; `columns` : champ → colonne du client)."""
        out = {e.row - 1 for e in summary.excluded_rows}
        written = [(i, r) for i, r in enumerate(rows) if i not in out]
        by_header = {h: i for i, h in enumerate(summary.headers)}
        ctx = self._context(answers, p)
        system = " ".join(x for x in (ctx.get("system", ""), ctx.get("version", "")) if x) or "target system"
        connected = self._target_connected(answers)
        found: list[ValueIssue] = []
        checked = 0  # références dont TOUTES les valeurs ont été trouvées dans le système connecté
        refs_ok = True
        # valeurs créées par le fichier lui-même (feuille « (import first) », importée avant les lignes)
        created = {c.field for c in summary.created_records}
        for f in p.fields:
            if not f.reference or f.name not in by_header:
                continue
            if f.name in created:
                checked += 1  # elles existeront dans la cible avant l'import des lignes
                continue
            used: dict[str, list[int]] = {}
            for i, r in written:
                if r[by_header[f.name]]:
                    used.setdefault(r[by_header[f.name]], []).append(i)
            if not used:
                refs_ok = refs_ok and not f.required  # obligatoire vide : rien n'a pu être vérifié
                continue
            known = self._all_options(answers, f) if connected else None
            ref_issues = check_reference(
                f,
                columns.get(f.name, ""),
                used,
                None if known is None else [o.value for o in known],
                system,
                complete=known is None or len(known) < MAX_CHECK,
            )
            found += ref_issues
            if ref_issues:
                refs_ok = False
            else:
                checked += 1
        refs_ok = refs_ok and checked > 0  # sans aucune vérification réelle, jamais « Done »
        issues = [*summary.value_issues, *found]
        own = LEFT_OUT_BY_CHOICE | BARCODE_KINDS | PRICE_KINDS | REFERENCE_KINDS
        checks = [
            ("Structure checked against the Nexa45 Registry", p.verification == Verification.VERIFIED),
            ("All required fields have a value", not summary.missing_required),
            ("Every value you entered could be checked", not summary.warnings),
            (f"Categories, units and other linked values found in your {system}", refs_ok),
            # moteur de valeurs : chaque valeur lue sans ambiguïté (sauf écartée par un choix explicite)
            ("Every value of your file fits its target field", not any(i.kind not in own for i in issues)),
            ("Every decision about your values is made", all(q.answer for q in summary.value_questions)),
        ]
        if any(is_barcode(f) and f.name in columns for f in p.fields):
            checks.append(("Barcodes are valid and unique", not any(i.kind in BARCODE_KINDS for i in issues)))
        if any(is_money(f) for f in p.fields):
            checks.append(("Every row has a price", not any(i.kind in PRICE_KINDS for i in issues)))
        status = (
            "to_complete"
            if summary.missing_required
            else ("ready" if all(ok for _, ok in checks) else "prepared")
        )
        return {
            "status": status,
            "checks": [{"label": label, "ok": ok} for label, ok in checks],
            "value_issues": issues,
        }

    def _deduced(
        self,
        answers: list[WizardAnswer],
        p: TargetProposal,
        decisions: list[MappingDecision],
        fills: list[Fill],
    ) -> tuple[list[dict[str, str]], list[Fill]]:
        """Champs à valeurs admises (Registry) sans colonne ni réponse, DÉDUITS des réponses du parcours sans
        LLM (`deduce_choice`) : appliqués à chaque préparation, donc jusqu'à l'export. Une réponse de
        l'utilisateur pour ce champ passe toujours avant."""
        mapped = {d.target for d in decisions if d.target} | {f.field for f in fills}
        pairs = [(a.topic, a.label) for a in shared(answers)]
        auto: list[dict[str, str]] = []
        out: list[Fill] = []
        for f in p.fields:
            if f.name in mapped or not f.allowed:
                continue
            found = deduce_choice(f, pairs)
            if found is None:
                continue
            value, label, answer = found
            out.append(Fill(field=f.name, common=value))
            auto.append(
                {
                    "field": f.name,
                    "label": plain_label(f),
                    "value": label,
                    "explanation": f"From your answer “{answer}”: {label}.",
                }
            )
        return auto, out

    def _target_connected(self, answers: list[WizardAnswer]) -> bool:
        """Un système connecté de l'organisation correspond-il à la cible (lecture possible) ?"""
        target = self._target_version(answers)
        connected = getattr(self.references, "connected", None)
        return bool(target and connected and connected(target[0], target[1], **self._chosen(answers)))

    @staticmethod
    def _chosen(answers: list[WizardAnswer]) -> dict[str, uuid.UUID]:
        """Instance choisie comme cible (réponse « connected_system ») : seule lue. {} : aucune."""
        for a in answers:
            if a.topic == INSTANCE_TOPIC and a.value.startswith(INSTANCE_PREFIX):
                try:
                    return {"instance_id": uuid.UUID(a.value[len(INSTANCE_PREFIX) :])}
                except ValueError:
                    return {}
        return {}

    def _known_values(
        self, answers: list[WizardAnswer], p: TargetProposal, fills: list[Fill]
    ) -> dict[str, set[str]]:
        by_name = {f.name: f for f in p.fields}
        known: dict[str, set[str]] = {}
        for fill in fills:
            f = by_name.get(fill.field)
            if f is not None and f.reference and (fill.common or fill.per_row):
                known[f.name] = {o.value for o in self._system_options(answers, f)}
        return known

    def _missing_ai(
        self, answers: list[WizardAnswer], p: TargetProposal, fields: list[TargetField], mode: str
    ) -> dict[str, dict[str, Any]]:
        """Formulation, explication et déduction du LLM ; échec ou IA absente : {} (questions des règles)."""
        if not fields or not isinstance(self.proposer, WizardMissingProposer):
            return {}
        ctx = self._context(answers, p)
        request = WizardMissingRequest(
            system=ctx["system"],
            version=ctx["version"],
            data_type=ctx["data_type"],
            mode="expert" if mode == "expert" else "simple",
            answers=[AnswerFact(question=a.question, answer=a.label) for a in shared(answers)],
            fields=[
                MissingFieldFact(
                    name=f.name,
                    label=f.label,
                    type=f.type,
                    kind=kind_of(f).value,
                    required=f.required,
                    allowed=[{"value": a.value, "label": a.label} for a in f.allowed],
                    reference_by=reference_by(f),  # type: ignore[arg-type]
                    description=f.description,
                    example=f.example,
                )
                for f in fields
            ],
        )
        try:
            data = self._call("missing", request, _missing_items)
        except WizardError:
            return {}  # questions des règles : le parcours continue
        return data

    # ------------------------------------------------------------------ #
    # Confrontation au Registry
    # ------------------------------------------------------------------ #
    def _snapshot(self, answers: list[WizardAnswer]) -> Any:
        for a in answers:
            if a.topic in VERSION_TOPICS and a.value.startswith(REGISTRY_PREFIX):
                parts = a.value[len(REGISTRY_PREFIX) :].split(":")
                if len(parts) == 3:
                    try:
                        return self.registry.resolve_snapshot(
                            SnapshotRef(system=parts[0], product=parts[1], version=parts[2])
                        )
                    except NotFound:
                        return None
        return None

    def _answer_status(self, a: WizardAnswer) -> ItemStatus:
        """Statut d'une réponse RECALCULÉ d'après le Registry (jamais d'après ce que le client déclare)."""
        facts = {f.code: f for f in self.registry_facts()}
        if a.value.startswith(REGISTRY_PREFIX):
            parts = a.value[len(REGISTRY_PREFIX) :].split(":")
            fact = facts.get(parts[0])
            if fact is not None and fact.has_schema:
                if len(parts) == 1:
                    return ItemStatus.VERIFIED_SCHEMA
                if len(parts) == 3:
                    try:
                        ref = SnapshotRef(system=parts[0], product=parts[1], version=parts[2])
                        self.registry.resolve_snapshot(ref)
                        return ItemStatus.VERIFIED_SCHEMA
                    except NotFound:
                        pass
        if a.value.startswith(KNOWN_PREFIX) and a.value[len(KNOWN_PREFIX) :] in facts:
            return ItemStatus.KNOWN_SYSTEM
        if a.source == ChoiceSource.USER:
            return ItemStatus.YOUR_ANSWER
        if a.source == ChoiceSource.FILE:
            return ItemStatus.FROM_YOUR_FILE
        return ItemStatus.AI_PROPOSAL

    def verify(self, answers: list[WizardAnswer], proposal: TargetProposal) -> TargetProposal:
        """Marque ce que le Registry confirme (entité, champs : type, obligatoire, libellé) ; le reste
        reste une proposition de l'IA. Rejouée à la génération de l'Excel : aucun « vérifié » ne vient du
        client. Cible format de fichier : la structure est RECONSTRUITE depuis le tableau de source (rien du
        client n'est repris)."""
        fmt = self._format(answers)
        if fmt is not None:
            data_type = next(
                (a.label for a in answers if a.topic == DATA_TYPE_TOPIC), fmt.table.data_types[0]
            )
            return format_structure(fmt, data_type)
        config = []
        for a in answers:
            status = self._answer_status(a)
            config.append(
                ConfigItem(
                    label=_topic_label(a.topic),
                    value=a.label,
                    verified=status == ItemStatus.VERIFIED_SCHEMA,
                    status=status,
                )
            )
        answered = {c.label.casefold() for c in config}
        config += [
            c.model_copy(update={"verified": False, "status": ItemStatus.AI_PROPOSAL})
            for c in proposal.configuration
            if c.label.casefold() not in answered
        ]
        fields = [
            f.model_copy(
                update={
                    "verified": False,
                    "allowed": [],
                    "reference": False,
                    "relation": None,
                    "computed_from": [],
                }
            )
            for f in proposal.fields
        ]
        notes = [n for n in proposal.notes if n != AI_NOTE]
        entity_verified = False
        reference = None
        snap = self._snapshot(answers)
        if snap is not None and proposal.entity:
            reference = self.registry.snapshot_label(snap)
            try:
                entity = self.registry.get_entity(snap.id, proposal.entity)
            except NotFound:
                entity = None
            if entity is not None:
                entity_verified = True
                known = {f.technical_name: f for f in entity.fields}
                relations = {
                    r.source_field.technical_name: r.target_entity_name
                    for r in self.registry.list_relations(entity.id)
                    if r.source_field is not None
                }
                fields = [_verified(f, known, relations) for f in fields]
                defaults = [
                    f.label for f in fields if f.verified and not f.required and known[f.name].required
                ]
                if defaults:
                    notes.append(
                        "Required by the target schema but usually filled by a default value of the target: "
                        + ", ".join(defaults)
                        + ". Check them if the import refuses a row."
                    )
        if entity_verified and all(f.verified for f in fields):
            level = Verification.VERIFIED
        elif entity_verified or any(f.verified for f in fields):
            level = Verification.PARTIAL
        else:
            level = Verification.AI
        if level != Verification.VERIFIED:
            notes.insert(0, AI_NOTE)
        rows = [r[: len(fields)] + [""] * (len(fields) - len(r)) for r in proposal.rows][:SAMPLE_ROWS]
        if len(rows) < SAMPLE_ROWS:
            notes.append(say("Only {n} sample [record was|records were] proposed.", len(rows)))
        return proposal.model_copy(
            update={
                "configuration": config,
                "fields": fields,
                "rows": rows,
                "entity_verified": entity_verified,
                "verification": level,
                "registry_reference": reference if entity_verified else None,
                "notes": notes,
            }
        )


DATA_TYPE_TOPIC = "data_type"  # sujet de la question qui choisit le type de données (prompt 1.7.0)
_DATA_TOPICS = ("business_object", "object", "domain", "entity", "data_kind", "kind_of_data")


def data_type_of(answers: list[WizardAnswer], proposal: TargetProposal) -> str:
    """Type de données affiché = réponse à la question du parcours qui l'a choisi : sujet `data_type`, sinon
    un sujet d'objet métier (jamais un SOUS-type ni une portée), à défaut le titre de la structure."""
    exact = next((a.label for a in answers if a.topic == DATA_TYPE_TOPIC), None)
    if exact:
        return exact
    for key in _DATA_TOPICS:
        for a in answers:
            if key in a.topic and not any(x in a.topic for x in ("subtype", "sub_type", "scope", "category")):
                return a.label
    return proposal.title


def _missing_items(raw: Any) -> dict[str, dict[str, Any]]:
    """Réponse brute du LLM → champ → {title, help, choices} ; forme invalide → WizardError."""
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), list):
        raise WizardError("The AI answer is not readable")
    return {
        str(item["name"]): item
        for item in raw["fields"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


# sortes de champ dont la valeur est propre à chaque ligne (jamais une valeur commune quand le fichier a des
# colonnes non importées) ; valeurs admises, oui / non et références restent des réglages communs possibles
PER_ROW_KINDS = frozenset({MissingKind.TEXT, MissingKind.NUMBER, MissingKind.DATE})


class _CachedColumns:
    """Réponses de l'IA par lot de colonnes reprises du cache (même fichier, même structure) : d'abord celui
    du processus, puis celui de PostgreSQL (partagé par les processus, survit aux redémarrages) ; l'aperçu et
    les retours arrière ne rappellent pas le LLM. Un échec n'est jamais gardé."""

    def __init__(self, proposer: WizardColumnProposer, session: Session | None = None) -> None:
        self.proposer = proposer
        self.session = session

    def wizard_columns(self, request: WizardColumnsRequest) -> ColumnProposalBatch:
        identity = getattr(self.proposer, "cache_identity", type(self.proposer).__name__)
        payload = json.dumps(["columns", identity, request.model_dump(mode="json")], sort_keys=True)
        key = hashlib.sha256(payload.encode()).hexdigest()
        hit = _cached(key)
        if isinstance(hit, ColumnProposalBatch):
            return hit
        stored = cached_batch(self.session, key) if self.session is not None else None
        if stored is not None:
            _remember(key, stored)
            return stored
        batch = self.proposer.wizard_columns(request)
        m = batch.meta
        log.info(
            "wizard columns: tokens in=%s out=%s cost_usd=%s latency_ms=%s error=%s",
            m.input_tokens,
            m.output_tokens,
            m.cost_usd,
            m.latency_ms,
            batch.error,
        )
        if batch.error is None:
            _remember(key, batch)
            if self.session is not None:
                store_batch(self.session, key, batch)
        return batch


def _verified(f: TargetField, known: dict[str, Any], relations: dict[str, str]) -> TargetField:
    """Champ confronté au schéma publié : nom exact → vérifié (libellé, type, obligatoire, longueur, valeurs
    admises du Registry). Nom « champ/attribut » (une colonne qui désigne un enregistrement par un attribut) :
    le champ de base existe, mais la colonne elle-même n'est pas vérifiée ; elle reste une référence."""
    exact = known.get(f.name)
    if exact is None:
        base = known.get(f.name.split("/", 1)[0]) if "/" in f.name else None
        if base is None:
            return f
        base_name = f.name.split("/", 1)[0]
        return f.model_copy(
            update={
                "required": f.required and bool(base.required),
                "reference": True,
                "relation": relations.get(base_name),
                "computed_from": _depends(base_name, base.metadata_ or {}),
            }
        )
    meta = exact.metadata_ or {}
    length = meta.get("length") or meta.get("size")
    selection = meta.get("selection")
    allowed = [
        AllowedValue(value=str(o["value"]), label=str(o.get("label") or o["value"]))
        for o in (selection if isinstance(selection, list) else [])
        if isinstance(o, dict) and o.get("value") not in (None, "")
    ]
    return f.model_copy(
        update={
            "verified": True,
            "label": exact.label or f.label,
            "type": exact.data_type or f.type,
            # obligatoire à compléter = le schéma ET la structure d'import le disent (un obligatoire du
            # schéma a souvent une valeur par défaut dans la cible, que le Registry ne connaît pas)
            "required": f.required and bool(exact.required),
            "length": length if isinstance(length, int) and length > 0 else f.length,
            "allowed": allowed,
            "reference": exact.data_type == "reference",
            "relation": relations.get(f.name),
            "computed_from": _depends(f.name, meta),
        }
    )


def _depends(name: str, meta: dict[str, Any]) -> list[str]:
    """Champs dont la cible calcule ce champ (Registry : `depends`, « a.b » → « a ») ; jamais lui-même."""
    raw = meta.get("depends")
    found = [str(d).split(".", 1)[0] for d in (raw if isinstance(raw, list) else [])]
    return [d for d in dict.fromkeys(found) if d and d != name]


def _version_key(v: str) -> tuple[int, str]:
    return (int(v), v) if v.isdigit() else (0, v)


def _is_format(system: Any, product: Any, version: Any) -> bool:
    """Produit du Registry qui est un FORMAT de fichier du système (autre que le système lui-même,
    interface de type fichier) : son nom et sa version parlent à l'utilisateur (« Buchungsstapel 13 »)."""
    return product.code != system.code and any(i.type == "file" for i in version.interfaces)


def _version_label(system: Any, product: Any, version: Any) -> str:
    """Libellé d'une version du Registry : le système lui-même → sa version (« 17 ») ; un format de
    fichier → son nom et sa version (« Buchungsstapel 13 ») ; l'API du système (autre produit, sans fichier) →
    ni le nom du produit ni la version de route ne parlent à l'utilisateur : le nom du système, ou la version
    s'il en a plusieurs."""
    if product.code == system.code:
        return version.version
    if _is_format(system, product, version):
        return f"{product.name} {version.version}"
    published = [v for v in product.versions if any(i.snapshots for i in v.interfaces)]
    return system.name if len(published) <= 1 else version.version


def _target_label(system_name: str, version_label: str) -> str:
    """Libellé d'une cible : le système et sa version, sans répéter le nom quand la version EST le nom."""
    if not version_label or version_label == system_name:
        return system_name
    return f"{system_name} {version_label}"
