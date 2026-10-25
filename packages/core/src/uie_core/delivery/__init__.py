"""Envoi vers un système cible par son API : contrat de l'expéditeur, liens source ↔ cible, envois suivis."""

# Ordre de chargement : `uie_core.exports` d'abord (son service dépend des partenaires, qui dépendent de ce
# paquet) ; ainsi le cycle exports → partners → delivery se referme sur des modules déjà complets.
import uie_core.exports  # noqa: F401
from uie_core.delivery.contract import (
    RecordSender,
    RecordSenderError,
    RecordSenderRegistry,
    RecordSenderResolver,
    RemoteRecord,
    SendError,
    SendErrorKind,
    TargetChoice,
)
from uie_core.delivery.records import TargetResourceLink, TargetSendItem, TargetSendRun
from uie_core.delivery.service import (
    DeliveryCandidate,
    DeliveryConflict,
    DeliveryError,
    DeliveryScope,
    DeliveryService,
    ItemStatus,
    RunStatus,
)

__all__ = [
    "DeliveryCandidate",
    "DeliveryConflict",
    "DeliveryError",
    "DeliveryScope",
    "DeliveryService",
    "ItemStatus",
    "RecordSender",
    "RecordSenderError",
    "RecordSenderRegistry",
    "RecordSenderResolver",
    "RemoteRecord",
    "RunStatus",
    "SendError",
    "SendErrorKind",
    "TargetChoice",
    "TargetResourceLink",
    "TargetSendItem",
    "TargetSendRun",
]
