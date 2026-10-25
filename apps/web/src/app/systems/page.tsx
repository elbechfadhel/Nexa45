"use client";

import Link from "next/link";

import { ErrorBox, formatDate, formatInt, LoadingRows } from "@/components/registry";
import { SystemLogo } from "@/components/system-logo";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useSystems } from "@/lib/api/hooks";

/** Versions dans l'ordre numérique (« 5 », « 10 » et non « 10 », « 5 »). */
const sortVersions = <T extends { version: string }>(versions: T[]) =>
  [...versions].sort((a, b) => a.version.localeCompare(b.version, undefined, { numeric: true }));

export default function SystemsPage() {
  const { data, error, isPending } = useSystems();

  return (
    <div className="page-stack">
      <div className="space-y-1.5">
        <h1 className="hero-badge">Systems</h1>
        <p className="page-description">
          System → Format → Version → Interface → published snapshots (immutable).
        </p>
      </div>

      {error && <ErrorBox error={error} />}
      {isPending && <LoadingRows />}
      {data?.length === 0 && (
        <p className="meta">The Registry is empty. Import a system with `uie registry import odoo ...`.</p>
      )}

      {data?.map((sys) => (
        <Card key={sys.id}>
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-3">
              <SystemLogo code={sys.code} name={sys.name} className="size-11" />
              <span>{sys.name}</span>
              <span className="tech font-normal text-muted-foreground">{sys.code}</span>
              <Badge variant="outline">{sys.category}</Badge>
              <Badge variant="secondary">{sys.status}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {sys.products.map((prod) => (
              <div key={prod.id} className="space-y-4">
                {/* « produit » du Registry (niveau de la hiérarchie, pas le domaine PRODUCT) affiché « Format » ;
                    masqué quand il ne se distingue pas du système (Odoo) */}
                {prod.code !== sys.code && (
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="eyebrow">Format</span>
                    <span className="font-medium">{prod.name}</span>
                    <span className="tech text-muted-foreground">{prod.code}</span>
                  </div>
                )}
                <div className="grid gap-4.5 md:grid-cols-2 xl:grid-cols-3">
                  {sortVersions(prod.versions).map((ver) => (
                    <Card key={ver.id} size="sm">
                      <CardContent className="space-y-3">
                        <div className="flex items-center gap-2">
                          <span className="item-title">
                            {/* produit distinct du système (DATEV : un format par produit) → nom du format */}
                            {prod.code === sys.code ? sys.name : prod.name} {ver.version}
                          </span>
                          {ver.edition && <span className="meta">{ver.edition}</span>}
                          <Badge variant="outline" className="ml-auto">
                            {ver.release_status}
                          </Badge>
                        </div>
                        {ver.interfaces.map((itf) => (
                          <div key={itf.id} className="space-y-2">
                            <div className="text-muted-foreground">
                              Interface <span className="tech text-foreground">{itf.code}</span> · {itf.type} ·{" "}
                              {itf.direction}
                            </div>
                            {itf.snapshots.map((snap) => (
                              <Link
                                key={snap.id}
                                href={`/snapshots/${snap.id}`}
                                className="block space-y-1 rounded-xl border border-border bg-muted/60 px-4 py-3 transition-colors hover:border-brand/40 hover:bg-accent"
                              >
                                <div className="flex items-center justify-between gap-3">
                                  <span className="tech font-semibold text-brand">@{snap.snapshot_version}</span>
                                  <span className="meta">{formatDate(snap.created_at)}</span>
                                </div>
                                <div className="meta tabular-nums">
                                  {formatInt(snap.entity_count)} entities · {formatInt(snap.field_count)} fields ·{" "}
                                  <span className="tech">{snap.checksum.slice(0, 12)}</span>
                                </div>
                              </Link>
                            ))}
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
