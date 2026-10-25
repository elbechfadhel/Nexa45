"""Sources à schéma fixe : contrat de lecture (connecteur) et binding de source (données). Aucun nom de
système."""

from uie_core.sources.binding import (
    Join,
    LabelSource,
    SourceBinding,
    SourceBindingError,
    SourceBindingRegistry,
    SourceEntity,
    SourceField,
    SourceItems,
    SourceRef,
    Unmapped,
    technical_name,
    value_at,
)
from uie_core.sources.contract import (
    RecordBatch,
    RecordPeriod,
    RecordSource,
    RecordSourceError,
    RecordSourceReader,
    RecordSourceRegistry,
    RecordSourceResolver,
)

__all__ = [
    "Join",
    "LabelSource",
    "RecordBatch",
    "RecordPeriod",
    "RecordSource",
    "RecordSourceError",
    "RecordSourceReader",
    "RecordSourceRegistry",
    "RecordSourceResolver",
    "SourceBinding",
    "SourceBindingError",
    "SourceBindingRegistry",
    "SourceEntity",
    "SourceField",
    "SourceItems",
    "SourceRef",
    "Unmapped",
    "technical_name",
    "value_at",
]
