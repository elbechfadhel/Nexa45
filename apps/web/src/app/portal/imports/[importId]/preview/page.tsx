"use client";

/** Preview d'import (V2d.1) : ce que GO45 essaierait d'importer depuis un fichier prêt, sans rien changer dans
 *  le système. Quatre sections : Summary, Data to import, Issues, Not imported (détails repliés). Aucun terme
 *  technique ; aucune promesse sur la façon dont l'import réel écrira ou rapprochera les données.
 *  V2d.2 : « Import to {système} » → confirmation (clé de requête générée à l'ouverture : un double clic ou un
 *  nouvel essai ne réimporte jamais) → page de résultat. Pendant l'appel : état honnête, aucun pourcentage. */
import { ArrowLeft, CheckCircle2, ShieldCheck, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";

import { PortalGate } from "@/components/portal";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { FormError } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type ImportPreview, useImportPreview, useRunImport } from "@/lib/api/supplier-hooks";
import { cn } from "@/lib/utils";

export default function ImportPreviewPage({ params }: { params: Promise<{ importId: string }> }) {
  const { importId } = use(params);
  return (
    <PortalGate>
      {() => (
        <div className="page-stack">
          <Preview importId={importId} />
        </div>
      )}
    </PortalGate>
  );
}

function Preview({ importId }: { importId: string }) {
  const preview = useImportPreview(importId);
  if (preview.error) return <ErrorBox error={preview.error} />;
  if (!preview.data) return <LoadingRows />;
  const p = preview.data;
  const kind = p.system?.kind || "your system";
  return (
    <>
      <div className="space-y-1.5">
        <h1 className="hero-badge">Import preview</h1>
        <p className="page-description">
          {p.filename}
          {p.system ? ` · ${p.system.name}` : ""}
        </p>
      </div>
      <Summary preview={p} kind={kind} />
      <DataToImport preview={p} />
      <Issues preview={p} />
      <NotImported preview={p} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link href={`/portal/imports/${importId}`} className="meta inline-flex items-center gap-1.5 hover:text-foreground">
          <ArrowLeft className="size-4" /> Back
        </Link>
        <ImportButton importId={importId} preview={p} kind={kind} />
      </div>
    </>
  );
}

function Summary({ preview: p, kind }: { preview: ImportPreview; kind: string }) {
  const blocked = p.rows - p.importable_rows;
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-start gap-3">
          {blocked ? (
            <TriangleAlert className="mt-0.5 size-6 shrink-0 text-warning" />
          ) : (
            <CheckCircle2 className="mt-0.5 size-6 shrink-0 text-success" />
          )}
          <div>
            <div className="item-title">
              {p.rows} {p.rows === 1 ? "product" : "products"} in this file
            </div>
            <p className="meta">
              {p.importable_rows} ready to import
              {blocked ? ` · ${blocked} cannot be imported` : ""}
              {p.rows_with_issues ? ` · ${p.rows_with_issues} with something to check` : ""}
            </p>
          </div>
        </div>
        <p className="meta inline-flex items-center gap-1.5">
          <ShieldCheck className="size-4 text-success" /> This is a preview: nothing is changed in {kind} until you
          import.
        </p>
      </CardContent>
    </Card>
  );
}

function DataToImport({ preview: p }: { preview: ImportPreview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Data to import</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Data</TableHead>
              <TableHead className="text-right">Values present</TableHead>
              <TableHead>Details</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {p.data.map((d) => (
              <TableRow key={`${d.section}:${d.label}`}>
                <TableCell>
                  <div className="font-medium">{d.label}</div>
                  <div className="meta">{d.section}</div>
                </TableCell>
                <TableCell className="text-right">
                  {d.rows_present} / {p.rows}
                  {d.rows_present > d.rows_valid ? (
                    <div className="meta text-warning">{d.rows_present - d.rows_valid} invalid</div>
                  ) : null}
                </TableCell>
                <TableCell className="meta">
                  {d.distinct_values !== null && d.distinct_values !== undefined
                    ? `${d.distinct_values} different ${d.distinct_values === 1 ? "value" : "values"}`
                    : ""}
                  {d.processed_during_import ? (
                    <div>Will be processed during import</div>
                  ) : null}
                  {d.not_imported_yet ? <div>Not imported yet</div> : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function Issues({ preview: p }: { preview: ImportPreview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Issues</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {p.issues.length === 0 ? (
          <p className="meta">Nothing to check.</p>
        ) : (
          p.issues.map((i) => (
            <div key={i.message} className="flex items-start gap-2">
              <TriangleAlert className={cn("mt-1 size-4 shrink-0", i.blocking ? "text-danger" : "text-warning")} />
              <div>
                <div>{i.message}</div>
                {i.rows.length ? (
                  <div className="meta">
                    {i.rows.length === 1 ? "Row" : "Rows"} {i.rows.join(", ")}
                    {i.count > i.rows.length ? ` and ${i.count - i.rows.length} more` : ""}
                  </div>
                ) : null}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function NotImported({ preview: p }: { preview: ImportPreview }) {
  const [open, setOpen] = useState(false);
  const n = p.not_imported;
  const groups = [
    { label: "Ignored", names: n.ignored },
    { label: "No matching field in your system", names: n.no_target },
    { label: "Empty in this file", names: n.empty },
    { label: "No longer available in your system (analyze the file again)", names: n.unavailable },
  ].filter((g) => g.names.length);
  const total = groups.reduce((sum, g) => sum + g.names.length, 0);
  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle>
          Not imported: {total} {total === 1 ? "column" : "columns"}
        </CardTitle>
        {total > 0 && (
          <Button size="sm" variant="outline" onClick={() => setOpen(!open)}>
            {open ? "Hide details" : "Show details"}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="meta">{groups.map((g) => `${g.names.length} ${g.label.toLowerCase()}`).join(" · ")}</p>
        {open &&
          groups.map((g) => (
            <div key={g.label} className="space-y-1">
              <div className="font-medium">
                {g.label} ({g.names.length})
              </div>
              <p className="meta">{g.names.join(" · ")}</p>
            </div>
          ))}
      </CardContent>
    </Card>
  );
}

/** Import réel : confirmation simple, clé de requête stable pour cette confirmation (idempotence côté serveur). */
function ImportButton({ importId, preview: p, kind }: { importId: string; preview: ImportPreview; kind: string }) {
  const router = useRouter();
  const run = useRunImport(importId);
  const [requestKey, setRequestKey] = useState<string | null>(null);
  const target = p.system?.name || kind;
  const notYet = p.data.filter((d) => d.not_imported_yet).map((d) => d.label);
  const confirm = () => {
    if (!requestKey) return;
    run.mutate(requestKey, {
      onSuccess: (result) => router.push(`/portal/imports/${importId}/runs/${result.id}`),
    });
  };
  return (
    <>
      <Button
        disabled={p.importable_rows === 0}
        onClick={() => setRequestKey(crypto.randomUUID().replaceAll("-", ""))}
      >
        Import to {kind}
      </Button>
      <Dialog open={requestKey !== null} onOpenChange={(open) => !open && !run.isPending && setRequestKey(null)}>
        <DialogContent>
          <DialogTitle>
            Import {p.rows} {p.rows === 1 ? "product" : "products"} to {target}?
          </DialogTitle>
          <DialogDescription>
            {p.rows} {p.rows === 1 ? "product" : "products"} will be processed.
          </DialogDescription>
          <div className="space-y-1.5">
            {p.rows_with_issues ? (
              <p className="meta">
                {p.rows_with_issues} {p.rows_with_issues === 1 ? "product contains" : "products contain"} non-blocking
                warnings.
              </p>
            ) : null}
            <p className="meta">Invalid values will be skipped where possible.</p>
            {notYet.length ? <p className="meta">Not imported yet: {notYet.join(", ")}.</p> : null}
          </div>
          {run.error ? <FormError error={run.error} /> : null}
          {run.isPending ? <p className="meta">Importing… this can take a moment. Please keep this page open.</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" disabled={run.isPending} onClick={() => setRequestKey(null)}>
              Cancel
            </Button>
            <Button disabled={run.isPending} onClick={confirm}>
              {run.isPending ? "Importing…" : `Import to ${kind}`}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
