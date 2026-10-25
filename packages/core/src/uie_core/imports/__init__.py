"""Import de fichiers catalogue (V2c.1) : compatibilité d'un fichier .xlsx / .csv avec le catalogue
d'un système.

UPLOAD → profilage (forme du fichier, aucune valeur retenue) → correspondance déterministe des colonnes avec
les cibles du plan de lecture V2a → rapport et décisions du fournisseur. Aucune écriture vers le système.
"""

from uie_core.imports.matching import (
    ColumnMatch,
    ColumnStatus,
    ImportTarget,
    MatchSource,
    ReviewReason,
    column_fits,
    header_key,
    import_targets,
    match_columns,
)
from uie_core.imports.profile import (
    ColumnProfile,
    ColumnType,
    FileProfile,
    ImportFileError,
    file_format,
    profile_file,
    read_table,
)
from uie_core.imports.proposer import (
    IMPORT_PROMPT,
    CandidateColumn,
    ColumnDecision,
    ColumnMappingProposer,
    ColumnProposal,
    ColumnProposalBatch,
    ColumnProposalRequest,
    TargetChoice,
    mask_example,
)
from uie_core.imports.service import CatalogImportService, ImportConflict, ImportView
from uie_core.imports.storage import FileStore, FileStoreError, LocalFileStore, default_file_store
from uie_core.imports.writer import (
    CatalogTarget,
    CatalogWriteError,
    CatalogWriter,
    CatalogWriterRegistry,
    CatalogWriterResolver,
    LabelResolution,
    LabelStatus,
    RecordId,
    RecordIds,
    WriteErrorKind,
    WriteValue,
)

__all__ = [
    "CatalogTarget",
    "CatalogWriteError",
    "CatalogWriter",
    "CatalogWriterRegistry",
    "CatalogWriterResolver",
    "LabelResolution",
    "LabelStatus",
    "RecordId",
    "RecordIds",
    "WriteErrorKind",
    "WriteValue",
    "IMPORT_PROMPT",
    "CandidateColumn",
    "ColumnDecision",
    "ColumnMappingProposer",
    "ColumnProposal",
    "ColumnProposalBatch",
    "ColumnProposalRequest",
    "TargetChoice",
    "mask_example",
    "CatalogImportService",
    "FileStore",
    "FileStoreError",
    "ImportConflict",
    "ImportView",
    "LocalFileStore",
    "default_file_store",
    "ColumnMatch",
    "ColumnProfile",
    "ColumnStatus",
    "ColumnType",
    "FileProfile",
    "ImportFileError",
    "ImportTarget",
    "MatchSource",
    "ReviewReason",
    "column_fits",
    "file_format",
    "header_key",
    "import_targets",
    "match_columns",
    "profile_file",
    "read_table",
]
