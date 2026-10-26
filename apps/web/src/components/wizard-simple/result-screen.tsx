"use client";

/** Écran 3 du mode Simple : la synthèse. En haut le résultat (articles prêts ou préparés, lignes écartées avec
 *  leur motif, valeurs non écrites), puis les décisions appliquées et les questions, les colonnes à confirmer et le
 *  téléchargement avec l'ordre d'import. Chaque changement met l'aperçu à jour (serveur). */
import { Loader2, RotateCcw } from "lucide-react";

import { type Tone, toneDot } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { PreparedFile, QuickStart } from "@/lib/api/wizard-hooks";
import { cn } from "@/lib/utils";
import { problemGroups, rowsText } from "@/lib/wizard/review.mjs";
import {
  alreadyExportedText,
  currencyNoticeText,
  type Edits,
  excludedGroups,
  nounOf,
  resultTitle,
} from "@/lib/wizard/simple.mjs";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

import { Columns } from "./columns";
import { Decisions } from "./decisions";
import { DownloadCard } from "./download-card";

type Issue = PreparedFile["value_issues"][number];

const STATUS_TONE: Record<PreparedFile["status"], Tone> = {
  ready: "success",
  prepared: "info",
  to_complete: "warning",
};

export function ResultScreen({
  start,
  preview,
  edits,
  onEdits,
  extra,
  onExtra,
  updating,
  updateError,
  downloading,
  downloadError,
  onDownload,
  onRestart,
}: {
  start: QuickStart;
  preview: PreparedFile;
  edits: Edits;
  onEdits: (e: Edits) => void;
  extra: Record<string, string>;
  onExtra: (e: Record<string, string>) => void;
  updating: boolean;
  updateError: Error | null;
  downloading: boolean;
  downloadError: Error | null;
  onDownload: () => void;
  onRestart: () => void;
}) {
  // rien de neuf : tout ce qui pouvait être écrit l'a déjà été (jamais « Your file is prepared »)
  const help = preview.nothing_new
    ? L.nothingNewHelp(nounOf(start.data_type))
    : preview.status === "ready"
      ? L.readyHelp
      : preview.status === "to_complete"
        ? L.toCompleteHelp
        : L.preparedHelp;
  const excluded = excludedGroups(preview.excluded_rows);
  const problems = problemGroups(preview.value_issues) as { notWritten: Issue[]; toCheck: Issue[] };
  const currencyNotice = currencyNoticeText(preview, start.data_type);

  return (
    <div className="space-y-6">
      <Card className="bg-transparent shadow-none">
        <CardContent className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <h2 className="item-title flex items-center gap-2">
                <span aria-hidden className={cn("size-2 shrink-0 rounded-full", toneDot(STATUS_TONE[preview.status]))} />
                {resultTitle(preview, start.data_type)}
              </h2>
              <p className="meta text-muted-foreground">
                {L.resultMeta(preview.source_rows || start.mapping.rows, start.mapping.file_name, start.target_label)}
              </p>
              <p>{help}</p>
            </div>
            {updating && (
              <span className="meta flex items-center gap-2 text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> {L.updating}
              </span>
            )}
          </div>
          {currencyNotice && (
            <p role="status" className="rounded-md border border-warning/40 bg-warning/10 p-3 font-medium text-warning">
              {currencyNotice}
            </p>
          )}
          {updateError && (
            <p className="rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">{updateError.message}</p>
          )}

          {preview.excluded_count > 0 && (
            <section className="space-y-2">
              <h3 className="eyebrow">
                {L.excludedHeading} · {L.excludedCount(preview.excluded_count)}
              </h3>
              <ul className="space-y-1.5">
                {excluded.map((g) =>
                  g.kind === "already_exported" ? (
                    <li key={g.kind}>
                      <span className="font-medium">{alreadyExportedText(preview, start.data_type)}</span>
                      <details className="meta mt-1">
                        <summary className="cursor-pointer text-muted-foreground">{L.alreadyExportedDetails}</summary>
                        <ul className="mt-1 space-y-1 pl-4">
                          {g.reasons.map((r) => (
                            <li key={`${r.row}-${r.reason}`}>
                              <span className="text-muted-foreground">{L.rowLabel(r.row)}</span> {r.reason}
                            </li>
                          ))}
                        </ul>
                      </details>
                    </li>
                  ) : (
                  <li key={g.kind}>
                    <span className="font-medium">{g.title}</span>
                    {g.reasons.length === 0 ? (
                      <span className="meta text-muted-foreground"> · {rowsText(g.rows, g.rows.length)}</span>
                    ) : (
                      <ul className="mt-1 space-y-1 pl-4">
                        {g.reasons.map((r) => (
                          <li key={`${r.row}-${r.reason}`} className="meta">
                            <span className="text-muted-foreground">{L.rowLabel(r.row)}</span> {r.reason}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                  ),
                )}
              </ul>
              {preview.excluded_count > preview.excluded_rows.length && (
                <p className="meta text-muted-foreground">
                  {L.excludedMore(preview.excluded_rows.length, preview.excluded_count)}
                </p>
              )}
            </section>
          )}

          <IssueList title={L.notWrittenHeading} help={L.notWrittenHelp} issues={problems.notWritten} />
          <IssueList title={L.toCheckHeading} issues={problems.toCheck} />

          {preview.value_transforms.length > 0 && (
            <section className="space-y-2">
              <div>
                <h3 className="eyebrow">{L.transformsHeading}</h3>
                <p className="meta text-muted-foreground">{L.transformsHelp}</p>
              </div>
              <ul className="space-y-1.5">
                {preview.value_transforms.map((t) => (
                  <li key={`${t.kind}-${t.column}`} className="space-y-0.5">
                    <p>
                      <span className="font-medium">{t.label}</span>
                      <span className="meta text-muted-foreground"> · {L.transformCount(t.count)}</span>
                    </p>
                    {t.examples.length > 0 && (
                      <p className="meta tech text-muted-foreground">
                        {t.examples
                          .slice(0, 3)
                          .map(([before, after]) => `${before} → ${after}`)
                          .join(", ")}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {preview.missing_required.length > 0 && (
            <section className="space-y-1">
              <h3 className="eyebrow">{L.missingHeading}</h3>
              <p>{L.missingText(preview.missing_required.map((f) => f.label).join(", "))}</p>
            </section>
          )}
        </CardContent>
      </Card>

      <Decisions start={start} preview={preview} edits={edits} onEdits={onEdits} extra={extra} onExtra={onExtra} />
      <Columns start={start} edits={edits} onEdits={onEdits} />
      <DownloadCard
        start={start}
        preview={preview}
        learning={edits.confirmed.length > 0}
        busy={downloading || updating}
        downloading={downloading}
        error={downloadError}
        onDownload={onDownload}
      />

      <div className="flex justify-start">
        <Button variant="ghost" onClick={onRestart}>
          <RotateCcw className="size-4" /> {L.startOver}
        </Button>
      </div>
    </div>
  );
}

function IssueList({ title, help, issues }: { title: string; help?: string; issues: Issue[] }) {
  if (issues.length === 0) return null;
  return (
    <section className="space-y-2">
      <div>
        <h3 className="eyebrow">{title}</h3>
        {help && <p className="meta text-muted-foreground">{help}</p>}
      </div>
      <ul className="space-y-3">
        {issues.map((i) => (
          <li key={`${i.kind}-${i.column}`} className="space-y-0.5">
            <p>{i.message}</p>
            {i.rows.length > 0 && <p className="meta text-muted-foreground">{rowsText(i.rows, i.count)}</p>}
            {i.fix && (
              <p className="meta">
                <span className="font-semibold">{L.fixLabel}:</span> {i.fix}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
