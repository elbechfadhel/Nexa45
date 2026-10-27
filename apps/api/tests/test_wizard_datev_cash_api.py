"""« Prepare data for DATEV » : livre de caisse (domaine 3) et règles communes du parcours (Supplier API).

Fichiers INVENTÉS (pièges de la vérité terrain du domaine 3). Cibles : specs DATEV générées localement : le
module est sauté si elles sont absentes. Rien n'est envoyé à DATEV."""

from __future__ import annotations

from typing import Any

from test_wizard_datev_api import (  # noqa: F401 - fixture et aides partagées
    BUCHUNGEN,
    _flow,
    pytestmark,
    world,
)
from uie_api import deps
from uie_core.domains import ProposalMeta
from uie_core.imports import ColumnProposal, ColumnProposalBatch
from uie_core.imports.proposer import ColumnDecision

# journal de factures dont la colonne du montant brut n'a pas un nom connu : seule l'IA la reconnaît
UNKNOWN_AMOUNT = (
    "Re-Nr.;Re-Datum;Kd-Nr.;Kunde;Netto €;USt %;Endsumme Kunde\n"
    "RE-1;05.07.2026;1001;Muster Bürotechnik GmbH;100,00;19;119,00\n"
)


class AmountLLM:
    """LLM simulé : reconnaît « Endsumme Kunde » comme montant brut, avec une confiance élevée."""

    provider = "fake"
    model = "fake"
    cache_identity = "fake-cash-amount"

    def wizard_columns(self, request: Any) -> ColumnProposalBatch:
        return ColumnProposalBatch(
            proposals=[
                ColumnProposal(
                    position=c.position,
                    decision=ColumnDecision.TARGET,
                    semantic_key="gross_amount",
                    confidence=0.97,
                )
                for c in request.columns
                if c.header == "Endsumme Kunde"
            ],
            meta=ProposalMeta(provider="fake", model="fake"),
        )


def test_an_amount_recognized_only_by_the_ai_is_to_confirm_never_chosen(world):  # noqa: F811
    """D9 : dans « Prepare data for DATEV », une colonne de MONTANT reconnue seulement par l'IA est « à
    confirmer » (jamais retenue d'office) ; les colonnes reconnues par leur nom restent sûres."""
    world["app"].dependency_overrides[deps.get_wizard_proposer] = lambda: AmountLLM()
    flow = _flow(world["client"], "Buchungsstapel 13", UNKNOWN_AMOUNT, "Rechnungen.csv")
    columns = {c["header"]: (c["status"], c["target"]) for c in flow["start"]["mapping"]["columns"]}
    assert columns["Endsumme Kunde"] == ("to_confirm", "gross_amount")
    assert columns["Netto €"] == ("matched", "net_amount")
    decided = {d["target"] for d in flow["start"]["decisions"]}
    assert "gross_amount" not in decided and "net_amount" in decided
