/** Statuts d'un import et d'une colonne (tokens sémantiques de la charte). */
import { Pill, type Tone } from "@/components/status-badge";
import type { CatalogImport, ImportColumn, ImportRun } from "@/lib/api/supplier-hooks";

const IMPORT: Record<CatalogImport["status"], { label: string; tone: Tone }> = {
  UPLOADED: { label: "Choose a system", tone: "neutral" },
  ANALYZED: { label: "Needs review", tone: "warning" },
  READY: { label: "Ready for import", tone: "success" },
};

const COLUMN: Record<ImportColumn["status"], { label: string; tone: Tone }> = {
  MATCHED: { label: "Matched", tone: "success" },
  REVIEW: { label: "Review", tone: "warning" },
  UNMATCHED: { label: "Unmatched", tone: "danger" },
  IGNORED: { label: "Ignored", tone: "neutral" },
  EMPTY: { label: "Empty", tone: "neutral" },
};

const RUN: Record<ImportRun["status"], { label: string; tone: Tone }> = {
  RUNNING: { label: "Importing", tone: "info" },
  COMPLETED: { label: "Imported", tone: "success" },
  COMPLETED_WITH_WARNINGS: { label: "Imported with warnings", tone: "warning" },
  PARTIALLY_COMPLETED: { label: "Partially imported", tone: "warning" },
  FAILED: { label: "Import failed", tone: "danger" },
};

export function RunStatusPill({ status }: { status: ImportRun["status"] }) {
  return <Pill tone={RUN[status].tone}>{RUN[status].label}</Pill>;
}

export function ImportStatusPill({ status }: { status: CatalogImport["status"] }) {
  return <Pill tone={IMPORT[status].tone}>{IMPORT[status].label}</Pill>;
}

export function ColumnStatusPill({ status }: { status: ImportColumn["status"] }) {
  return <Pill tone={COLUMN[status].tone}>{COLUMN[status].label}</Pill>;
}
