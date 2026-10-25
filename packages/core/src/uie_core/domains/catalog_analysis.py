"""Analyze côté fournisseur (V2b.2) : UNE orchestration au-dessus du moteur V1, aucun nouveau moteur.

    1. InstanceService.test_connection          « Connection checked »
    2. InstanceService.discover_schema           « Reading product structure »   (toujours : détecte les
                                                 changements ; nouveau snapshot immutable — F2)
    3. ProductDomainService.analyze              domaine + règles + vocabulaire + décisions réappliquées
    4. SemanticEnrichmentService.enrich_with_llm « Identifying catalog fields » — seulement si un fournisseur
                                                 LLM est configuré ET qu'il reste des candidats ambigus (F1) ;
                                                 le cache V1c (même champ, même prompt) évite les appels
    5. résumé (questions éventuelles, « Catalog analysis complete »)

`ProductDomainService.analyze` reste sans LLM (règle V1c) : l'IA est une étape explicite de l'action
« Analyze » du propriétaire du système. Contexte obligatoire ; instance d'un autre tenant = NotFound avant
toute connexion.
Aucune synchronisation du catalogue ici (F3 : c'est l'étape distincte « Sync » du catalogue V2a).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from uie_core.domains.ai_enrichment import AiEnrichmentResult
from uie_core.domains.enrichment import CatalogAnalysisSummary, SemanticEnrichmentService
from uie_core.identity.context import Context, ensure_context
from uie_core.instances.discovery import DiscoveryError
from uie_core.instances.service import InstanceService
from uie_core.registry.service import NotFound, RegistryError

log = logging.getLogger(__name__)


class CatalogAnalysisFailed(RegistryError):
    """Analyse impossible (connexion, lecture de la structure...). Message technique (journaux, Admin) ;
    le fournisseur ne voit qu'une phrase simple."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step


@dataclass(frozen=True)
class CatalogAnalysisRun:
    snapshot_sequence: int
    ai: AiEnrichmentResult | None  # None : IA non configurée ou aucun candidat (aucun appel)
    summary: CatalogAnalysisSummary


class CatalogAnalysisService:
    def __init__(
        self, ctx: Context, instances: InstanceService, enrichment: SemanticEnrichmentService
    ) -> None:
        self.ctx = ensure_context(ctx)
        if instances.ctx != self.ctx or enrichment.ctx != self.ctx:
            raise ValueError("Les services de l'analyse doivent partager le même contexte")
        self.instances = instances
        self.enrichment = enrichment

    def analyze(self, instance_id: uuid.UUID) -> CatalogAnalysisRun:
        inst = self.instances.get(instance_id)  # contrôle d'appartenance AVANT toute connexion
        result = self.instances.test_connection(inst.id)
        if not result.ok:
            raise CatalogAnalysisFailed("connection", result.error or "connection failed")
        try:
            snap = self.instances.discover_schema(inst.id)
            self.enrichment.domains.analyze(inst.id)
        except NotFound:
            raise
        except (DiscoveryError, RegistryError) as exc:
            log.warning("analyse du catalogue impossible instance=%s : %s", inst.id, exc)
            raise CatalogAnalysisFailed("structure", str(exc)) from None
        ai = None
        if self.enrichment.proposer.provider != "none" and self.enrichment.ai_status(inst.id).candidates > 0:
            ai = self.enrichment.enrich_with_llm(inst.id)
        return CatalogAnalysisRun(
            snapshot_sequence=snap.sequence, ai=ai, summary=self.enrichment.summary(inst.id)
        )
