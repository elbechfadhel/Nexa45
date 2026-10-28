"""Préparation d'une démonstration « Prepare data for DATEV » : une liste de fichiers exportés dans
l'ordre, par le MÊME chemin que la page (démarrage par le fichier → aperçu → téléchargement : numéros de
compte réservés, pièces journalisées, registre des factures ouvertes rempli), avec des réponses DÉCLARÉES.

Une question posée par la page sans réponse déclarée arrête tout (`DemoQuestionUnanswered`) : rien n'est
deviné, pas même la présélection d'une question bloquante. Les décisions « appliquées » (Decided for you)
gardent leur présélection, comme sur la page sans clic. L'appelant décide de la transaction (tout ou rien)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field

from uie_core.wizard.mapping import PreparedFile
from uie_core.wizard.service import REGISTRY_PREFIX, MigrationWizardService

MAX_ROUNDS = 4  # aperçus successifs : une réponse peut faire apparaître une autre question de valeur


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DemoFile(_Model):
    file: str  # nom du fichier (lu par l'appelant)
    target: str = Field(pattern=r"^[a-z0-9_-]+/[a-z0-9_-]+/[A-Za-z0-9._-]+$")  # système/format/version
    answers: dict[str, str] = {}  # réponses propres à ce fichier (priment sur les réponses communes)


class DemoPlan(_Model):
    """Fichier de réponses : réponses communes (clé de décision → valeur), fichiers dans l'ordre d'export."""

    answers: dict[str, str] = {}
    files: list[DemoFile] = Field(min_length=1)


class DemoQuestionUnanswered(Exception):  # noqa: N818 — c'est un arrêt voulu, pas une erreur du code
    def __init__(self, file: str, key: str, title: str, options: list[str]) -> None:
        self.file, self.key, self.title, self.options = file, key, title, options
        choices = f" (options: {', '.join(options)})" if options else ""
        super().__init__(
            f"{file}: question « {title} » has no answer in the answers file: add `{key}`{choices}."
        )


class DemoTargetUnknown(Exception):  # noqa: N818
    pass


@dataclass
class DemoFileResult:
    file: str
    target_label: str
    data_type: str
    summary: PreparedFile
    content: bytes
    filename: str
    answered: dict[str, str] = field(default_factory=dict)  # réponses du fichier de réponses utilisées


def prepare_demo(
    wizard: MigrationWizardService, plan: DemoPlan, data: dict[str, bytes]
) -> list[DemoFileResult]:
    """Exporte chaque fichier du plan dans l'ordre (`data` : nom → contenu). Arrêt à la première question sans
    réponse déclarée, ou au premier refus du format ; ce qui précède n'est validé que par l'appelant."""
    targets = {t.value for t in wizard.quick_targets()}
    return [
        _one(wizard, spec, data[spec.file], {**plan.answers, **spec.answers}, targets) for spec in plan.files
    ]


def _one(
    wizard: MigrationWizardService, spec: DemoFile, data: bytes, answers: dict[str, str], targets: set[str]
) -> DemoFileResult:
    value = REGISTRY_PREFIX + spec.target.replace("/", ":")
    if value not in targets:
        raise DemoTargetUnknown(
            f"{spec.file}: target {spec.target} is not a published format of the Registry."
        )
    name = PurePath(spec.file).name  # le nom du fichier, comme déposé sur la page (sans son dossier)
    start = wizard.quick_start(value, data, name, "simple")
    used: dict[str, str] = {}
    choices = dict(start.choices)
    for q in start.questions:  # « Your decisions » : jamais la présélection, toujours une réponse déclarée
        if q.decision is None or q.decision not in answers:
            raise DemoQuestionUnanswered(
                spec.file,
                q.decision or q.question.field,
                q.question.title,
                [c.value for c in q.question.choices],
            )
        choices[q.decision] = used[q.decision] = answers[q.decision]
    for q in start.applied:  # « Decided for you » : présélection gardée, sauf réponse déclarée
        if q.decision is not None and q.decision in answers:
            choices[q.decision] = used[q.decision] = answers[q.decision]
    proposal = wizard.verify(start.answers, start.proposal)
    for _ in range(MAX_ROUNDS):
        summary, _, _ = wizard.prepare_file(
            start.answers, proposal, data, name, start.decisions, start.fills, choices
        )
        open_ = [q for q in summary.value_questions if q.answer is None]
        if not open_:
            break
        for q in open_:
            if q.key not in answers:
                raise DemoQuestionUnanswered(spec.file, q.key, q.title, [o.value for o in q.options])
            choices[q.key] = used[q.key] = answers[q.key]
    summary, content, filename = wizard.prepare_file(
        start.answers, proposal, data, name, start.decisions, start.fills, choices, generate=True
    )
    return DemoFileResult(spec.file, start.target_label, start.data_type, summary, content, filename, used)


__all__ = [
    "DemoFile",
    "DemoFileResult",
    "DemoPlan",
    "DemoQuestionUnanswered",
    "DemoTargetUnknown",
    "prepare_demo",
]
