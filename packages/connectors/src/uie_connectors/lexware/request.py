"""Forme de la requête d'un enregistrement pour l'API Lexware Office (JSON imbriqué).

Le constructeur générique du Core fait tout le travail à partir des métadonnées du Registry (`json_path`,
`presence`). Ce module ne fait que l'enregistrer pour ce système : il ne sait pas ce qu'est un partenaire,
ne connaît aucun modèle canonique ni aucune source ; il reçoit des valeurs déjà préparées et validées, par
champ du contrat.

La requête construite ici sert à l'aperçu ET à l'envoi (phase 1b : `sender.py` ajoute la version)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from uie_core.mapping import RecordRequestBuilder, RecordRequestBuilderRegistry
from uie_core.registry import FieldDef


@RecordRequestBuilderRegistry.register
class LexwareRequestBuilder(RecordRequestBuilder):
    system_code = "lexware"
    interface_code = "rest"

    def build(self, fields: list[FieldDef], values: dict[str, Any]) -> dict[str, Any]:
        # montants : nombres JSON (l'API n'accepte pas de texte) ; la précision est contrôlée en amont
        return _numbers(super().build(fields, values))


def _numbers(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_numbers(v) for v in value]
    return value
