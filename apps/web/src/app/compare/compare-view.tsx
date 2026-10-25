"use client";

import Link from "next/link";
import { useState } from "react";

import { ChangeGroups } from "@/components/change-groups";
import { ErrorBox, formatInt, LoadingRows } from "@/components/registry";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { CompareOut, SnapshotDetail } from "@/lib/api/client";
import { useCompare, useResolve, useSystems } from "@/lib/api/hooks";
import { useUrlState } from "@/lib/use-url-state";

type Ref = { system?: string; product?: string; version?: string };

/** Valeur d'URL « system:version » (from=odoo:17) ou « system:produit:version » quand le système a plusieurs
 * produits (from=datev:buchungsstapel:12) */
const parseRef = (v: string | undefined): Ref => {
  const parts = (v ?? "").split(":");
  const [system, product, version] = parts.length >= 3 ? parts : [parts[0], undefined, parts[1]];
  return { system: system || undefined, product: product || undefined, version: version || undefined };
};
const formatRef = (r: Ref) => [r.system, ...(r.product ? [r.product] : []), r.version ?? ""].join(":");

/** Versions dans l'ordre numérique quand elles sont numériques (« 5 », « 10 » et non « 10 », « 5 ») */
const byVersion = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });

export function CompareView() {
  const url = useUrlState();
  const from = parseRef(url.get("from"));
  const to = parseRef(url.get("to"));
  const entity = url.get("entity");
  const [entityInput, setEntityInput] = useState(entity ?? "");

  const fromSnap = useResolve(from.system, from.version, from.product);
  const toSnap = useResolve(to.system, to.version, to.product);
  const result = useCompare(fromSnap.data?.id, toSnap.data?.id, entity);

  return (
    <div className="page-stack">
      <div className="space-y-1.5">
        <h1 className="hero-badge">Version Compare</h1>
        <p className="page-description">
          Differences computed by the Core (compare_snapshots): ADDED / REMOVED / CHANGED.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-6">
          <SnapshotPicker
            label="From"
            value={from}
            onChange={(v) => url.set({ from: formatRef(v) })}
            resolved={fromSnap.data}
            error={fromSnap.error}
          />
          <span className="pb-2 text-xl text-muted-foreground">→</span>
          <SnapshotPicker
            label="To"
            value={to}
            onChange={(v) => url.set({ to: formatRef(v) })}
            resolved={toSnap.data}
            error={toSnap.error}
          />
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              url.set({ entity: entityInput.trim() });
            }}
          >
            <label className="space-y-1.5">
              <span className="block text-xs font-medium text-muted-foreground">Entity (optional)</span>
              <Input
                className="w-64 font-mono"
                placeholder="product.template"
                value={entityInput}
                onChange={(e) => setEntityInput(e.target.value)}
              />
            </label>
            <Button type="submit" variant="secondary">
              Filter
            </Button>
            {entity && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setEntityInput("");
                  url.set({ entity: undefined });
                }}
              >
                All
              </Button>
            )}
          </form>
        </CardContent>
      </Card>

      {result.error && <ErrorBox error={result.error} />}
      {result.isFetching && (
        <div className="space-y-2">
          <p className="meta">Comparing…</p>
          <LoadingRows rows={8} />
        </div>
      )}
      {!fromSnap.data || !toSnap.data ? (
        <p className="meta">Choose two versions to compare.</p>
      ) : (
        result.data && !result.isFetching && <CompareResult data={result.data} />
      )}
    </div>
  );
}

function SnapshotPicker({
  label,
  value,
  onChange,
  resolved,
  error,
}: {
  label: string;
  value: Ref;
  onChange: (v: Ref) => void;
  resolved?: SnapshotDetail;
  error: unknown;
}) {
  const systems = useSystems();
  const system = systems.data?.find((s) => s.code === value.system);
  // un système à plusieurs produits (DATEV : un produit par format) : le produit se choisit avant la version
  const multiProduct = (system?.products.length ?? 0) > 1;
  const products = multiProduct ? system!.products.filter((p) => p.code === value.product) : system?.products;
  const versions = products?.flatMap((p) => p.versions.map((v) => v.version)) ?? [];
  const systemItems = (systems.data ?? []).map((s) => ({ value: s.code, label: s.name }));
  const productItems = (system?.products ?? []).map((p) => ({ value: p.code, label: p.name }));
  const versionItems = [...new Set(versions)].sort(byVersion).map((v) => ({ value: v, label: v }));

  return (
    <div className="space-y-1.5">
      <span className="block text-xs font-medium text-muted-foreground">{label}</span>
      <div className="flex gap-2">
        <Select
          items={systemItems}
          value={value.system ?? null}
          onValueChange={(v) => v && onChange({ system: v })}
        >
          <SelectTrigger className="w-32">
            <SelectValue placeholder="System" />
          </SelectTrigger>
          <SelectContent>
            {systemItems.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {multiProduct && (
          <Select
            items={productItems}
            value={value.product ?? null}
            onValueChange={(v) => v && value.system && onChange({ system: value.system, product: v })}
          >
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Format" />
            </SelectTrigger>
            <SelectContent>
              {productItems.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Select
          items={versionItems}
          value={value.version ?? null}
          onValueChange={(v) => v && value.system && onChange({ ...value, system: value.system, version: v })}
          disabled={!system || (multiProduct && !value.product)}
        >
          <SelectTrigger className="w-24">
            <SelectValue placeholder="Version" />
          </SelectTrigger>
          <SelectContent>
            {versionItems.map((v) => (
              <SelectItem key={v.value} value={v.value}>
                {v.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="meta h-5">
        {resolved && (
          <Link href={`/snapshots/${resolved.id}`} className="hover:underline">
            <span className="tech text-brand">@{resolved.snapshot_version}</span> ·{" "}
            {formatInt(resolved.stats.entities)} entities · {formatInt(resolved.stats.fields)} fields
          </Link>
        )}
        {error instanceof Error && <span className="text-danger">{error.message}</span>}
      </div>
    </div>
  );
}

function CompareResult({ data }: { data: CompareOut }) {
  return (
    <ChangeGroups
      changes={data.changes}
      counts={data.counts}
      header={
        <>
          <span className="tech text-base font-medium">
            {data.source} → {data.target}
          </span>
          {data.entity && <span className="text-muted-foreground">entity {data.entity}</span>}
        </>
      }
    />
  );
}
