"use client";

/** Catalogue du fournisseur (V2a) : état (Analyze → Catalog ready → Sync) puis ses vrais produits, lus dans son
 *  système et présentés sous forme canonique. Aucun nom technique : uniquement les DTO fournisseur. */
import { ChevronLeft, ChevronRight, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { FormError } from "@/components/form";
import { OtherOrganizations, PortalGate } from "@/components/portal";
import { Thumb } from "@/components/product-image";
import { ErrorBox, formatDate, LoadingRows } from "@/components/registry";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  formatPrice,
  type SupplierSystem,
  useCatalogSync,
  useProducts,
  useSupplierSystems,
  useSyncCatalog,
} from "@/lib/api/supplier-hooks";
import { useDebounced, useUrlState } from "@/lib/use-url-state";

const PAGE_SIZE = 25;

export function CatalogView() {
  return (
    <PortalGate>
      {(org, me) => (
        <div className="page-stack">
          <div className="space-y-1.5">
            <h1 className="hero-badge">Catalog</h1>
            <p className="page-description">Your products, read from your system.</p>
          </div>
          <SystemCatalog />
          <OtherOrganizations me={me} current={org} />
        </div>
      )}
    </PortalGate>
  );
}

function SystemCatalog() {
  const systems = useSupplierSystems();
  const url = useUrlState();
  if (systems.error) return <ErrorBox error={systems.error} />;
  if (!systems.data) return <LoadingRows />;
  if (!systems.data.length) {
    return (
      <Card size="sm" className="max-w-2xl">
        <CardContent>
          <div className="item-title">No system connected yet</div>
          <p className="meta">Your GO45 contact connects your system; your catalog then appears here.</p>
        </CardContent>
      </Card>
    );
  }
  const system = systems.data.find((s) => s.id === url.get("system")) ?? systems.data[0];
  return (
    <>
      {systems.data.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {systems.data.map((s) => (
            <Button
              key={s.id}
              variant={s.id === system.id ? "default" : "outline"}
              size="sm"
              onClick={() => url.set({ system: s.id, page: undefined, q: undefined })}
            >
              {s.name}
            </Button>
          ))}
        </div>
      )}
      <StatusCard system={system} />
      <Products systemId={system.id} />
    </>
  );
}

function StatusCard({ system }: { system: SupplierSystem }) {
  const sync = useCatalogSync(system.id);
  const run = useSyncCatalog(system.id);
  const status = system.catalog.status;
  const last = sync.data;
  const error = run.error;

  return (
    <Card size="sm">
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="item-title">
              {system.name} <span className="meta font-normal">· {system.system}</span>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              {status === "complete" && <StatusBadge tone="success">Catalog ready</StatusBadge>}
              {status === "questions_open" && (
                <StatusBadge tone="warning">
                  {system.catalog.questions} question{system.catalog.questions === 1 ? "" : "s"} to answer
                </StatusBadge>
              )}
              {(status === "not_analyzed" || status === "awaiting_setup") && (
                <StatusBadge tone="neutral">Not analyzed yet</StatusBadge>
              )}
              {last && <SyncState sync={last} />}
            </div>
          </div>
          <div className="flex gap-2">
            {status !== "complete" && (
              // l'analyse et les questions se font sur la page du système (V2b.2)
              <Link href={`/portal/systems/${system.id}`} className={buttonVariants()}>
                {status === "questions_open" ? "Answer questions" : "Analyze catalog"}
              </Link>
            )}
            {status === "complete" && (
              <Button onClick={() => run.mutate()} disabled={run.isPending}>
                <RefreshCw className={run.isPending ? "animate-spin" : undefined} />
                {run.isPending ? "Syncing…" : last ? "Sync again" : "Sync catalog"}
              </Button>
            )}
          </div>
        </div>
        {status === "questions_open" && (
          <p className="meta">
            A few fields need your confirmation before your products can be read.
          </p>
        )}
        {error && <FormError error={error} />}
      </CardContent>
    </Card>
  );
}

function SyncState({ sync }: { sync: NonNullable<ReturnType<typeof useCatalogSync>["data"]> }) {
  if (sync.status === "failed") {
    return <StatusBadge tone="danger">Last sync failed · {sync.message ?? "unknown error"}</StatusBadge>;
  }
  if (sync.status === "running") return <StatusBadge tone="info">Sync in progress</StatusBadge>;
  return (
    <span className="meta">
      Last synchronized {formatDate(sync.finished_at ?? sync.started_at)} · {sync.products} products ·{" "}
      {sync.variants} variants
      {sync.partial && " · partial (product limit reached)"}
    </span>
  );
}

function Products({ systemId }: { systemId: string }) {
  const url = useUrlState();
  const [search, setSearch] = useState(url.get("q") ?? "");
  const debounced = useDebounced(search.trim());
  const page = Math.max(Number(url.get("page") ?? 1) || 1, 1);
  const products = useProducts(systemId, debounced, page, PAGE_SIZE);

  useEffect(() => {
    if (debounced !== (url.get("q") ?? "")) url.set({ q: debounced || undefined, page: undefined });
  }, [debounced, url]);

  const data = products.data;
  const pages = data ? Math.max(Math.ceil(data.total / data.page_size), 1) : 1;
  if (products.error) return <ErrorBox error={products.error} />;
  if (data && data.total === 0 && !debounced) return null; // pas encore synchronisé

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="relative w-full max-w-sm">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, SKU, barcode or category"
            className="pl-9"
            aria-label="Search products"
          />
        </div>
        {data && <span className="meta">{data.total} {data.total === 1 ? "product" : "products"}</span>}
      </div>
      {!data ? (
        <LoadingRows />
      ) : data.items.length === 0 ? (
        <p className="meta">No product matches “{debounced}”.</p>
      ) : (
        <Card className="py-0">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16 pl-4">Image</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead>SKU</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Price</TableHead>
                  <TableHead className="pr-4">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="pl-4">
                      <Thumb productId={p.id} hasImage={p.has_image} alt={p.name ?? ""} />
                    </TableCell>
                    <TableCell>
                      <Link href={`/portal/catalog/${p.id}`} className="font-medium hover:underline">
                        {p.name ?? "Unnamed product"}
                      </Link>
                      {p.variant_count > 0 && <div className="meta">{p.variant_count} variants</div>}
                    </TableCell>
                    <TableCell className="tech">{p.sku ?? "—"}</TableCell>
                    <TableCell>{p.category ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatPrice(p.price)}</TableCell>
                    <TableCell className="pr-4">
                      <StatusBadge tone={p.status === "active" ? "success" : "neutral"}>
                        {p.status === "active" ? "Active" : "Archived"}
                      </StatusBadge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      {data && pages > 1 && (
        <div className="flex items-center justify-end gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => url.set({ page: page - 1 })}>
            <ChevronLeft /> Previous
          </Button>
          <span className="meta">
            Page {page} / {pages}
          </span>
          <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => url.set({ page: page + 1 })}>
            Next <ChevronRight />
          </Button>
        </div>
      )}
    </div>
  );
}
