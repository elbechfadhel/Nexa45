"""uie_ai — implémentations du contrat `uie_core.domains.SemanticProposer` (Product Domain V1c).

Le Core ne connaît que le contrat ; les racines de composition (API, CLI) choisissent le proposer d'après
l'environnement (`build_proposer`). Sans configuration : NullProposer, le produit fonctionne sans LLM.
"""

from uie_ai.config import LlmSettings, build_proposer
from uie_ai.proposers import NullProposer, RecordingProposer, ReplayProposer

__all__ = ["LlmSettings", "NullProposer", "RecordingProposer", "ReplayProposer", "build_proposer"]
