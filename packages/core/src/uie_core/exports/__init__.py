"""Exports vers un format de fichier cible (PARTNER) : profil, attribution des comptes, aperçu, génération.

Aucune donnée de partenaire persistée : seulement les numéros attribués et un journal d'audit sans contenu.
"""

from uie_core.exports.documents import (
    DocumentClassification,
    DocumentContent,
    DocumentExportLedger,
    DocumentExportState,
    DocumentScope,
    ExportHistoryError,
    FieldChange,
    RecordOrigin,
    fingerprint,
)
from uie_core.exports.records import (
    ExportAccountAssignmentRecord,
    ExportDocumentRecord,
    ExportDocumentReleaseRecord,
    ExportProfileRecord,
    ExportRunRecord,
    ExportValueMappingRecord,
)
from uie_core.exports.service import (
    AccountPlan,
    ExportError,
    ExportNotReady,
    ExportPreview,
    ExportProfileService,
    ExportTarget,
    GeneratedFile,
    NotExported,
    PartnerExportService,
    PreviewRecord,
    ProfileInvalid,
    plan_accounts,
    validate_profile,
)

__all__ = [
    "fingerprint",
    "RecordOrigin",
    "FieldChange",
    "ExportHistoryError",
    "ExportDocumentReleaseRecord",
    "ExportDocumentRecord",
    "DocumentScope",
    "DocumentExportState",
    "DocumentExportLedger",
    "DocumentContent",
    "DocumentClassification",
    "AccountPlan",
    "ExportAccountAssignmentRecord",
    "ExportError",
    "ExportNotReady",
    "ExportPreview",
    "ExportProfileRecord",
    "ExportProfileService",
    "ExportRunRecord",
    "ExportTarget",
    "ExportValueMappingRecord",
    "GeneratedFile",
    "NotExported",
    "PartnerExportService",
    "PreviewRecord",
    "ProfileInvalid",
    "plan_accounts",
    "validate_profile",
]

# `export_document_records.send_item_id` référence une table de `uie_core.delivery` : elle doit être connue de
# la même metadata (import en fin de module, `uie_core.delivery` importe lui-même ce paquet).
from uie_core.delivery import records as _delivery_records  # noqa: E402, F401
