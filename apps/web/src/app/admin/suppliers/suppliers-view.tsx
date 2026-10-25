"use client";

/** Suppliers (V1d) : organisations clientes de la plateforme, leurs utilisateurs et leurs instances. */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Field, FormError } from "@/components/form";
import { ErrorBox, formatDate, LoadingRows } from "@/components/registry";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCreateOrganization, useOrganizations } from "@/lib/api/hooks";
import { useUrlState } from "@/lib/use-url-state";

export function SuppliersView() {
  const { data, error, isPending } = useOrganizations();
  const url = useUrlState();
  const creating = url.get("new") === "1";
  return (
    <div className="page-stack">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="hero-badge">Suppliers</h1>
          <p className="page-description">
            Organizations using the platform. Each one only ever sees its own systems and catalog decisions.
          </p>
        </div>
        {!creating && <Button onClick={() => url.set({ new: 1 })}>Create supplier</Button>}
      </div>
      {creating && <CreateSupplierForm onClose={() => url.set({ new: undefined })} />}
      {error && <ErrorBox error={error} />}
      {isPending && <LoadingRows />}
      {!!data?.length && (
        <Card className="py-0">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Supplier</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Users</TableHead>
                  <TableHead className="text-right">Instances</TableHead>
                  <TableHead className="pr-4">Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((o) => (
                  <TableRow key={o.id}>
                    <TableCell className="pl-4">
                      <Link href={`/admin/suppliers/${o.id}`} className="font-medium hover:underline">
                        {o.name}
                      </Link>
                      <div className="meta tech">{o.slug}</div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge tone={o.status === "ACTIVE" ? "success" : "neutral"}>
                        {o.status === "ACTIVE" ? "Active" : "Inactive"}
                      </StatusBadge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{o.user_count}</TableCell>
                    <TableCell className="text-right tabular-nums">{o.instance_count}</TableCell>
                    <TableCell className="meta pr-4">{formatDate(o.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function CreateSupplierForm({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const create = useCreateOrganization();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || create.isPending) return;
    create.mutate(
      { name: name.trim(), slug: slug.trim() || null },
      { onSuccess: (o) => router.push(`/admin/suppliers/${o.id}`) },
    );
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>New supplier</CardTitle>
        <Button variant="ghost" onClick={onClose} type="button">
          Close
        </Button>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-5" autoComplete="off">
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Name *">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Tools GmbH" required />
            </Field>
            <Field label="Identifier" hint="derived from the name when empty, e.g. acme-tools-gmbh">
              <Input value={slug} onChange={(e) => setSlug(e.target.value)} className="font-mono" />
            </Field>
          </div>
          {create.error && <FormError error={create.error} />}
          <Button type="submit" disabled={!name.trim() || create.isPending}>
            {create.isPending ? "Creating…" : "Create supplier"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
