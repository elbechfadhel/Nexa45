"use client";

/** Création d'une instance. Générique : système/version viennent du Registry, les paramètres de connexion
 *  sont une liste clé/valeur libre (non secrets). Le secret n'est gardé que dans l'état local jusqu'à
 *  l'envoi, puis effacé ; l'API ne le renvoie jamais.
 *  V1d : l'instance appartient à un supplier. Depuis la page d'un supplier, `organizationId` est fixé
 *  (POST /admin/organizations/{id}/instances) ; sinon on le choisit (POST /admin/instances). */
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FormError } from "@/components/form";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  type InstanceCreateBody,
  useCreateInstance,
  useCreateOrganizationInstance,
  useOrganizations,
  useSystems,
} from "@/lib/api/hooks";

type Param = { key: string; value: string };

const ENVIRONMENTS = ["test", "staging", "production"].map((e) => ({ value: e, label: e }));
// Suggestions courantes (modifiables / supprimables) : le front ne présuppose aucun système
const DEFAULT_PARAMS: Param[] = [
  { key: "db", value: "" },
  { key: "username", value: "" },
];

// organisation structurelle créée par la migration V1d (valeur par défaut de l'API)
const GO45_DEV_ORG_ID = "00000000-0000-4000-8000-000000000045";

export function CreateInstanceForm({ onClose, organizationId }: { onClose: () => void; organizationId?: string }) {
  const router = useRouter();
  const systems = useSystems();
  const organizations = useOrganizations();
  const createAny = useCreateInstance();
  const createInOrg = useCreateOrganizationInstance(organizationId ?? "");
  const create = organizationId ? createInOrg : createAny;
  const [organization, setOrganization] = useState<string | null>(GO45_DEV_ORG_ID);
  const orgItems = (organizations.data ?? []).map((o) => ({ value: o.id, label: o.name }));

  const [name, setName] = useState("");
  const [system, setSystem] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [environment, setEnvironment] = useState("test");
  const [endpoint, setEndpoint] = useState("");
  const [params, setParams] = useState<Param[]>(DEFAULT_PARAMS);
  const [password, setPassword] = useState("");

  const sys = systems.data?.find((s) => s.code === system);
  const systemItems = (systems.data ?? []).map((s) => ({ value: s.code, label: s.name }));
  const versionItems = [...new Set(sys?.products.flatMap((p) => p.versions.map((v) => v.version)) ?? [])].map(
    (v) => ({ value: v, label: v }),
  );
  const canSubmit =
    !!name.trim() && !!system && !!version && !!endpoint.trim() && (!!organizationId || !!organization) && !create.isPending;

  const setParam = (i: number, patch: Partial<Param>) =>
    setParams((ps) => ps.map((p, j) => (j === i ? { ...p, ...patch } : p)));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    const config = Object.fromEntries(
      params.filter((p) => p.key.trim() && p.value.trim()).map((p) => [p.key.trim(), p.value.trim()]),
    );
    const body: InstanceCreateBody = {
      name: name.trim(),
      system: system!,
      version: version!,
      environment,
      endpoint: endpoint.trim(),
      config,
      password: password || null,
    };
    const run = organizationId
      ? createInOrg.mutateAsync(body)
      : createAny.mutateAsync({ ...body, organization_id: organization });
    run.then(
      (inst) => {
        setPassword("");
        router.push(`/instances/${inst.id}`);
      },
      () => setPassword(""), // ne jamais garder le secret plus longtemps que nécessaire
    );
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>New instance</CardTitle>
        <Button variant="ghost" onClick={onClose} type="button">
          Close
        </Button>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-5" autoComplete="off">
          {!organizationId && (
            <Field label="Supplier *" hint="organization that owns the instance">
              <Select items={orgItems} value={organization} onValueChange={setOrganization}>
                <SelectTrigger className="w-72">
                  <SelectValue placeholder="Supplier" />
                </SelectTrigger>
                <SelectContent>
                  {orgItems.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
          <div className="grid gap-4 md:grid-cols-3">
            <Field label="Name *" hint="unique within the supplier, e.g. customer17">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="customer17" required />
            </Field>
            <Field label="System and version *" hint="standard version imported into the Registry">
              <div className="flex gap-2">
                <Select
                  items={systemItems}
                  value={system}
                  onValueChange={(v) => {
                    setSystem(v);
                    setVersion(null);
                  }}
                >
                  <SelectTrigger className="w-36">
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
                <Select items={versionItems} value={version} onValueChange={setVersion} disabled={!sys}>
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
            </Field>
            <Field label="Environment">
              <div className="flex gap-2">
                <Select items={ENVIRONMENTS} value={environment} onValueChange={(v) => v && setEnvironment(v)}>
                  <SelectTrigger className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ENVIRONMENTS.map((e) => (
                      <SelectItem key={e.value} value={e.value}>
                        {e.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </Field>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <Field label="Endpoint *" hint="instance URL">
              <Input
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                placeholder="http://localhost:8027"
                className="font-mono"
                required
              />
            </Field>
            <Field label="Connection parameters" hint="not secret · key / value">
              <div className="space-y-2">
                {params.map((p, i) => (
                  <div key={i} className="flex gap-2">
                    <Input
                      value={p.key}
                      onChange={(e) => setParam(i, { key: e.target.value })}
                      placeholder="key"
                      className="w-36 font-mono"
                      aria-label={`parameter ${i + 1} key`}
                    />
                    <Input
                      value={p.value}
                      onChange={(e) => setParam(i, { value: e.target.value })}
                      placeholder="value"
                      className="font-mono"
                      aria-label={`value of parameter ${p.key || i + 1}`}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => setParams((ps) => ps.filter((_, j) => j !== i))}
                      aria-label="Remove parameter"
                    >
                      ✕
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setParams((ps) => [...ps, { key: "", value: "" }])}
                >
                  + parameter
                </Button>
              </div>
            </Field>
            <Field label="Password / API key" hint="encrypted on the server, never shown again">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                placeholder="••••••••"
              />
            </Field>
          </div>

          {create.error && <FormError error={create.error} />}

          <div className="flex items-center gap-3">
            <Button type="submit" disabled={!canSubmit}>
              {create.isPending ? "Creating…" : "Create instance"}
            </Button>
            <span className="meta">
              Capabilities start as DECLARED; Test Connection verifies them.
            </span>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
