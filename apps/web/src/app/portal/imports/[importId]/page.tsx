"use client";

/** Rapport de compatibilité d'un fichier (V2c.1) : compteurs, puis une ligne par colonne (Source column / Target /
 *  Status). Décisions propres à CE fichier : Confirm (suggestion), Choose target (champs du catalogue du système),
 *  Ignore column, Ignore all unmatched columns (avec confirmation ; les colonnes restent, en Ignored). Colonnes
 *  vides (« Empty in this file ») : hors compteurs de correspondance, listées à part en bas. Toutes les colonnes Matched ou Ignored → « Ready for import ». L'import lui-même arrive à
 *  l'étape suivante : bouton désactivé. Aucune donnée technique (les cibles sont des libellés métier).
 *  Mémoire (V2c.2 / V2c.2b) : colonnes réglées d'office par les choix passés du fournisseur (mapping ou Ignore),
 *  signalées sous leur statut ; rien à décider → « Ready for import » + « No action needed ».
 *  Fallback AI (V2c.3) : « Matched by AI · 96% », « Suggested by AI · 96% » (Confirm / Choose target / Ignore),
 *  « Probably not needed · Suggested by AI · 97% » (jamais ignorée d'office) et, dès deux colonnes dans cet
 *  état, « Ignore all AI not relevant » (confirmation ; appris pour ce système, comme un Ignore).
 *  Filtre : un clic sur un compteur (Matched, To review…) n'affiche que ces colonnes ; un second clic l'enlève. */
import { CheckCircle2, Search } from "lucide-react";
import Link from "next/link";
import { use, useMemo, useState } from "react";

import { FormError } from "@/components/form";
import { PortalGate } from "@/components/portal";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  type CatalogImportDetail,
  type ImportColumn,
  useChooseTarget,
  useIgnoreAiNotRelevant,
  useIgnoreColumn,
  useIgnoreUnmatched,
  useImport,
  useImportTargets,
} from "@/lib/api/supplier-hooks";
import { cn } from "@/lib/utils";

import { ColumnStatusPill, ImportStatusPill } from "../status";

export default function ImportPage({ params }: { params: Promise<{ importId: string }> }) {
  const { importId } = use(params);
  return (
    <PortalGate>
      {() => (
        <div className="page-stack">
          <Report importId={importId} />
        </div>
      )}
    </PortalGate>
  );
}

type ColumnState = ImportColumn["status"];

function Report({ importId }: { importId: string }) {
  const detail = useImport(importId);
  const [filter, setFilter] = useState<ColumnState | null>(null);
  if (detail.error) return <ErrorBox error={detail.error} />;
  if (!detail.data) return <LoadingRows />;
  const d = detail.data;

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="hero-badge">{d.filename}</h1>
          <p className="page-description">
            {d.rows} rows · {d.columns} columns{d.system ? ` · checked against ${d.system.name}` : ""}
          </p>
        </div>
        <ImportStatusPill status={d.status} />
      </div>
      {d.status === "UPLOADED" ? (
        <Card size="sm" className="max-w-2xl">
          <CardContent className="space-y-3">
            <div className="item-title">Choose a system for this file</div>
            <p className="meta">We compare your columns with the catalog of one of your systems.</p>
            <Link href={`/portal/imports/new?import=${d.id}`} className={buttonVariants({ size: "sm" })}>
              Choose a system
            </Link>
          </CardContent>
        </Card>
      ) : (
        <>
          {d.status === "READY" ? (
            <Ready detail={d} />
          ) : (
            <Counts detail={d} filter={filter} onFilter={setFilter} />
          )}
          <Columns detail={d} filter={d.status === "READY" ? null : filter} onClear={() => setFilter(null)} />
        </>
      )}
    </>
  );
}

function Counts({
  detail,
  filter,
  onFilter,
}: {
  detail: CatalogImportDetail;
  filter: ColumnState | null;
  onFilter: (state: ColumnState | null) => void;
}) {
  const c = detail.counts;
  if (!c) return null;
  const items: { state: ColumnState; label: string; value: number; tone: string }[] = [
    { state: "MATCHED", label: "Matched", value: c.matched, tone: "text-success" },
    { state: "REVIEW", label: "To review", value: c.review, tone: "text-warning" },
    { state: "UNMATCHED", label: "Unmatched", value: c.unmatched, tone: "text-danger" },
    { state: "IGNORED", label: "Ignored", value: c.ignored, tone: "text-muted-foreground" },
    { state: "EMPTY", label: "Empty in this file", value: c.empty, tone: "text-muted-foreground" },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-4.5 md:grid-cols-5">
        {items.map((i) => (
          <button
            key={i.state}
            type="button"
            aria-pressed={filter === i.state}
            title={filter === i.state ? "Show all columns" : `Show only: ${i.label}`}
            onClick={() => onFilter(filter === i.state ? null : i.state)}
            className="rounded-[20px] text-left outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <Card size="sm" className={cn("h-full transition-colors hover:bg-muted/40", filter === i.state && "ring-2 ring-primary")}>
              <CardContent>
                <div className={cn("text-3xl font-semibold", i.tone)}>{i.value}</div>
                <div className="meta">{i.label}</div>
              </CardContent>
            </Card>
          </button>
        ))}
      </div>
      <AnalysisLine detail={detail} />
      <p className="meta">Review the highlighted columns: choose where each one goes, or ignore it.</p>
    </div>
  );
}

/** Ce que GO45 a compris seul au moment de l'analyse (règles, puis choix passés du fournisseur). */
function AnalysisLine({ detail }: { detail: CatalogImportDetail }) {
  const a = detail.analysis;
  if (!a) return null;
  const parts = [`${a.automatic} matched automatically`];
  if (a.learned) parts.push(`${a.learned} from your previous choices`);
  if (a.learned_ignored) parts.push(`${a.learned_ignored} ignored from your previous choices`);
  if (a.ai_matched) parts.push(`${a.ai_matched} resolved by AI`);
  if (a.ai_suggested) parts.push(`${a.ai_suggested} AI ${a.ai_suggested === 1 ? "suggestion" : "suggestions"}`);
  if (a.ai_not_relevant) parts.push(`${a.ai_not_relevant} probably not needed (AI)`);
  parts.push(`${a.needs_attention} needed your attention`);
  return <p className="meta">{parts.join(" · ")}</p>;
}

function Ready({ detail }: { detail: CatalogImportDetail }) {
  const kind = detail.system?.kind || "your system";
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="mt-0.5 size-6 shrink-0 text-success" />
          <div>
            <div className="item-title">Ready for import</div>
            <p className="meta">
              {detail.analysis?.needs_attention === 0 ? "No action needed. " : ""}This file is compatible with{" "}
              {detail.system?.name}.
            </p>
            <AnalysisLine detail={detail} />
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <div className="flex gap-2">
            <a href="#mapping" className={buttonVariants({ variant: "outline" })}>
              Review mapping
            </a>
            <Link href={`/portal/imports/${detail.id}/preview`} className={buttonVariants()}>
              Preview import
            </Link>
            <Link href={`/portal/imports/${detail.id}/preview`} className={buttonVariants({ variant: "outline" })}>
              Import to {kind}
            </Link>
          </div>
          <span className="meta">Review the preview, then import.</span>
        </div>
      </CardContent>
    </Card>
  );
}

function Columns({
  detail,
  filter,
  onClear,
}: {
  detail: CatalogImportDetail;
  filter: ColumnState | null;
  onClear: () => void;
}) {
  const choose = useChooseTarget(detail.id);
  const ignore = useIgnoreColumn(detail.id);
  const ignoreAll = useIgnoreUnmatched(detail.id);
  const ignoreAi = useIgnoreAiNotRelevant(detail.id);
  const [picking, setPicking] = useState<ImportColumn | null>(null);
  const busy = choose.isPending || ignore.isPending || ignoreAll.isPending || ignoreAi.isPending;
  const error = choose.error ?? ignore.error ?? ignoreAll.error ?? ignoreAi.error;
  const columns = detail.items.filter((c) => c.status !== "EMPTY");
  const empty = detail.items.filter((c) => c.status === "EMPTY");
  const unmatched = columns.filter((c) => c.status === "UNMATCHED").length;
  const notNeeded = columns.filter((c) => c.probably_not_needed).length;
  const shown = filter ? columns.filter((c) => c.status === filter) : columns;

  return (
    <Card id="mapping">
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle>Column mapping</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          {notNeeded > 1 && (
            <BulkIgnore
              label="Ignore all AI not relevant"
              question={`Ignore the ${notNeeded} columns AI marked as probably not needed?`}
              busy={busy}
              pending={ignoreAi.isPending}
              onConfirm={(done) => ignoreAi.mutate(undefined, { onSettled: done })}
            />
          )}
          {unmatched > 0 && (
            <BulkIgnore
              label="Ignore all unmatched columns"
              question={`Ignore the ${unmatched} unmatched ${unmatched === 1 ? "column" : "columns"}?`}
              busy={busy}
              pending={ignoreAll.isPending}
              onConfirm={(done) => ignoreAll.mutate(undefined, { onSettled: done })}
            />
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {error ? <FormError error={error} /> : null}
        {filter ? (
          <p className="meta">
            Showing {filter === "EMPTY" ? empty.length : shown.length} of {detail.items.length} columns ·{" "}
            <button type="button" className="underline hover:text-foreground" onClick={onClear}>
              Show all
            </button>
          </p>
        ) : null}
        {filter !== "EMPTY" && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source column</TableHead>
              <TableHead>Target</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.length === 0 ? (
              <TableRow>
                <TableCell colSpan={4} className="meta">
                  No columns in this state.
                </TableCell>
              </TableRow>
            ) : null}
            {shown.map((c) => (
              <TableRow key={c.position} className={cn(c.status === "IGNORED" && "opacity-60")}>
                <TableCell>
                  <div className="font-medium">{c.name}</div>
                  <div className="meta">
                    {c.content}
                    {c.unexpected_values > 0
                      ? ` · ${c.unexpected_values} unexpected ${c.unexpected_values === 1 ? "value" : "values"}`
                      : ""}
                  </div>
                </TableCell>
                <TableCell>
                  {c.target ? (
                    <>
                      <div>{c.target.label}</div>
                      <div className="meta">{c.target.group}</div>
                    </>
                  ) : (
                    <span className="meta">—</span>
                  )}
                  {c.message && c.status !== "IGNORED" && !c.probably_not_needed ? (
                    <div className="meta text-warning">{c.message}</div>
                  ) : null}
                </TableCell>
                <TableCell>
                  <ColumnStatusPill status={c.status} />
                  {c.decided_by_you && c.status !== "IGNORED" ? <div className="meta">Chosen by you</div> : null}
                  {c.source === "learned" && c.status === "MATCHED" ? (
                    <div className="meta">Learned from a previous mapping</div>
                  ) : null}
                  {c.source === "learned" && c.status === "IGNORED" ? (
                    <div className="meta">Ignored based on your previous choices</div>
                  ) : null}
                  {c.source === "ai" && c.confidence_pct !== null && c.status !== "IGNORED" ? (
                    <div className="meta">
                      {c.probably_not_needed ? "Probably not needed · " : ""}
                      {c.status === "MATCHED" ? "Matched" : "Suggested"} by AI · {c.confidence_pct}%
                    </div>
                  ) : null}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap justify-end gap-2">
                    {c.status === "REVIEW" && c.target ? (
                      <Button
                        size="sm"
                        disabled={busy}
                        onClick={() => choose.mutate({ position: c.position, targetId: c.target!.id })}
                      >
                        Confirm
                      </Button>
                    ) : null}
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => setPicking(c)}>
                      {c.status === "MATCHED" ? "Change target" : "Choose target"}
                    </Button>
                    {c.status !== "IGNORED" ? (
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => ignore.mutate(c.position)}>
                        Ignore column
                      </Button>
                    ) : null}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        )}
        {empty.length > 0 && (!filter || filter === "EMPTY") && (
          <div className="space-y-1.5 border-t border-border pt-4">
            <div className="flex items-center gap-2">
              <span className="font-medium">Empty in this file</span>
              <ColumnStatusPill status="EMPTY" />
            </div>
            <p className="meta">
              {empty.length} {empty.length === 1 ? "column has" : "columns have"} no values: nothing to import.
            </p>
            <p className="meta">{empty.map((c) => c.name).join(" · ")}</p>
          </div>
        )}
      </CardContent>
      <TargetDialog
        importId={detail.id}
        column={picking}
        taken={new Map(
          columns
            .filter((c) => c.target && c.status !== "IGNORED" && c.position !== picking?.position)
            .map((c) => [c.target!.id, c.name]),
        )}
        pending={choose.isPending}
        onCancel={() => setPicking(null)}
        onChoose={(targetId) =>
          picking && choose.mutate({ position: picking.position, targetId }, { onSuccess: () => setPicking(null) })
        }
      />
    </Card>
  );
}

function TargetDialog({
  importId,
  column,
  taken,
  pending,
  onCancel,
  onChoose,
}: {
  importId: string;
  column: ImportColumn | null;
  taken: Map<string, string>;
  pending: boolean;
  onCancel: () => void;
  onChoose: (targetId: string) => void;
}) {
  const targets = useImportTargets(importId, !!column);
  const [search, setSearch] = useState("");
  const groups = useMemo(() => {
    const q = search.trim().toLowerCase();
    const out = new Map<string, { id: string; label: string }[]>();
    for (const t of targets.data ?? []) {
      if (q && !t.label.toLowerCase().includes(q)) continue;
      out.set(t.group, [...(out.get(t.group) ?? []), t]);
    }
    return [...out.entries()];
  }, [targets.data, search]);

  return (
    <Dialog open={!!column} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        <DialogTitle>Choose target</DialogTitle>
        <DialogDescription>
          Where should the column “{column?.name}” go in your catalog? This choice applies to this file only.
        </DialogDescription>
        <div className="relative">
          <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search fields"
            className="pl-9"
          />
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          {targets.error ? <ErrorBox error={targets.error} /> : null}
          {!targets.data ? <LoadingRows rows={4} /> : null}
          {groups.map(([group, items]) => (
            <div key={group} className="space-y-1.5">
              <div className="eyebrow">{group}</div>
              {items.map((t) => {
                const usedBy = taken.get(t.id);
                const current = column?.target?.id === t.id;
                return (
                  <button
                    key={t.id}
                    type="button"
                    disabled={pending || !!usedBy}
                    onClick={() => onChoose(t.id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                      current ? "border-brand bg-brand/5" : "border-border hover:border-brand/60",
                      usedBy && "cursor-not-allowed opacity-50 hover:border-border",
                    )}
                  >
                    <span>{t.label}</span>
                    {usedBy ? <span className="meta">Used by “{usedBy}”</span> : null}
                  </button>
                );
              })}
            </div>
          ))}
          {targets.data && !groups.length ? <p className="meta">No field matches your search.</p> : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Action groupée avec confirmation (« Ignore all … ») : les colonnes restent, en Ignored. */
function BulkIgnore({
  label,
  question,
  busy,
  pending,
  onConfirm,
}: {
  label: string;
  question: string;
  busy: boolean;
  pending: boolean;
  onConfirm: (done: () => void) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirming(true)}>
        {label}
      </Button>
    );
  }
  return (
    <>
      <span className="text-sm">{question}</span>
      <Button size="sm" disabled={busy} onClick={() => onConfirm(() => setConfirming(false))}>
        {pending ? "Saving…" : "Yes, ignore them"}
      </Button>
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>
        Cancel
      </Button>
    </>
  );
}
