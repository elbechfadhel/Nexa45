"use client";

/** Export DATEV Buchungsstapel (ACCOUNTING, phase 5 + D11) : suite naturelle de la préparation. « Preview DATEV
 *  file » montre le contenu EXACT du fichier (en-tête, lignes, pièces non exportées et pourquoi) et
 *  l'HISTORIQUE D'EXPORT de chaque pièce — New, Already exported, Changed since export — axe distinct du statut
 *  DATEV (Ready / Warning / Blocked). Par défaut seules les pièces nouvelles sont écrites : une pièce déjà
 *  exportée ne repart pas, une pièce modifiée depuis son export attend une décision explicite. « Download DATEV
 *  file » télécharge le contenu aperçu (refusé s'il a changé depuis). Aucune décision comptable ici ; fichier
 *  local : rien n'est envoyé à DATEV, le système source n'est pas modifié. */
import { Download, Loader2 } from "lucide-react";
import { useState } from "react";

import { ErrorBox, formatDate } from "@/components/registry";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import {
  changeLines,
  EXPORT_POLICY,
  exportState,
  fileSentence,
  fileSize,
  historySentence,
  nothingToExport,
  rowSummary,
  targetStatus,
} from "@/lib/accounting/preparation.mjs";
import {
  type AccountingFilePreview,
  type ExportDocumentState,
  useAccountingFilePreview,
  useAccountingFiles,
  useAccountingTargetSettings,
  useAdoptLegacyFile,
  useDownloadAccountingFile,
  useReleaseDocument,
} from "@/lib/api/supplier-hooks";

type T = { systemId: string; system: string; product: string };
type Query = { companyId: string; period: string; version: string };
type Counts = { documents: number; ready: number; warnings: number; blocked: number; not_prepared: number };

export function DatevFileExport({ t, query, counts }: { t: T; query: Query; counts: Counts }) {
  const settings = useAccountingTargetSettings(t);
  const preview = useAccountingFilePreview(t);
  const download = useDownloadAccountingFile(t);
  const release = useReleaseDocument(t);
  const adopt = useAdoptLegacyFile(t);
  const files = useAccountingFiles(t);
  const [duplicates, setDuplicates] = useState(false); // réécrire les pièces déjà exportées (choix explicite)
  const [understood, setUnderstood] = useState(false); // seconde confirmation avant le téléchargement
  const missing = settings.data?.missing_for_file ?? [];
  const exportable = counts.ready + counts.warnings;
  const file = preview.data;
  const run = (includeExported: boolean) => {
    download.reset();
    preview.mutate({ ...query, includeExported });
  };
  const nothing = file ? nothingToExport(file.history) : null;
  const canDownload = !!file && file.history.will_be_exported > 0 && (!file.include_exported || understood);

  return (
    <section className="space-y-4 border-t border-border pt-5">
      <div>
        <h4 className="font-semibold">DATEV Buchungsstapel export</h4>
        <p className="meta text-muted-foreground">
          {EXPORT_POLICY} A document already exported is not exported again; a document that changed since its export
          waits for your decision. The file is created for download only: nothing is sent to DATEV and your system is
          not modified.
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 @xl:grid-cols-4">
        <Fact label="Ready or warning" value={`${exportable} document${exportable === 1 ? "" : "s"}`} />
        <Fact label="With warnings" value={String(counts.warnings)} />
        <Fact label="Blocked" value={String(counts.blocked)} />
        <Fact label="Not prepared" value={String(counts.not_prepared)} />
      </dl>
      {missing.length > 0 && (
        <div className="rounded-md border border-warning/35 bg-warning/10 p-3 text-warning">
          Needed to create the file: {missing.join(", ")}. Choose it in the DATEV settings above, then Save.
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          disabled={missing.length > 0 || exportable === 0 || preview.isPending}
          onClick={() => run(duplicates)}
        >
          {preview.isPending && <Loader2 className="animate-spin" />} Preview DATEV file
        </Button>
        <Button
          disabled={!canDownload || download.isPending}
          onClick={() =>
            file &&
            download.mutate(
              {
                ...query,
                contentToken: file.content_token,
                includeExported: file.include_exported,
                confirmDuplicates: file.include_exported && understood,
              },
              { onSuccess: () => run(false) },
            )
          }
        >
          {download.isPending ? <Loader2 className="animate-spin" /> : <Download />}{" "}
          {file?.include_exported ? "Download again (duplicates)" : "Download DATEV file"}
        </Button>
        {!file && <span className="meta text-muted-foreground">Preview the file first, then download it.</span>}
      </div>
      {exportable === 0 && <p className="text-muted-foreground">No document can be exported for this period yet.</p>}
      {preview.error && <ErrorBox error={preview.error} />}
      {download.error && <ErrorBox error={download.error} />}
      {download.data && (
        <div className="rounded-md border border-success/35 bg-success/10 p-3 text-success">
          Downloaded <span className="tech">{download.data.name}</span> ({fileSize(download.data.size)}). Nothing was
          sent to DATEV. These documents are now marked as exported.
        </div>
      )}

      {file && (
        <History
          file={file}
          nothing={nothing}
          busy={release.isPending || adopt.isPending}
          error={release.error ?? adopt.error}
          onRelease={(d, revoke) =>
            release.mutate({ ref: d.ref, ...query, revoke }, { onSuccess: () => run(file.include_exported) })
          }
          onAdopt={(id) => adopt.mutate(id, { onSuccess: () => run(file.include_exported) })}
        />
      )}

      {file && file.history.already_exported > 0 && (
        <div className="space-y-2 rounded-card-sm border border-border p-3">
          <div className="font-semibold">Export again on purpose</div>
          <p className="meta text-muted-foreground">
            Only if you lost the file. The content of generated files is not kept, so an earlier file cannot be
            downloaded again. DATEV does not remove duplicates: importing the same bookings twice books them twice.
          </p>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={duplicates}
              onChange={(e) => {
                setDuplicates(e.target.checked);
                setUnderstood(false);
                run(e.target.checked);
              }}
            />
            <span>
              Include the {file.history.already_exported} already exported document
              {file.history.already_exported === 1 ? "" : "s"} in the file
            </span>
          </label>
          {file.include_exported && (
            <label className="flex items-start gap-2 text-warning">
              <input
                type="checkbox"
                className="mt-1"
                checked={understood}
                onChange={(e) => setUnderstood(e.target.checked)}
              />
              <span>
                I understand that {file.history.duplicates} document{file.history.duplicates === 1 ? "" : "s"} will be
                exported a second time and must not be imported twice.
              </span>
            </label>
          )}
        </div>
      )}

      {file && file.rows.length > 0 && <FilePreview file={file} />}
      {file && file.excluded.length > 0 && <NotExported file={file} />}
      {files.data && files.data.length > 0 && (
        <div className="space-y-2">
          <div className="meta text-muted-foreground">Generated files (the content is not kept)</div>
          <ul className="divide-y divide-border">
            {files.data.map((r) => (
              <li key={r.id} className="space-y-0.5 py-2">
                <div className="flex flex-wrap justify-between gap-x-4 gap-y-1">
                  <span>
                    {r.period ?? "—"} · {r.exported_documents ?? "—"} documents · {r.rows} rows
                  </span>
                  <span className="meta text-muted-foreground">
                    {formatDate(r.created_at)} · {fileSize(r.file_size)} · {r.encoding}
                  </span>
                </div>
                <div className="meta text-muted-foreground">
                  {r.tracked
                    ? [
                        `${r.new_documents ?? 0} new`,
                        `${r.already_exported_skipped ?? 0} already exported skipped`,
                        `${r.changed_blocked ?? 0} changed blocked`,
                        r.released_documents ? `${r.released_documents} re-exported after a change` : null,
                        r.duplicate_documents ? `${r.duplicate_documents} exported again on purpose` : null,
                        r.warnings ? `${r.warnings} with warnings` : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : "Generated before export tracking: its documents are not marked as exported"}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="meta text-muted-foreground">{label}</dt>
      <dd className="wrap-anywhere">{value}</dd>
    </div>
  );
}

function History({
  file,
  nothing,
  busy,
  error,
  onRelease,
  onAdopt,
}: {
  file: AccountingFilePreview;
  nothing: string | null;
  busy: boolean;
  error: unknown;
  onRelease: (d: ExportDocumentState, revoke: boolean) => void;
  onAdopt: (fileId: string) => void;
}) {
  const h = file.history;
  const [confirming, setConfirming] = useState<string | null>(null); // décision en deux temps, sans dialogue
  return (
    <div className="space-y-3 rounded-card-sm border border-border p-3">
      <div className="font-semibold">Export history of these documents</div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 @xl:grid-cols-4">
        <Fact label="New" value={String(h.new)} />
        <Fact label="Already exported" value={String(h.already_exported)} />
        <Fact label="Changed since export" value={String(h.changed_since_export)} />
        <Fact label="Will be exported" value={String(h.will_be_exported)} />
      </dl>
      <p className="meta text-muted-foreground">{historySentence(h)}</p>
      {nothing && <div className="rounded-md border border-info/35 bg-info/10 p-3 text-info">{nothing}</div>}
      {error ? <ErrorBox error={error} /> : null}

      {file.legacy_files.map((f) => (
        <div key={f.id} className="space-y-2 rounded-md border border-warning/35 bg-warning/10 p-3">
          <div className="text-warning">
            A file generated on {formatDate(f.created_at)}, before export tracking, contains {f.documents} of these
            documents. They are shown as new: if you imported that file, do not export them again.
          </div>
          {confirming === `adopt-${f.id}` ? (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={busy} onClick={() => onAdopt(f.id)}>
                Confirm: mark {f.documents} documents as already exported
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button size="sm" variant="outline" onClick={() => setConfirming(`adopt-${f.id}`)}>
              Mark them as already exported
            </Button>
          )}
          <p className="meta text-muted-foreground">
            Accepted only if these documents are still exactly what that file contained.
          </p>
        </div>
      ))}

      <ul className="divide-y divide-border">
        {file.documents.map((d) => {
          const state = exportState(d.state);
          return (
            <li key={d.ref} className="space-y-1 py-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  <span className="tech">{d.number ?? "—"}</span>
                  <span className="meta text-muted-foreground"> · {d.partner}</span>
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  {d.target_status === "warning" && <StatusBadge tone="warning">Warning</StatusBadge>}
                  <StatusBadge tone={state.tone}>{state.label}</StatusBadge>
                </span>
              </div>
              <div className="meta text-muted-foreground">
                {d.message} · {d.included ? "in this file" : "not in this file"}
              </div>
              {d.state === "changed_since_export" && (
                <div className="space-y-2">
                  <div className="meta">
                    Previous export: {d.previous_bookings ?? "—"} booking{d.previous_bookings === 1 ? "" : "s"} ·
                    current preparation: {d.bookings} booking{d.bookings === 1 ? "" : "s"}
                  </div>
                  {changeLines(d.changes).map((line) => (
                    <div key={line} className="text-danger">
                      {line}
                    </div>
                  ))}
                  {d.released ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-warning">You allowed a new export of this document.</span>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRelease(d, true)}>
                        Withdraw
                      </Button>
                    </div>
                  ) : confirming === d.ref ? (
                    <div className="space-y-2">
                      <p className="meta text-warning">
                        The earlier export stays in DATEV: correct or cancel it there yourself. Nothing is reversed
                        automatically.
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" disabled={busy} onClick={() => onRelease(d, false)}>
                          Confirm: allow a new export
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                          Keep blocked
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <Button size="sm" variant="outline" onClick={() => setConfirming(d.ref)}>
                      Allow new export
                    </Button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function FilePreview({ file }: { file: AccountingFilePreview }) {
  const [raw, setRaw] = useState<number | null>(null);
  const c = file.counts;
  return (
    <div className="space-y-4 rounded-card-sm border border-border p-3">
      <div>
        <div className="font-semibold">File preview</div>
        <p className="meta text-muted-foreground">
          {file.target} · {file.company} · {file.period} · {file.encoding} · {fileSize(file.size)}
        </p>
        <p>{fileSentence(c)}</p>
        <p className="meta text-muted-foreground">
          {c.source_documents} documents read · {c.columns} columns per row (DATEV format, version {file.version})
        </p>
      </div>
      {file.include_exported && (
        <div className="rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">
          This file contains {file.history.duplicates} document{file.history.duplicates === 1 ? "" : "s"} that were
          already exported. Importing it in DATEV books them a second time.
        </div>
      )}

      <details>
        <summary className="cursor-pointer font-semibold">File header</summary>
        <ul className="mt-2 space-y-1">
          {file.header.map((h) => (
            <li key={h.field} className="flex justify-between gap-3">
              <span className="text-muted-foreground">
                <span className="tech">{h.field}</span> {h.label}
              </span>
              <span className="tech wrap-anywhere">{h.value}</span>
            </li>
          ))}
        </ul>
        {file.changes_every_time.length > 0 && (
          <p className="meta mt-2 text-muted-foreground">
            Changes at every generation: {file.changes_every_time.join(", ")}. Everything else is identical for the same
            documents and settings.
          </p>
        )}
      </details>

      <div className="space-y-2">
        <div className="font-semibold">Booking rows ({c.rows})</div>
        <ul className="divide-y divide-border">
          {file.rows.map((r) => {
            const s = rowSummary(r);
            const status = targetStatus(r.status);
            return (
              <li key={r.row} className="space-y-1 py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    <span className="meta text-muted-foreground">Row {r.row} · </span>
                    <span className="tech">{r.document ?? "—"}</span>
                    <span className="meta text-muted-foreground"> · {r.partner}</span>
                  </span>
                  <span className="flex flex-wrap items-center gap-2">
                    {r.state !== "new" && (
                      <StatusBadge tone={r.state === "already_exported" ? "danger" : "warning"}>
                        {r.state === "already_exported" ? "Duplicate" : "Re-exported after a change"}
                      </StatusBadge>
                    )}
                    {r.status !== "ready" && <StatusBadge tone={status.tone}>{status.label}</StatusBadge>}
                  </span>
                </div>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-1 @xl:grid-cols-4">
                  <Fact label="Umsatz" value={s.amount} />
                  <Fact label="Soll/Haben" value={s.side} />
                  <Fact label="Konto" value={s.account} />
                  <Fact label="Gegenkonto" value={s.counter} />
                  <Fact label="BU-Schlüssel" value={s.taxKey} />
                  <Fact label="Belegdatum" value={s.date} />
                  <Fact label="Belegfeld 1" value={s.reference} />
                  <Fact label="Buchungstext" value={s.text} />
                </dl>
                <button
                  type="button"
                  className="meta cursor-pointer text-muted-foreground underline"
                  onClick={() => setRaw(raw === r.row ? null : r.row)}
                >
                  {raw === r.row ? "Hide raw DATEV fields" : "Raw DATEV fields"}
                </button>
                {raw === r.row && (
                  <ul className="space-y-1">
                    {r.cells.map((cell) => (
                      <li key={cell.field} className="flex justify-between gap-3">
                        <span className="text-muted-foreground">
                          <span className="tech">{cell.field}</span> {cell.label}
                        </span>
                        <span className="tech wrap-anywhere">{cell.value}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function NotExported({ file }: { file: AccountingFilePreview }) {
  return (
    <div className="space-y-2 rounded-card-sm border border-border p-3">
      <div className="font-semibold">Not exported ({file.excluded.length})</div>
      <ul className="divide-y divide-border">
        {file.excluded.map((e, i) => {
          const badge = e.state
            ? exportState(e.state)
            : e.target_status
              ? targetStatus(e.target_status)
              : { label: e.status, tone: "neutral" as const };
          return (
            <li key={`${e.number ?? e.reference ?? ""}-${i}`} className="space-y-1 py-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  <span className="tech">{e.number ?? "Not yet numbered"}</span>
                  <span className="meta text-muted-foreground">
                    {" "}
                    · {[e.type, e.reference].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
              </div>
              {e.reasons.map((reason) => (
                <div key={reason} className="meta text-muted-foreground wrap-anywhere">
                  {reason}
                </div>
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
