"use client";

/** Fiche produit du fournisseur (V2a) : un produit métier et ses variantes regroupées, lus dans son système.
 *  Les informations absentes de la source ne sont simplement pas affichées. */
import { useState } from "react";

import { PortalGate } from "@/components/portal";
import { Thumb } from "@/components/product-image";
import { ErrorBox, formatDate, LoadingRows } from "@/components/registry";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatPrice, type ProductDetail, useProduct } from "@/lib/api/supplier-hooks";
import { cn } from "@/lib/utils";

export function ProductView({ productId }: { productId: string }) {
  return <PortalGate>{() => <Product productId={productId} />}</PortalGate>;
}

function Product({ productId }: { productId: string }) {
  const product = useProduct(productId);
  const [variantId, setVariantId] = useState<string>();
  if (product.error) return <ErrorBox error={product.error} />;
  if (!product.data) return <LoadingRows rows={10} />;
  const p = product.data;
  const shown = p.variants.find((v) => v.id === variantId && v.has_image);

  return (
    <div className="page-stack">
      <div>
        <div className="flex flex-wrap items-center gap-4">
          <h1 className="hero-badge">{p.name ?? "Unnamed product"}</h1>
          <StatusBadge tone={p.status === "active" ? "success" : "neutral"}>
            {p.status === "active" ? "Active" : "Archived"}
          </StatusBadge>
        </div>
        {p.category && <p className="meta mt-1">{p.category}</p>}
      </div>

      <div className="grid gap-4.5 lg:grid-cols-[minmax(0,360px)_1fr]">
        <Card size="sm">
          <CardContent className="flex items-center justify-center">
            <Thumb
              key={shown?.id ?? "product"}
              productId={p.id}
              variantId={shown?.id}
              hasImage={shown ? shown.has_image : p.has_image}
              alt={p.name ?? ""}
              size="large"
              className="aspect-square w-full max-w-80"
            />
          </CardContent>
        </Card>
        <Card size="sm">
          <CardContent>
            <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              <Info label="Price" value={formatPrice(p.price)} />
              <Info label="SKU" value={p.sku} mono />
              <Info label="Barcode" value={p.barcode} mono />
              <Info label="Category" value={p.category} />
              <Info label="Weight" value={p.weight ? String(p.weight) : null} />
              <Info label="Volume" value={p.volume ? String(p.volume) : null} />
              <Info label="Unit" value={p.unit} />
              <Info label="Product type" value={p.product_type} />
            </dl>
            {p.description && <p className="mt-4 whitespace-pre-line">{p.description}</p>}
            <p className="meta mt-4">Last synchronized {formatDate(p.synced_at)}</p>
          </CardContent>
        </Card>
      </div>

      {p.variants.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Variants ({p.variants.length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {p.attributes.length > 0 && (
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                {p.attributes.map((a) => (
                  <span key={a.name}>
                    <span className="meta">{a.name}:</span> {a.values.join(", ")}
                  </span>
                ))}
              </div>
            )}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">Image</TableHead>
                  <TableHead>Variant</TableHead>
                  <TableHead>SKU</TableHead>
                  <TableHead>Barcode</TableHead>
                  <TableHead className="text-right">Price</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {p.variants.map((v) => (
                  <TableRow
                    key={v.id}
                    onClick={() => setVariantId(v.id)}
                    className={cn("cursor-pointer", v.id === variantId && "bg-muted/60")}
                  >
                    <TableCell>
                      <Thumb
                        productId={p.id}
                        variantId={v.has_image ? v.id : undefined}
                        hasImage={v.has_image || p.has_image}
                        alt={v.label}
                        className="size-10"
                      />
                    </TableCell>
                    <TableCell className="font-medium">{v.label}</TableCell>
                    <TableCell className="tech">{v.sku ?? "—"}</TableCell>
                    <TableCell className="tech">{v.barcode ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatPrice(v.price)}</TableCell>
                    <TableCell>
                      <StatusBadge tone={v.status === "active" ? "success" : "neutral"}>
                        {v.status === "active" ? "Active" : "Archived"}
                      </StatusBadge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {p.supplier_offers.length > 0 && <SupplierInfo product={p} />}

      {p.additional_information.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Additional information</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              {p.additional_information.map((x) => (
                <Info key={x.label} label={x.label} value={x.value === null ? null : String(x.value)} />
              ))}
            </dl>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function SupplierInfo({ product }: { product: ProductDetail }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Supplier information</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Supplier</TableHead>
              <TableHead>Supplier reference</TableHead>
              <TableHead className="text-right">Price</TableHead>
              <TableHead className="text-right">Min. quantity</TableHead>
              <TableHead className="text-right">Lead time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {product.supplier_offers.map((o, i) => (
              <TableRow key={i}>
                <TableCell className="font-medium">{o.supplier ?? "—"}</TableCell>
                <TableCell className="tech">{o.supplier_reference ?? "—"}</TableCell>
                <TableCell className="text-right tabular-nums">{formatPrice(o.price)}</TableCell>
                <TableCell className="text-right tabular-nums">{o.min_quantity ?? "—"}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {o.lead_time_days != null ? `${o.lead_time_days} days` : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function Info({ label, value, mono = false }: { label: string; value: string | null | undefined; mono?: boolean }) {
  if (!value) return null;
  return (
    <div>
      <dt className="meta">{label}</dt>
      <dd className={cn(mono && "tech")}>{value}</dd>
    </div>
  );
}
