"use client";

/** Capabilities (portail fournisseur), LECTURE SEULE : « System integrations » (Source | Target | Use case | Status,
 *  une ligne par use case et par direction, filtres Source et Target) puis « System capabilities » (System |
 *  Role | Capability | Status, filtres System et Role : use cases propres à un système, plus les capacités des
 *  systèmes cibles servis par des formats du Registry, une ligne par use case). Use cases, systèmes et statuts lus dans le
 *  catalogue : rien n'est codé en dur ici, aucune fonctionnalité inventée. Logique pure :
 *  `lib/use-cases/capabilities.mjs`. */
import { useState } from "react";

import { PortalGate } from "@/components/portal";
import { Card, CardContent } from "@/components/ui/card";
import { useAccountingTargets, useExportTargets } from "@/lib/api/supplier-hooks";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  capabilityRows,
  capabilitySystems,
  filterRows,
  filterSystemRows,
  flowSystems,
  type RegistryFormat,
  roleLabel,
  ROLES,
  type RowState,
  stateLabel,
  systemCapabilityRows,
} from "@/lib/use-cases/capabilities.mjs";
import { listUseCases, SYSTEM_NAMES, USE_CASES } from "@/lib/use-cases/catalog.mjs";
import { visibleRows, visibleUseCases } from "@/lib/use-cases/profile.mjs";
import { cn } from "@/lib/utils";

const ORDER = Object.keys(SYSTEM_NAMES); // ordre d'affichage des systèmes : celui du catalogue
const TONE = { success: "text-success", warning: "text-warning", neutral: "text-neutral" };

const systemName = (code: string) => SYSTEM_NAMES[code as keyof typeof SYSTEM_NAMES] ?? code;

export default function CapabilitiesPage() {
  return <PortalGate>{() => <Capabilities />}</PortalGate>;
}

function Capabilities() {
  const useCases = listUseCases("supplier", visibleUseCases(USE_CASES));
  return (
    <div className="page-stack">
      <div className="space-y-1.5">
        <h1 className="hero-badge">Capabilities</h1>
        <p className="page-description text-muted-foreground">
          What can flow between systems, and what you can do within each system.
        </p>
      </div>
      <Integrations useCases={useCases} />
      <SystemCapabilities useCases={useCases} />
    </div>
  );
}

type UseCases = ReturnType<typeof listUseCases>;

function Integrations({ useCases }: { useCases: UseCases }) {
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const systems = flowSystems(useCases, ORDER);
  const rows = filterRows(visibleRows(capabilityRows(useCases, ORDER)), { source, target });

  return (
    <section className="space-y-4">
      <h2 className="section-title">System integrations</h2>
      <div className="flex flex-wrap items-end gap-4">
        <SystemFilter label="Source" value={source} systems={systems} onChange={setSource} />
        <SystemFilter label="Target" value={target} systems={systems} onChange={setTarget} />
      </div>
      <Card>
        <CardContent className="overflow-x-auto p-0">
          <Table className="min-w-[640px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[16%]">Source</TableHead>
                <TableHead className="w-[16%]">Target</TableHead>
                <TableHead>Use case</TableHead>
                <TableHead className="w-[24%]">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={`${r.source}-${r.target}-${r.useCase?.id ?? "none"}`}>
                  <TableCell className="font-medium">{systemName(r.source)}</TableCell>
                  <TableCell className="font-medium">{systemName(r.target)}</TableCell>
                  <TableCell className={cn(!r.useCase && "text-muted-foreground")}>
                    {r.useCase ? r.useCase.title : "No use case yet"}
                  </TableCell>
                  <TableCell>
                    <Status state={r.state} />
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && <EmptyRow colSpan={4} />}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </section>
  );
}

/** Formats cibles PRÉSENTS dans le Registry, tels que l'API du portail les liste (export des partenaires,
 *  comptabilité) ; un format sans version publiée n'est pas une capacité. */
function useRegistryFormats(): RegistryFormat[] {
  const partners = useExportTargets().data ?? [];
  const accounting = useAccountingTargets(true).data ?? [];
  return [
    ...partners
      .filter((t) => t.format_version)
      .map((t) => ({ system: t.system, domain: "partner", name: t.name, version: t.format_version })),
    ...accounting
      .filter((t) => t.versions.length > 0)
      .map((t) => ({ system: t.system, domain: "accounting", name: t.name, version: t.versions[0] })),
  ];
}

function SystemCapabilities({ useCases }: { useCases: UseCases }) {
  const [system, setSystem] = useState("");
  const [role, setRole] = useState("");
  const all = systemCapabilityRows(useCases, ORDER, useRegistryFormats());
  const rows = filterSystemRows(all, { system, role });

  return (
    <section className="space-y-4">
      <h2 className="section-title">System capabilities</h2>
      <div className="flex flex-wrap items-end gap-4">
        <SystemFilter label="System" value={system} systems={capabilitySystems(all)} onChange={setSystem} />
        <label className="flex min-w-44 flex-col gap-1.5">
          <span className="label">Role</span>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">Source and target</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Card>
        <CardContent className="overflow-x-auto p-0">
          <Table className="min-w-[640px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[16%]">System</TableHead>
                <TableHead className="w-[12%]">Role</TableHead>
                <TableHead>Capability</TableHead>
                <TableHead className="w-[24%]">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={`${r.system}-${r.role}-${r.useCase.id}`}>
                  <TableCell className="font-medium">{systemName(r.system)}</TableCell>
                  <TableCell className="text-muted-foreground">{roleLabel(r.role)}</TableCell>
                  <TableCell>
                    {r.useCase.title}
                    {r.formats.length > 0 && (
                      <span className="meta tech block text-muted-foreground">{r.formats.join(" · ")}</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Status state={r.state} />
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && <EmptyRow colSpan={4} />}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </section>
  );
}

function EmptyRow({ colSpan }: { colSpan: number }) {
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="text-muted-foreground">
        Nothing for this choice.
      </TableCell>
    </TableRow>
  );
}

function Status({ state }: { state: RowState }) {
  const l = stateLabel(state);
  return (
    <span className={cn("inline-flex items-center gap-2 whitespace-nowrap", TONE[l.tone])}>
      <span className="font-bold" aria-hidden>
        {l.symbol}
      </span>
      {l.label}
    </span>
  );
}

function SystemFilter({
  label,
  value,
  systems,
  onChange,
}: {
  label: string;
  value: string;
  systems: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex min-w-44 flex-col gap-1.5">
      <span className="label">{label}</span>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All systems</option>
        {systems.map((s) => (
          <option key={s} value={s}>
            {systemName(s)}
          </option>
        ))}
      </select>
    </label>
  );
}
