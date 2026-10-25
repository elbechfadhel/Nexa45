"use client";

/** Imports (V2c.1) : les fichiers catalogue du fournisseur et leur compatibilité avec un de ses systèmes. */
import { Plus } from "lucide-react";
import Link from "next/link";

import { PortalGate } from "@/components/portal";
import { ErrorBox, formatDate, LoadingRows } from "@/components/registry";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useImports } from "@/lib/api/supplier-hooks";

import { ImportStatusPill, RunStatusPill } from "./status";

export default function ImportsPage() {
  return (
    <PortalGate>
      {() => (
        <div className="page-stack">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1.5">
              <h1 className="hero-badge">Imports</h1>
              <p className="page-description">Check that your product files fit your system before importing them.</p>
            </div>
            <Link href="/portal/imports/new" className={buttonVariants()}>
              <Plus /> New import
            </Link>
          </div>
          <ImportList />
        </div>
      )}
    </PortalGate>
  );
}

function ImportList() {
  const imports = useImports();
  if (imports.error) return <ErrorBox error={imports.error} />;
  if (!imports.data) return <LoadingRows />;
  if (!imports.data.length) {
    return (
      <Card size="sm" className="max-w-2xl">
        <CardContent className="space-y-1">
          <div className="item-title">No file yet</div>
          <p className="meta">Upload an .xlsx or .csv file to check it against your system.</p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead>System</TableHead>
              <TableHead>Rows</TableHead>
              <TableHead>Columns</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Uploaded</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {imports.data.map((i) => (
              <TableRow key={i.id}>
                <TableCell>
                  <Link href={`/portal/imports/${i.id}`} className="font-medium hover:underline">
                    {i.filename}
                  </Link>
                </TableCell>
                <TableCell>{i.system?.name ?? <span className="meta">Not chosen yet</span>}</TableCell>
                <TableCell>{i.rows}</TableCell>
                <TableCell>
                  {i.columns}
                  {i.counts && (
                    <span className="meta ml-2">
                      {i.counts.matched} matched
                      {i.counts.review ? ` · ${i.counts.review} to review` : ""}
                      {i.counts.unmatched ? ` · ${i.counts.unmatched} unmatched` : ""}
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  <ImportStatusPill status={i.status} />
                  {i.last_run && (
                    <Link
                      href={`/portal/imports/${i.id}/runs/${i.last_run.id}`}
                      className="mt-1 block w-fit hover:opacity-80"
                    >
                      <RunStatusPill status={i.last_run.status} />
                    </Link>
                  )}
                  {i.status === "READY" && (
                    <Link
                      href={`/portal/imports/${i.id}/preview`}
                      className="meta mt-1 block hover:text-foreground hover:underline"
                    >
                      Preview import
                    </Link>
                  )}
                </TableCell>
                <TableCell className="meta">{formatDate(i.created_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
