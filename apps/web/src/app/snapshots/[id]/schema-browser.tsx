"use client";

import { useState } from "react";

import { ErrorBox, FieldName, Flag, formatDate, formatInt, LoadingRows } from "@/components/registry";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { EntityDetail, RelationOut } from "@/lib/api/client";
import { useEntities, useEntity, useFieldSearch, useSnapshot } from "@/lib/api/hooks";
import { useDebounced, useUrlState } from "@/lib/use-url-state";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

export function SchemaBrowser({ snapshotId }: { snapshotId: string }) {
  const url = useUrlState();
  const [filter, setFilter] = useState(url.get("q") ?? "");
  const [fieldQuery, setFieldQuery] = useState("");
  const q = useDebounced(filter);
  const fq = useDebounced(fieldQuery);
  const page = Number(url.get("page") ?? 0);
  const selected = url.get("entity");

  const snapshot = useSnapshot(snapshotId);
  const entities = useEntities(snapshotId, q, PAGE_SIZE, page * PAGE_SIZE);
  const fieldHits = useFieldSearch(snapshotId, fq);
  const entity = useEntity(selected);

  const total = entities.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const onFilter = (value: string) => {
    setFilter(value);
    url.set({ q: value, page: undefined });
  };

  return (
    <div className="page-stack">
      {snapshot.error && <ErrorBox error={snapshot.error} />}
      {snapshot.data && (
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <div>
            <h1 className="hero-badge">
              Schema Browser <span className="tech font-medium text-muted-foreground">{snapshot.data.label}</span>
            </h1>
            <p className="page-description">
              {snapshot.data.system}/{snapshot.data.product} {snapshot.data.version}
              {snapshot.data.edition && ` (${snapshot.data.edition})`} · interface {snapshot.data.interface} ·
              published {formatDate(snapshot.data.created_at)} · checksum{" "}
              <span className="tech">{snapshot.data.checksum.slice(0, 12)}</span>
            </p>
          </div>
          <div className="ml-auto flex gap-2">
            <Badge variant="secondary">{formatInt(snapshot.data.stats.entities)} entities</Badge>
            <Badge variant="secondary">{formatInt(snapshot.data.stats.fields)} fields</Badge>
            <Badge variant="secondary">{formatInt(snapshot.data.stats.relations)} relations</Badge>
          </div>
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
        {/* ---------------- Liste des entités ---------------- */}
        <Card className="h-fit">
          <CardHeader className="space-y-2">
            <Input
              placeholder="Filter entities (e.g. partner)"
              value={filter}
              onChange={(e) => onFilter(e.target.value)}
            />
            <Input
              placeholder="Search a field in the snapshot (e.g. vat)"
              value={fieldQuery}
              onChange={(e) => setFieldQuery(e.target.value)}
            />
          </CardHeader>
          <CardContent className="space-y-3">
            {fq.length >= 2 && (
              <div className="rounded-md border">
                <div className="meta border-b px-3 py-1.5">
                  Fields “{fq}” {fieldHits.data && `· ${fieldHits.data.length} result(s)`}
                </div>
                <div className="max-h-56 overflow-y-auto">
                  {fieldHits.data?.map((h) => (
                    <button
                      key={h.id}
                      onClick={() => url.set({ entity: h.entity_id })}
                      className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left hover:bg-accent/50"
                    >
                      <span className="meta tech">{h.entity}</span>
                      <FieldName field={h} />
                    </button>
                  ))}
                </div>
              </div>
            )}

            {entities.error && <ErrorBox error={entities.error} />}
            {entities.isPending ? (
              <LoadingRows rows={12} />
            ) : (
              <div className="divide-y rounded-md border">
                {entities.data?.items.map((e) => (
                  <button
                    key={e.id}
                    onClick={() => url.set({ entity: e.id })}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent/50",
                      selected === e.id && "bg-muted",
                    )}
                  >
                    <span className={cn("tech truncate", e.kind !== "entity" && "text-muted-foreground")}>
                      {e.technical_name}
                    </span>
                    {e.kind !== "entity" && (
                      <Badge variant="outline">
                        {e.kind}
                      </Badge>
                    )}
                    <span className="meta ml-auto shrink-0 tabular-nums">{e.field_count}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="meta flex items-center justify-between">
              <span>
                {formatInt(total)} entities · page {page + 1}/{pages}
              </span>
              <div className="flex gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => url.set({ page: page - 1 || undefined })}
                >
                  ←
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page + 1 >= pages}
                  onClick={() => url.set({ page: page + 1 })}
                >
                  →
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* ---------------- Détail ---------------- */}
        <div className="min-w-0">
          {!selected && (
            <div className="rounded-card border border-dashed p-10 text-center text-muted-foreground">
              Select an entity to see its fields and relations.
            </div>
          )}
          {entity.error && <ErrorBox error={entity.error} />}
          {selected && entity.isPending && <LoadingRows rows={16} />}
          {entity.data && <EntityPanel entity={entity.data} onSelect={(id) => url.set({ entity: id })} />}
        </div>
      </div>
    </div>
  );
}

function EntityPanel({ entity, onSelect }: { entity: EntityDetail; onSelect: (id: string) => void }) {
  const meta = entity.metadata as { modules?: string[]; kind_inferred?: boolean };
  // Relations indexées par champ source (déjà calculées par le Core) pour les afficher sur la ligne
  const relationOf = new Map(entity.relations.filter((r) => r.source_field).map((r) => [r.source_field!, r]));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-baseline gap-3">
          <span className="tech">{entity.technical_name}</span>
          <span className="font-normal text-muted-foreground">{entity.display_name}</span>
        </CardTitle>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline">module {entity.module ?? "—"}</Badge>
          <Badge variant="outline">
            {entity.kind}
            {meta.kind_inferred && " (inferred)"}
          </Badge>
          <Badge variant="outline">table {entity.physical_name ?? "—"}</Badge>
          <Badge variant="outline">{entity.field_count} fields</Badge>
          <Badge variant="outline">{entity.relations.length} relations</Badge>
        </div>
        {meta.modules && meta.modules.length > 1 && (
          <p className="meta">Extended by: {meta.modules.slice(1).join(", ")}</p>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-[31%]">Field</TableHead>
              <TableHead className="w-[22%]">Label</TableHead>
              <TableHead className="w-[25%]">Type · native</TableHead>
              <TableHead className="w-[22%]">Properties</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entity.fields.map((f) => (
              <TableRow key={f.id}>
                <TableCell
                  className="truncate"
                  title={f.physical_column ? `column ${f.physical_column}` : "no physical column"}
                >
                  <FieldName field={f} />
                </TableCell>
                <TableCell className="truncate" title={f.help ?? undefined}>
                  {f.label}
                </TableCell>
                <TableCell className="meta tech text-foreground">
                  <div className="truncate">
                    {f.data_type}
                    {f.native_type && <span className="text-muted-foreground"> · {f.native_type}</span>}
                  </div>
                  <RelationTarget relation={relationOf.get(f.technical_name)} onSelect={onSelect} />
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    <Flag on={f.required} label="req" tone="required" />
                    <Flag on={f.readonly} label="ro" />
                    <Flag on={f.computed} label={f.stored ? "computed, stored" : "computed"} />
                    <Flag on={!!(f.metadata as { company_dependent?: boolean }).company_dependent} label="company" />
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {entity.relations.length > 0 && (
          <div>
            <h3 className="item-title mb-2">Relations</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Field</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Inverse</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entity.relations.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="tech">{r.source_field ?? "—"}</TableCell>
                    <TableCell className="tech text-muted-foreground">{r.relation_type}</TableCell>
                    <TableCell className="tech">
                      {r.target_entity_id ? (
                        <button
                          onClick={() => onSelect(r.target_entity_id!)}
                          className="underline-offset-2 hover:underline"
                        >
                          {r.target_entity}
                        </button>
                      ) : (
                        <span className="truncate text-muted-foreground" title="Entity outside the snapshot (e.g. transient)">
                          {r.target_entity}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="tech text-muted-foreground">{r.inverse_field ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RelationTarget({ relation, onSelect }: { relation?: RelationOut; onSelect: (id: string) => void }) {
  if (!relation) return null;
  const target = relation.target_entity_id;
  return (
    <div className="meta tech mt-0.5 flex min-w-0" title={relation.target_entity}>
      <span className="shrink-0 pr-1 text-muted-foreground">→</span>
      {target ? (
        <button
          onClick={() => onSelect(target)}
          className="truncate text-info underline-offset-2 hover:underline"
        >
          {relation.target_entity}
        </button>
      ) : (
        <span className="text-muted-foreground" title="Entity outside the snapshot (e.g. transient)">
          {relation.target_entity}
        </span>
      )}
    </div>
  );
}
