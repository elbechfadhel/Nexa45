"""DTO fournisseur de l'assistant de migration (AI Migration Wizard).

Aucune donnée technique : ni fournisseur ni modèle LLM, ni prompt, ni snapshot (seulement son libellé lisible
quand le Registry a vérifié la structure). `source` = registry | known | ai | user ; `verified` et
`status` viennent TOUJOURS du Core (Registry), jamais du client."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from uie_core.wizard import (
    ColumnStatus,
    FileMapping,
    Fill,
    MappingDecision,
    MissingInfo,
    PreparedFile,
    PreviousChoice,
    QuickQuestion,
    QuickStart,
    TargetProposal,
    WizardAnswer,
    WizardStep,
)
from uie_core.wizard.mapping import import_first_sheet
from uie_core.wizard.templates import TemplateStep, WizardTemplate

MATCHED_STATUSES = {ColumnStatus.MATCHED, ColumnStatus.AI_MATCH, ColumnStatus.PREVIOUS}
EXCLUDED_SHOWN = 20  # lignes non importées montrées dans l'aperçu (toutes dans le fichier préparé)
Source = Literal["registry", "known", "ai", "user"]
# réponse : aussi « file » (déduite du fichier par des règles, démarrage « fichier d'abord »)
AnswerSource = Literal["registry", "known", "ai", "user", "file"]
# statut montré : schéma vérifié (Registry), système seulement connu, proposition du LLM, réponse libre
Status = Literal["verified_schema", "known_system", "ai_proposal", "your_answer", "from_your_file"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WizardAnswerIn(_Out):
    question_id: str = Field(max_length=60)
    topic: str = Field(max_length=60)
    question: str = Field(max_length=240)
    value: str = Field(max_length=300)
    label: str = Field(max_length=300)
    source: AnswerSource = "ai"

    def core(self) -> WizardAnswer:
        return WizardAnswer.model_validate(self.model_dump())


class WizardAnswersIn(_Out):
    answers: list[WizardAnswerIn] = Field(default_factory=list, max_length=20)
    # simple : questions pour un utilisateur non technique ; expert : vocabulaire de l'éditeur
    mode: Literal["simple", "expert"] = "simple"

    def core(self) -> list[WizardAnswer]:
        return [a.core() for a in self.answers]


class WizardChoiceOut(_Out):
    value: str
    label: str
    description: str | None
    group: str | None
    source: Source


class WizardQuestionOut(_Out):
    id: str
    topic: str
    title: str
    help: str | None
    choices: list[WizardChoiceOut]
    allow_other: bool
    other_label: str | None


class WizardChoiceIn(_Out):
    value: str = Field(max_length=160)
    label: str = Field(max_length=160)
    description: str | None = Field(default=None, max_length=400)
    group: str | None = Field(default=None, max_length=60)
    source: Source = "ai"


class WizardQuestionIn(_Out):
    """Question telle que posée (rejouée par un modèle, jamais redemandée au LLM)."""

    id: str = Field(max_length=60)
    topic: str = Field(max_length=60)
    title: str = Field(max_length=240)
    help: str | None = Field(default=None, max_length=500)
    choices: list[WizardChoiceIn] = Field(min_length=1, max_length=40)
    allow_other: bool = True
    other_label: str | None = Field(default=None, max_length=60)


class TemplateStepIn(_Out):
    question: WizardQuestionIn
    answer: WizardAnswerIn


class WizardTemplateIn(_Out):
    """Modèle de questionnaire : questions et réponses SEULEMENT (ni fichier, ni mapping, ni donnée)."""

    name: str = Field(min_length=1, max_length=120)
    steps: list[TemplateStepIn] = Field(min_length=1, max_length=20)


class WizardTemplateRenameIn(_Out):
    name: str = Field(min_length=1, max_length=120)


class TemplateStepOut(_Out):
    question: WizardQuestionOut
    answer: WizardAnswerIn


class WizardTemplateOut(_Out):
    id: str
    name: str
    steps: list[TemplateStepOut]
    answered: int
    created_at: str
    updated_at: str


class WizardStepOut(_Out):
    done: bool
    question: WizardQuestionOut | None
    answered: int
    remaining: int


class ConfigItemOut(_Out):
    label: str
    value: str
    verified: bool
    status: Status = "ai_proposal"


class AllowedValueOut(_Out):
    value: str
    label: str


class TargetFieldOut(_Out):
    name: str
    label: str
    type: str
    length: int | None
    required: bool
    example: str | None
    description: str | None
    verified: bool
    allowed: list[AllowedValueOut] = []  # valeurs admises lues dans le Registry
    reference: bool = False  # identifiant d'un enregistrement existant de la cible
    computed_from: list[str] = []  # champs dont la cible calcule ce champ (Registry)


class TargetProposalOut(_Out):
    title: str
    summary: str
    entity: str | None
    entity_verified: bool
    template_name: str | None
    verification: Literal["verified", "partial", "ai"]
    registry_reference: str | None
    configuration: list[ConfigItemOut]
    fields: list[TargetFieldOut]
    rows: list[list[str]]
    notes: list[str]


class ProposalFieldIn(_Out):
    name: str = Field(max_length=120)
    label: str = Field(max_length=200)
    type: str = Field(max_length=60)
    length: int | None = None
    required: bool = False
    example: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=400)
    # renvoyé tel quel par le client : ignoré, toujours recalculé d'après le Registry (`verify`)
    computed_from: list[str] = Field(default_factory=list, max_length=20)


class ProposalIn(_Out):
    """Structure à mettre en Excel (renvoyée par le client) : les statuts « vérifié » sont RECALCULÉS."""

    title: str = Field(max_length=200)
    summary: str = Field(default="", max_length=1200)
    entity: str | None = Field(default=None, max_length=200)
    template_name: str | None = Field(default=None, max_length=200)
    configuration: list[ConfigItemOut] = Field(default_factory=list, max_length=40)
    fields: list[ProposalFieldIn] = Field(min_length=1, max_length=60)
    rows: list[list[str]] = Field(default_factory=list, max_length=5)
    notes: list[str] = Field(default_factory=list, max_length=20)


class WizardExcelIn(WizardAnswersIn):
    proposal: ProposalIn

    def core_proposal(self) -> TargetProposal:
        p = self.proposal
        return TargetProposal.model_validate(
            {
                "title": p.title,
                "summary": p.summary,
                "entity": p.entity,
                "template_name": p.template_name,
                "configuration": [c.model_dump() | {"verified": False} for c in p.configuration],
                "fields": [f.model_dump() for f in p.fields],
                "rows": [[str(c)[:200] for c in r][:60] for r in p.rows],
                "notes": [n[:400] for n in p.notes],
            }
        )


class WizardMappingDecisionIn(_Out):
    position: int = Field(ge=0, le=500)
    target: str | None = Field(default=None, max_length=120)  # None : colonne non importée


class PreviousChoiceIn(_Out):
    """Choix déjà retenu pour une colonne du même fichier (analyse précédente) : réappliqué."""

    header: str = Field(max_length=200)
    target: str | None = Field(default=None, max_length=120)  # None : « Not imported » choisi


class FillIn(_Out):
    """Réponse à « Complete missing information » : valeur commune et / ou valeurs par ligne."""

    field: str = Field(max_length=120)
    common: str | None = Field(default=None, max_length=300)
    per_row: dict[int, str] = Field(default_factory=dict, max_length=5000)
    confirmed: bool = False  # valeur non vérifiable : confirmation explicite de l'utilisateur
    hint: str | None = Field(default=None, max_length=120)  # réponse métier sans valeur écrite (signalée)
    unknown: bool = False  # « I don't know » : champ laissé vide et signalé
    auto: bool = False  # appliquée d'office d'après les réponses (jamais « complétée par l'utilisateur »)


class WizardEarlyIn(WizardExcelIn):
    """Questions prévisibles avant le fichier : structure + réponses déjà données (validées)."""

    fills: list[FillIn] = Field(default_factory=list, max_length=100)

    def core_fills(self) -> list[Fill]:
        return [Fill.model_validate(f.model_dump()) for f in self.fills]


class WizardFileIn(WizardExcelIn):
    """Partie JSON d'un envoi de fichier (le fichier lui-même est une pièce jointe, jamais stocké)."""

    decisions: list[WizardMappingDecisionIn] = Field(default_factory=list, max_length=500)
    fills: list[FillIn] = Field(default_factory=list, max_length=100)
    previous: list[PreviousChoiceIn] = Field(default_factory=list, max_length=500)
    # décisions sur les VALEURS : clé de question (« list:3 », « unit:9 », « currency »…) → option choisie
    choices: dict[str, str] = Field(default_factory=dict, max_length=200)
    # colonnes dont l'utilisateur a confirmé ou corrigé la correspondance (mémoire de l'organisation)
    confirmed: list[int] = Field(default_factory=list, max_length=500)

    def core_choices(self) -> dict[str, str]:
        return {k[:40]: v[:40] for k, v in self.choices.items()}

    def core_previous(self) -> list[PreviousChoice]:
        return [PreviousChoice(header=c.header, target=c.target) for c in self.previous]

    def core_fills(self) -> list[Fill]:
        return [Fill.model_validate(f.model_dump()) for f in self.fills]

    def core_decisions(self) -> list[MappingDecision]:
        return [MappingDecision(position=d.position, target=d.target) for d in self.decisions]


class ColumnMappingOut(_Out):
    position: int
    header: str
    filled: int
    examples: list[str]
    status: Literal["matched", "ai_match", "to_confirm", "unmatched", "empty", "previous"]
    target: str | None
    confidence: int | None
    probably_not_needed: bool


class FileMappingOut(_Out):
    file_name: str
    rows: int
    matched: int  # correspondances trouvées (nom exact ou IA sûre)
    to_confirm: int  # correspondances incertaines à confirmer
    unmatched: int
    ai_unavailable: bool = False  # l'IA n'a pas pu analyser les colonnes : relancer l'analyse
    columns: list[ColumnMappingOut]
    verification: Literal["verified", "partial", "ai"]


class FieldRefOut(_Out):
    name: str
    label: str


class AutoFilledOut(_Out):
    field: str
    label: str
    value: str
    explanation: str


class CheckOut(_Out):
    label: str
    ok: bool


class ValueIssueOut(_Out):
    """Valeurs NON écrites d'une colonne, groupées par sorte : comptées, avec des exemples."""

    field: FieldRefOut
    column: str
    kind: str
    count: int
    examples: list[str]
    message: str
    rows: list[int] = []  # lignes du fichier du client (1 = première ligne de données), au plus 200
    fix: str = ""  # solution simple
    severity: Literal["not_written", "to_check"] = "not_written"  # non écrite | écrite, à vérifier


class ValueTransformOut(_Out):
    """Transformation déterministe appliquée (même valeur, autre écriture), avec avant → après."""

    field: FieldRefOut
    column: str
    kind: str
    label: str
    count: int
    examples: list[list[str]]


class QuestionOptionOut(_Out):
    value: str
    label: str
    examples: list[list[str]] = []  # avant → après si cette option est choisie


class ValueQuestionOut(_Out):
    """Décision sur les valeurs : une fois par colonne et par sorte (devise : une fois par fichier)."""

    key: str
    kind: str
    field: FieldRefOut | None
    column: str | None
    title: str
    help: str
    count: int
    options: list[QuestionOptionOut]
    allow_other: bool = False
    suggested: str | None = None  # suggestion (règle ou IA) : jamais appliquée seule
    suggested_by: Literal["rule", "ai"] | None = None
    answer: str | None = None


class ExcludedRowOut(_Out):
    row: int
    reason: str
    kind: str = ""  # "currency" | "duplicate" : regroupement des motifs


class SetByTargetOut(_Out):
    field: FieldRefOut
    source: str  # libellés des champs dont la cible le calcule


class CreatedRecordsOut(_Out):
    """Enregistrements à créer AVANT les lignes (feuille « … (import first) » du fichier préparé)."""

    field: FieldRefOut  # champ des lignes qui les désigne (ex. la catégorie des articles)
    label: str
    count: int
    sheet: str  # nom de la feuille du fichier préparé


class CurrencyNoticeOut(_Out):
    target: str  # devise de la cible
    file: list[str]  # devises des lignes concernées, la plus fréquente d'abord
    rows: int  # lignes préparées sans leurs montants
    fields: list[str]  # libellés des montants non écrits
    from_system: bool  # devise lue dans votre système connecté (sinon : votre choix)
    note: str = ""  # ce que l'import de la cible écrira à la place (ex. son prix par défaut)


class PreparedFileOut(_Out):
    rows: int
    fields: list[FieldRefOut]  # colonnes du fichier préparé (nom technique + libellé)
    preview: list[list[str]]
    mapped: int
    missing_required: list[FieldRefOut]
    # obligatoires calculés par la cible d'après d'autres champs (Registry) : jamais demandés
    set_by_target: list[SetByTargetOut] = []
    verification: Literal["verified", "partial", "ai"]
    data_type: str = ""  # type de données choisi dans le parcours
    status: Literal["to_complete", "prepared", "ready"] = "prepared"  # préparé ≠ prêt à importer
    checks: list[CheckOut] = []  # contrôles réellement effectués
    auto_filled: list[AutoFilledOut] = []  # remplis d'office d'après les réponses du parcours
    completed: list[FieldRefOut] = []  # champs complétés par l'utilisateur
    warnings: list[str] = []  # valeurs saisies non vérifiables (avertissements conservés)
    not_imported: list[str] = []  # colonnes du fichier AVEC des valeurs envoyées dans aucun champ
    source_rows: int = 0  # lignes du fichier du client (rows = lignes écrites)
    value_issues: list[ValueIssueOut] = []
    value_transforms: list[ValueTransformOut] = []
    value_questions: list[ValueQuestionOut] = []
    converted_count: int = 0  # valeurs converties (unité) ; détail dans le fichier préparé
    currency_column: str | None = None
    currency: str | None = None
    excluded_count: int = 0  # lignes non importées (autre devise)
    excluded_rows: list[ExcludedRowOut] = []  # les premières, la liste complète est dans le fichier
    created_records: list[CreatedRecordsOut] = []  # à importer avant les lignes, dans cet ordre
    left_to_target: list[
        str
    ] = []  # libellés des champs laissés vides : la cible applique sa valeur par défaut
    # plus de la moitié des lignes SANS leurs montants (autre devise) : message en haut de la synthèse
    currency_notice: CurrencyNoticeOut | None = None
    delivery: Literal["sheet", "format"] = "sheet"  # format : fichier du format de la cible (ex. DATEV EXTF)
    notices: list[str] = []  # constats du format cible (enregistrements écrits, comptes réservés, période)


class MissingChoiceOut(_Out):
    value: str
    label: str
    description: str | None
    verified: bool


class FileColumnOut(_Out):
    """Colonne du fichier non importée, avec des valeurs (exemples courts)."""

    position: int
    header: str
    examples: list[str] = []


class MissingQuestionOut(_Out):
    field: str
    label: str
    kind: Literal["choice", "boolean", "reference", "number", "date", "text"]
    title: str
    help: str | None
    choices: list[MissingChoiceOut]
    free_text: bool
    warning: str | None
    optional: bool = False  # proposé seulement parce qu'il se déduit des réponses précédentes
    suggested: str | None = None  # valeur déduite des réponses précédentes (pré-sélectionnée, à confirmer)
    explanation: str | None = None  # réponse métier → valeur technique, en mots simples
    hints: list[str] = []  # réponses métier d'une référence non lisible (jamais un identifiant)
    hint_names: dict[str, str] = {}  # réponse métier → nom standard de la cible (à confirmer)
    same_as: str | None = None  # champ de la même sorte d'enregistrement : sa réponse est reprise
    # donnée propre à chaque ligne sans colonne : colonnes du fichier proposées (jamais une valeur unique)
    file_columns: list[FileColumnOut] = []
    allow_unknown: bool = False  # « I don't know »
    from_system: bool = False  # choix lus dans le système connecté
    previous_hint: str | None = (
        None  # réponse déjà donnée sans valeur écrite (gardée, jamais redemandée à vide)
    )
    previous_unknown: bool = False
    resolution: str | None = None  # comment résoudre un champ resté « à compléter »


class RowRefOut(_Out):
    index: int
    label: str


class MissingInfoOut(_Out):
    questions: list[MissingQuestionOut]
    rows: list[RowRefOut]
    row_count: int


class WizardQuickIn(_Out):
    """Démarrage par le fichier : cible (« instance:<id> », « registry:<système>[:<produit>:<version>] »,
    « known:<système> ») ; le fichier est la pièce jointe."""

    target: str = Field(max_length=200)
    mode: Literal["simple", "expert"] = "simple"
    # type de données choisi par l'utilisateur quand il n'a pas été reconnu (une des options rendues)
    data_type: str | None = Field(default=None, max_length=80)


class QuickTargetChoiceOut(_Out):
    """Cible proposée avant le dépôt du fichier ; `value` est renvoyée telle quelle à /quick-start."""

    value: str
    label: str  # système et version
    group: Literal["instance", "registry", "known"]
    detail: str | None = None  # nom de votre système connecté
    connected: bool = False
    system: str | None = None  # Registry : code du système (regroupement de ses formats)
    system_name: str | None = None
    format: str | None = None  # format et version quand le système en a plusieurs (« Buchungsstapel 13 »)
    product: str | None = None  # format du Registry (code)
    product_name: str | None = None  # nom du format quand le système en a plusieurs (« Buchungsstapel »)
    version: str | None = None
    latest: bool = False  # version la plus récente de ce format
    receives: list[str] = []  # types de données que la cible reçoit


class QuickTargetOut(_Out):
    """Système connecté choisi comme cible : nom, version, connexion, devise de la société (sans accès)."""

    name: str
    version: str
    connected: bool
    currency: str | None = None


class QuickQuestionOut(_Out):
    question: MissingQuestionOut
    default: str | None = None  # valeur présélectionnée, à confirmer
    default_from: (
        Literal[
            "your_answers",
            "your_system",
            "your_settings",
            "registry_first_value",
            "your_file",
            "usual_default",
        ]
        | None
    ) = None
    # décision sur les valeurs (clé de `choices`, ex. « currency ») ; None : valeur d'un champ (`fills`)
    decision: str | None = None


class QuickStartOut(_Out):
    target_label: str
    target: QuickTargetOut | None = None
    data_type: str
    data_type_from: Literal["rules", "ai", "user", "target"]
    structure_from: Literal["registry", "ai"]
    answers: list[WizardAnswerIn]  # à renvoyer aux étapes suivantes (aperçu, fichier préparé)
    proposal: TargetProposalOut
    mapping: FileMappingOut
    decisions: list[WizardMappingDecisionIn]
    choices: dict[str, str] = {}
    questions: list[QuickQuestionOut]  # au plus 3, chacune avec sa réponse présélectionnée
    # au-delà : appliquées avec leur réponse présélectionnée (déjà dans `choices` et `fills`), listées
    applied: list[QuickQuestionOut] = []
    fills: list[FillIn] = []
    more_questions: int = 0


def _quick_question(x: QuickQuestion) -> QuickQuestionOut:
    return QuickQuestionOut(
        question=MissingQuestionOut.model_validate(x.question.model_dump(mode="json")),
        default=x.default,
        default_from=x.default_from,  # type: ignore[arg-type]
        decision=x.decision,
    )


def quick_out(q: QuickStart) -> QuickStartOut:
    return QuickStartOut(
        target_label=q.target_label,
        target=QuickTargetOut(
            name=q.target.name,
            version=q.target.version,
            connected=q.target.connected,
            currency=q.target.currency,
        )
        if q.target
        else None,
        data_type=q.data_type,
        data_type_from=q.data_type_from,  # type: ignore[arg-type]
        structure_from=q.structure_from,  # type: ignore[arg-type]
        answers=[WizardAnswerIn.model_validate(a.model_dump(mode="json")) for a in q.answers],
        proposal=proposal_out(q.proposal),
        mapping=mapping_out(q.mapping, q.proposal.verification.value),
        decisions=[WizardMappingDecisionIn(position=d.position, target=d.target) for d in q.decisions],
        choices=q.choices,
        questions=[_quick_question(x) for x in q.questions],
        applied=[_quick_question(x) for x in q.applied],
        fills=[FillIn.model_validate(f.model_dump(mode="json")) for f in q.fills],
        more_questions=q.more_questions,
    )


def mapping_out(m: FileMapping, verification: str) -> FileMappingOut:
    return FileMappingOut(
        file_name=m.file_name,
        rows=m.rows,
        # un choix précédent « Not imported » n'est pas une correspondance
        matched=sum(1 for c in m.columns if c.target and c.status in MATCHED_STATUSES),
        to_confirm=m.count(ColumnStatus.TO_CONFIRM),
        unmatched=m.count(ColumnStatus.UNMATCHED),
        ai_unavailable=m.ai_unavailable,
        columns=[ColumnMappingOut.model_validate(c.model_dump(mode="json")) for c in m.columns],
        verification=verification,  # type: ignore[arg-type]
    )


def prepared_out(p: PreparedFile, proposal: TargetProposal) -> PreparedFileOut:
    labels = {f.name: f.label for f in proposal.fields}
    return PreparedFileOut(
        rows=p.rows,
        fields=[FieldRefOut(name=h, label=labels.get(h, h)) for h in p.headers],
        preview=p.preview,
        mapped=p.mapped,
        missing_required=[FieldRefOut(name=n, label=labels.get(n, n)) for n in p.missing_required],
        set_by_target=[
            SetByTargetOut(field=FieldRefOut(name=x["field"], label=x["label"]), source=x["source"])
            for x in p.set_by_target
        ],
        verification=p.verification.value,  # type: ignore[arg-type]
        data_type=p.data_type,
        status=p.status,  # type: ignore[arg-type]
        checks=[CheckOut(label=str(c["label"]), ok=bool(c["ok"])) for c in p.checks],
        auto_filled=[AutoFilledOut.model_validate(a) for a in p.auto_filled],
        completed=[FieldRefOut(name=n, label=labels.get(n, n)) for n in p.completed],
        warnings=p.warnings,
        not_imported=p.not_imported,
        source_rows=p.source_rows,
        value_issues=[
            ValueIssueOut(
                field=FieldRefOut(name=i.field, label=labels.get(i.field, i.field)),
                column=i.column,
                kind=i.kind,  # type: ignore[arg-type]
                count=i.count,
                examples=i.examples,
                message=i.message,
                rows=i.rows,
                fix=i.fix,
                severity=i.severity,  # type: ignore[arg-type]
            )
            for i in p.value_issues
        ],
        value_transforms=[
            ValueTransformOut(
                **t.model_dump(exclude={"field"}),
                field=FieldRefOut(name=t.field, label=labels.get(t.field, t.field)),
            )
            for t in p.value_transforms
        ],
        converted_count=len(p.value_conversions),
        value_questions=[
            ValueQuestionOut(
                **q.model_dump(exclude={"field", "options"}),
                field=FieldRefOut(name=q.field, label=labels.get(q.field, q.field)) if q.field else None,
                options=[QuestionOptionOut.model_validate(o.model_dump()) for o in q.options],
            )
            for q in p.value_questions
        ],
        currency_column=p.currency_column,
        currency=p.currency,
        delivery=p.delivery,  # type: ignore[arg-type]
        notices=p.notices,
        currency_notice=CurrencyNoticeOut.model_validate(p.currency_notice.model_dump())
        if p.currency_notice
        else None,
        excluded_count=len(p.excluded_rows),
        excluded_rows=[
            ExcludedRowOut(row=e.row, reason=e.reason, kind=e.kind) for e in p.excluded_rows[:EXCLUDED_SHOWN]
        ],
        created_records=[
            CreatedRecordsOut(
                field=FieldRefOut(name=c.field, label=labels.get(c.field, c.field)),
                label=c.label,
                count=len(c.rows),
                sheet=import_first_sheet(c),
            )
            for c in p.created_records
        ],
        left_to_target=p.left_to_target,
    )


def template_out(t: WizardTemplate) -> WizardTemplateOut:
    return WizardTemplateOut(
        id=str(t.id),
        name=t.name,
        steps=[TemplateStepOut.model_validate(s.model_dump(mode="json")) for s in t.steps],
        answered=len(t.steps),
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat(),
    )


def template_steps(body: WizardTemplateIn) -> list[TemplateStep]:
    return [TemplateStep.model_validate(s.model_dump()) for s in body.steps]


def missing_out(m: MissingInfo) -> MissingInfoOut:
    return MissingInfoOut.model_validate(m.model_dump(mode="json", exclude={"created"}))  # usage interne


def step_out(s: WizardStep) -> WizardStepOut:
    return WizardStepOut.model_validate(s.model_dump(mode="json"))


def proposal_out(p: TargetProposal) -> TargetProposalOut:
    data = p.model_dump(mode="json")
    for f in data["fields"]:
        f.pop("relation", None)  # entité liée : interne (recherche des valeurs dans le système connecté)
    return TargetProposalOut.model_validate(data)
