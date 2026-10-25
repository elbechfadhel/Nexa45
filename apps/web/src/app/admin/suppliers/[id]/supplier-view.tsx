"use client";

/** Un supplier (V1d) : ses utilisateurs (ajout avec mot de passe initial) et ses instances (création,
 *  rattachement d'une instance existante, liens vers les écrans techniques). */
import Link from "next/link";
import { useState } from "react";

import { ConnectionStatus } from "@/components/instances";
import { Field, FormError } from "@/components/form";
import { ErrorBox, formatDate, LoadingRows } from "@/components/registry";
import { environmentTone, StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { OrganizationDetail } from "@/lib/api/client";
import { useAddOrganizationUser, useAttachInstance, useInstances, useOrganization } from "@/lib/api/hooks";
import { useUrlState } from "@/lib/use-url-state";

import { CreateInstanceForm } from "@/app/instances/create-instance-form";

const ROLES = [
  { value: "MEMBER", label: "Member" },
  { value: "OWNER", label: "Owner" },
];

export function SupplierView({ id }: { id: string }) {
  const org = useOrganization(id);
  const url = useUrlState();
  const panel = url.get("panel"); // add-user | new-instance | attach
  const close = () => url.set({ panel: undefined });

  if (org.error) return <ErrorBox error={org.error} />;
  if (!org.data) return <LoadingRows rows={10} />;
  const o = org.data;

  return (
    <div className="page-stack">
      <div>
        <div className="flex flex-wrap items-center gap-4">
          <h1 className="hero-badge">{o.name}</h1>
          <span className="tech text-muted-foreground">{o.slug}</span>
          <StatusBadge tone={o.status === "ACTIVE" ? "success" : "neutral"}>
            {o.status === "ACTIVE" ? "Active" : "Inactive"}
          </StatusBadge>
        </div>
        <p className="meta mt-1">Created {formatDate(o.created_at)}</p>
      </div>

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
          <CardTitle>Users</CardTitle>
          {panel !== "add-user" && (
            <Button variant="outline" onClick={() => url.set({ panel: "add-user" })}>
              Add user
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-5">
          {panel === "add-user" && <AddUserForm organizationId={o.id} onClose={close} />}
          {o.users.length === 0 ? (
            <p className="meta">No user yet: nobody can sign in to this supplier&apos;s portal.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Member since</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {o.users.map((u) => (
                  <TableRow key={u.user_id}>
                    <TableCell>
                      <div className="font-medium">{u.display_name}</div>
                      <div className="meta">{u.email}</div>
                    </TableCell>
                    <TableCell>{u.role === "OWNER" ? "Owner" : "Member"}</TableCell>
                    <TableCell>
                      <StatusBadge tone={u.status === "ACTIVE" ? "success" : "neutral"}>
                        {u.status === "ACTIVE" ? "Active" : "Inactive"}
                      </StatusBadge>
                    </TableCell>
                    <TableCell className="meta">{formatDate(u.joined_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
          <CardTitle>Instances</CardTitle>
          {!panel?.match(/new-instance|attach/) && (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => url.set({ panel: "attach" })}>
                Attach existing
              </Button>
              <Button onClick={() => url.set({ panel: "new-instance" })}>Create instance</Button>
            </div>
          )}
        </CardHeader>
        <CardContent className="space-y-5">
          {panel === "attach" && <AttachInstanceForm org={o} onClose={close} />}
          {o.instances.length === 0 ? (
            <p className="meta">No instance yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Instance</TableHead>
                  <TableHead>System</TableHead>
                  <TableHead>Env.</TableHead>
                  <TableHead>Connection</TableHead>
                  <TableHead>Technical screens</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {o.instances.map((i) => (
                  <TableRow key={i.id}>
                    <TableCell>
                      <Link href={`/instances/${i.id}`} className="font-medium hover:underline">
                        {i.name}
                      </Link>
                    </TableCell>
                    <TableCell className="capitalize">
                      {i.system} {i.version}
                    </TableCell>
                    <TableCell>
                      <StatusBadge tone={environmentTone(i.environment)}>{i.environment}</StatusBadge>
                    </TableCell>
                    <TableCell>
                      <ConnectionStatus instance={i} />
                    </TableCell>
                    <TableCell className="space-x-3 text-sm">
                      <Link href={`/instances/${i.id}`} className="hover:underline">
                        Instance
                      </Link>
                      <Link href={`/instances/${i.id}/product-domain`} className="hover:underline">
                        Product domain
                      </Link>
                      <Link href={`/instances/${i.id}/catalog`} className="hover:underline">
                        Catalog analysis
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {panel === "new-instance" && <CreateInstanceForm organizationId={o.id} onClose={close} />}
    </div>
  );
}

function AddUserForm({ organizationId, onClose }: { organizationId: string; onClose: () => void }) {
  const add = useAddOrganizationUser(organizationId);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<string | null>("MEMBER");
  const [added, setAdded] = useState<string>();

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || add.isPending) return;
    add.mutate(
      {
        email: email.trim(),
        display_name: name.trim() || null,
        password: password || null,
        role: role === "OWNER" ? "OWNER" : "MEMBER",
      },
      {
        onSuccess: (m) => {
          setAdded(
            m.user_created
              ? `${m.email} created. Share the initial password through a secure channel.`
              : `${m.email} already had an account: added to this supplier, password unchanged.`,
          );
          setEmail("");
          setName("");
        },
        onSettled: () => setPassword(""), // le mot de passe ne reste pas dans l'état
      },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-4 rounded-card-sm border border-border p-4.5" autoComplete="off">
      <div className="grid gap-4 md:grid-cols-4">
        <Field label="Email *">
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </Field>
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Initial password" hint="new user only · at least 12 characters">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
        </Field>
        <Field label="Role">
          <Select items={ROLES} value={role} onValueChange={setRole}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ROLES.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>
      {add.error && <FormError error={add.error} />}
      {added && !add.error && <p className="text-sm text-success">{added}</p>}
      <div className="flex gap-2">
        <Button type="submit" disabled={!email.trim() || add.isPending}>
          {add.isPending ? "Adding…" : "Add user"}
        </Button>
        <Button type="button" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </form>
  );
}

function AttachInstanceForm({ org, onClose }: { org: OrganizationDetail; onClose: () => void }) {
  const instances = useInstances();
  const attach = useAttachInstance(org.id);
  const [instance, setInstance] = useState<string | null>(null);
  const items = (instances.data ?? [])
    .filter((i) => i.organization_id !== org.id)
    .map((i) => ({ value: i.id, label: `${i.name} · ${i.organization_name}` }));

  return (
    <div className="space-y-4 rounded-card-sm border border-border p-4.5">
      <p className="meta">
        The instance moves to {org.name} with its snapshots, catalog analysis and answers. Its current supplier
        no longer sees it.
      </p>
      {instances.error && <ErrorBox error={instances.error} />}
      <div className="flex flex-wrap items-center gap-2">
        <Select items={items} value={instance} onValueChange={setInstance}>
          <SelectTrigger className="w-80">
            <SelectValue placeholder={items.length ? "Instance" : "No other instance"} />
          </SelectTrigger>
          <SelectContent>
            {items.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          disabled={!instance || attach.isPending}
          onClick={() => instance && attach.mutate(instance, { onSuccess: onClose })}
        >
          {attach.isPending ? "Attaching…" : "Attach"}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      {attach.error && <FormError error={attach.error} />}
    </div>
  );
}
