"""Modèles de questionnaire du Wizard (« templates ») : questions et réponses du questionnaire initial
(« Your answers »), sauvegardées par UTILISATEUR dans son organisation, pour reprendre une migration sans
refaire le questionnaire ni ses appels au LLM.

Un modèle ne contient JAMAIS de fichier, de correspondance, de valeur complétée ni aucune donnée du client :
la seule donnée est la liste des étapes (question posée telle quelle + réponse donnée), validée par les
modèles du Wizard. Filtrage unique par le contexte : le modèle d'un autre utilisateur (ou d'une autre
organisation) lève le même `NotFound` qu'un identifiant inexistant."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from uie_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from uie_core.identity import PermissionDenied, TenantContext
from uie_core.registry import NotFound, RegistryError
from uie_core.wizard.model import WizardAnswer, WizardQuestion

MAX_TEMPLATES = 100  # par utilisateur et par organisation
MAX_STEPS = 20  # même borne que les réponses du Wizard
NAME_MAX = 120

_UUID = UUID(as_uuid=True)


class WizardTemplateRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "wizard_templates"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "name", name="uq_wizard_templates_name"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX), nullable=False)
    # étapes du questionnaire : [{question, answer}] (modèles du Wizard) ; aucune donnée du client
    steps: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TemplateStep(_Model):
    question: WizardQuestion
    answer: WizardAnswer


class WizardTemplate(_Model):
    id: uuid.UUID
    name: str
    steps: list[TemplateStep]
    created_at: datetime
    updated_at: datetime


class TemplateConflict(RegistryError):
    """Nom déjà pris, ou trop de modèles (→ HTTP 409)."""


def _name(name: str) -> str:
    clean = " ".join(name.split())[:NAME_MAX]
    if not clean:
        raise TemplateConflict("Give the template a name")
    return clean


class WizardTemplateService:
    """Modèles de questionnaire de l'utilisateur du contexte (fournisseur seulement)."""

    def __init__(self, session: Session, ctx: TenantContext) -> None:
        if not isinstance(ctx, TenantContext):
            raise PermissionDenied("Templates belong to a supplier user")
        self.session, self.ctx = session, ctx

    def _query(self):  # noqa: ANN202
        return select(WizardTemplateRecord).where(
            WizardTemplateRecord.organization_id == self.ctx.organization_id,
            WizardTemplateRecord.user_id == self.ctx.user_id,
        )

    def _get(self, template_id: uuid.UUID) -> WizardTemplateRecord:
        record = self.session.scalars(self._query().where(WizardTemplateRecord.id == template_id)).first()
        if record is None:
            raise NotFound(f"Template {template_id} not found")
        return record

    def _taken(self, name: str, other_than: uuid.UUID | None = None) -> None:
        q = self._query().where(WizardTemplateRecord.name == name)
        if other_than is not None:
            q = q.where(WizardTemplateRecord.id != other_than)
        if self.session.scalars(q).first() is not None:
            raise TemplateConflict(f"A template named “{name}” already exists")

    @staticmethod
    def _out(r: WizardTemplateRecord) -> WizardTemplate:
        return WizardTemplate(
            id=r.id,
            name=r.name,
            steps=[TemplateStep.model_validate(s) for s in r.steps],
            created_at=r.created_at,
            updated_at=r.updated_at,
        )

    def list(self) -> list[WizardTemplate]:
        """Les plus récents d'abord."""
        rows = self.session.scalars(self._query().order_by(WizardTemplateRecord.updated_at.desc()))
        return [self._out(r) for r in rows]

    def get(self, template_id: uuid.UUID) -> WizardTemplate:
        return self._out(self._get(template_id))

    def create(self, name: str, steps: list[TemplateStep]) -> WizardTemplate:
        if not steps:
            raise TemplateConflict("Answer at least one question before saving a template")
        if len(steps) > MAX_STEPS:
            raise TemplateConflict(f"A template keeps at most {MAX_STEPS} answers")
        clean = _name(name)
        self._taken(clean)
        if len(self.session.scalars(self._query()).all()) >= MAX_TEMPLATES:
            raise TemplateConflict(f"At most {MAX_TEMPLATES} templates: delete one first")
        record = WizardTemplateRecord(
            organization_id=self.ctx.organization_id,
            user_id=self.ctx.user_id,
            name=clean,
            steps=[s.model_dump(mode="json") for s in steps],
        )
        self.session.add(record)
        self.session.flush()
        return self._out(record)

    def rename(self, template_id: uuid.UUID, name: str) -> WizardTemplate:
        record = self._get(template_id)
        clean = _name(name)
        self._taken(clean, other_than=record.id)
        record.name = clean
        record.updated_at = utcnow()
        self.session.flush()
        return self._out(record)

    def delete(self, template_id: uuid.UUID) -> None:
        self.session.delete(self._get(template_id))
        self.session.flush()


__all__ = [
    "MAX_STEPS",
    "MAX_TEMPLATES",
    "TemplateConflict",
    "TemplateStep",
    "WizardTemplate",
    "WizardTemplateRecord",
    "WizardTemplateService",
]
