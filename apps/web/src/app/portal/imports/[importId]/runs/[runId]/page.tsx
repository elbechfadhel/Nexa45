"use client";

/** Résultat d'un import dans le système du fournisseur (V2d.2) : statut, compteurs, relations, avertissements et
 *  échecs par ligne, en langage simple. Aucun identifiant du système, modèle, champ ni message technique.
 *  Exécution interrompue (coupure) : « Continue import » reprend sans jamais réimporter une ligne déjà faite. */
import { CheckCircle2, CircleX, TriangleAlert } from "lucide-react";
import { use } from "react";

import { FormError } from "@/components/form";
import { PortalGate } from "@/components/portal";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type ImportRun, useImportRun, useResumeImportRun } from "@/lib/api/supplier-hooks";

import { RunStatusPill } from "../../../status";

const TITLE: Record<ImportRun["status"], string> = {
  RUNNING: "Import in progress",
  COMPLETED: "Import completed",
  COMPLETED_WITH_WARNINGS: "Import completed with warnings",
  PARTIALLY_COMPLETED: "Import partially completed",
  FAILED: "Import failed",
};

export default function ImportRunPage({ params }: { params: Promise<{ importId: string; runId: string }> }) {
  const { importId, runId } = use(params);
  return (
    <PortalGate>
      {() => (
        <div className="page-stack">
          <Result importId={importId} runId={runId} />
        </div>
      )}
    </PortalGate>
  );
}

function Result({ importId, runId }: { importId: string; runId: string }) {
  const run = useImportRun(importId, runId);
  const resume = useResumeImportRun(importId, runId);
  if (run.error) return <ErrorBox error={run.error} />;
  if (!run.data) return <LoadingRows />;
  const r = run.data;
  const ok = r.status === "COMPLETED" || r.status === "COMPLETED_WITH_WARNINGS";
  const imported = r.created + r.updated;
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="hero-badge">{TITLE[r.status]}</h1>
          <p className="page-description">{r.filename}</p>
        </div>
        <RunStatusPill status={r.status} />
      </div>
      <Card>
        <CardContent className="space-y-3">
          <div className="flex items-start gap-3">
            {r.status === "FAILED" ? (
              <CircleX className="mt-0.5 size-6 shrink-0 text-danger" />
            ) : ok && !r.with_warnings ? (
              <CheckCircle2 className="mt-0.5 size-6 shrink-0 text-success" />
            ) : (
              <TriangleAlert className="mt-0.5 size-6 shrink-0 text-warning" />
            )}
            <div>
              <div className="item-title">
                {r.rows} {r.rows === 1 ? "product" : "products"} processed · {imported} imported
              </div>
              <p className="meta">
                {r.created} created · {r.updated} updated
                {r.with_warnings ? ` · ${r.with_warnings} with warnings` : ""}
                {r.skipped ? ` · ${r.skipped} not imported` : ""}
                {r.failed ? ` · ${r.failed} failed` : ""}
                {r.pending ? ` · ${r.pending} not processed yet` : ""}
              </p>
            </div>
          </div>
          {r.error ? <p className="text-warning">{r.error}</p> : null}
          {r.pending > 0 ? (
            <div className="space-y-2">
              {resume.error ? <FormError error={resume.error} /> : null}
              <Button disabled={resume.isPending} onClick={() => resume.mutate()}>
                {resume.isPending ? "Continuing…" : "Continue import"}
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
      {r.relations.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Related values</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {r.relations.map((rel) => (
              <div key={rel.label}>
                <span className="font-medium">{rel.label}</span>
                <span className="meta">
                  {" "}
                  · {rel.values} {rel.values === 1 ? "value" : "values"} · {rel.existing} already in your system ·{" "}
                  {rel.created} created
                  {rel.ambiguous ? ` · ${rel.ambiguous} not set (several matches)` : ""}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}
      <Messages title="Not imported" tone="text-danger" items={r.failures} />
      <Messages title="Warnings" tone="text-warning" items={r.warnings} />
      {r.not_imported_yet.length ? (
        <p className="meta">Not imported yet in this version: {r.not_imported_yet.join(", ")}.</p>
      ) : null}
    </>
  );
}

function Messages({ title, tone, items }: { title: string; tone: string; items: ImportRun["warnings"] }) {
  if (!items.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {title} ({items.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        {items.map((m) => (
          <div key={`${m.row}:${m.message}`} className="flex gap-2">
            <span className={`meta w-16 shrink-0 ${tone}`}>Row {m.row}</span>
            <span>{m.message}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
