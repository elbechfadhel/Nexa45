"use client";

import { ArrowRight, Copy, Database, MoreHorizontal, Pencil, Trash2, CircleHelp } from "lucide-react";
import Link from "next/link";

import { CAPABILITY_TONE } from "@/components/instances";
import { ErrorBox, formatDate, formatInt, LoadingRows } from "@/components/registry";
import { environmentTone, Pill, StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { CapabilityOut, InstanceDetail } from "@/lib/api/client";
import { useDiscoverSchema, useInstance, useInstanceDiff, useTestConnection } from "@/lib/api/hooks";
import { useUrlState } from "@/lib/use-url-state";
import { cn } from "@/lib/utils";

import { DeleteInstancePanel } from "./delete-instance";
import { capitalize, formatBaseline, formatInterface } from "./format";
import { InstanceDiffCard } from "./instance-diff";

export function InstanceView({ id }: { id: string }) {
  const url = useUrlState();
  const selected = url.get("snapshot") ? Number(url.get("snapshot")) : undefined;
  const instance = useInstance(id);
  const hasSnapshots = (instance.data?.snapshot_count ?? 0) > 0;
  const diff = useInstanceDiff(id, selected, hasSnapshots);
  const test = useTestConnection(id);
  const discover = useDiscoverSchema(id);
  const deleting = url.get("delete") === "1";

  if (instance.error) return <ErrorBox error={instance.error} />;
  if (!instance.data) return <LoadingRows rows={10} />;
  const i = instance.data;
  const runDiscover = () => discover.mutate();

  return (
    <div className="page-stack">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-4">
            <h1 className="hero-badge">{i.name}</h1>
            <span className="text-muted-foreground">
              {capitalize(i.system)} {i.version}
              {i.edition && ` (${i.edition})`}
            </span>
            <Pill variant="neutral" tone={environmentTone(i.environment)} title="Environment" className="font-semibold">
              {i.environment}
            </Pill>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ConnectionBox i={i} />
          <Button variant="outline" size="lg" className="h-10 px-4" onClick={() => test.mutate()} disabled={test.isPending}>
            {test.isPending ? "Testing…" : "Test Connection"}
          </Button>
          <Button size="lg" className="h-10 px-4" onClick={runDiscover} disabled={discover.isPending}>
            {discover.isPending ? "Discovering…" : "Discover Schema"}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger className={cn(buttonVariants({ variant: "outline" }), "size-10")} aria-label="More actions">
              <MoreHorizontal />
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem variant="destructive" onClick={() => url.set({ delete: 1 })}>
                <Trash2 /> Delete instance
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {deleting && <DeleteInstancePanel i={i} onCancel={() => url.set({ delete: undefined })} />}
      {!!test.error && <ErrorBox error={test.error} />}
      {!!discover.error && <ErrorBox error={discover.error} />}

      <div className="grid gap-4.5 lg:grid-cols-2 2xl:grid-cols-[1fr_0.92fr_1.08fr]">
        <InstanceInformation i={i} />
        <ConnectionDetails i={i} />
        <Capabilities i={i} />
      </div>

      <SchemaCard
        i={i}
        selected={selected ?? i.last_discovery?.sequence}
        onSelect={(n) => url.set({ snapshot: n === i.last_discovery?.sequence ? undefined : n })}
        onDiscover={runDiscover}
        discovering={discover.isPending}
      />

      {hasSnapshots && (
        <InstanceDiffCard i={i} diff={diff.data} error={diff.error} pending={diff.isPending} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* En-tête                                                            */
/* ------------------------------------------------------------------ */

function connectionState(i: InstanceDetail) {
  const c = i.connection;
  if (!c?.last_test_at) return { tone: "neutral" as const, label: "Not tested" };
  return c.last_test_status === "OK"
    ? { tone: "success" as const, label: "Connected" }
    : { tone: "danger" as const, label: "Connection failed" };
}

function ConnectionBox({ i }: { i: InstanceDetail }) {
  const st = connectionState(i);
  return (
    <div className="rounded-lg border px-4 py-1.5">
      <StatusBadge tone={st.tone} className="text-sm">
        {st.label}
      </StatusBadge>
      <div className="meta pl-3.5">
        {i.connection?.last_test_at ? `Last test: ${formatDate(i.connection.last_test_at)}` : "Run Test Connection"}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Cartes du haut                                                     */
/* ------------------------------------------------------------------ */

/** [libellé, valeur, technique?] : seules les valeurs techniques (identifiants, URL, versions) sont en monospace. */
type KV = [string, React.ReactNode, boolean?];

function KeyValues({ rows, labelWidth = 130 }: { rows: KV[]; labelWidth?: number }) {
  return (
    <dl className="grid gap-x-4 gap-y-2 text-base" style={{ gridTemplateColumns: `${labelWidth}px 1fr` }}>
      {rows.map(([k, v, tech]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className={cn("[overflow-wrap:anywhere]", tech && "tech")}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function InstanceInformation({ i }: { i: InstanceDetail }) {
  const cfg = i.connection?.config ?? {};
  const { db, username, ...otherConfig } = cfg as Record<string, unknown>;
  const rows: KV[] = [
    ["Name", i.name, true],
    ["System", capitalize(i.system)],
    ["Version", `${i.version}${i.edition ? ` (${i.edition})` : ""}`],
    ["Interface", formatInterface(i.interface, i.connection?.connector_type)],
    ["Environment", i.environment],
    ["Organization", i.organization_name],
    ["Endpoint", i.connection?.endpoint ?? "—", true],
    ...(db !== undefined ? ([["Database", String(db), true]] as KV[]) : []),
    ...(username !== undefined ? ([["Username", String(username), true]] as KV[]) : []),
    ...Object.entries(otherConfig).map(([k, v]) => [capitalize(k), String(v), true] as KV),
    [
      "Password",
      i.connection?.has_secret ? (
        <span>
          ••••••••• <span className="whitespace-nowrap text-muted-foreground">(stored securely)</span>
        </span>
      ) : (
        "—"
      ),
    ],
    ["Created", formatDate(i.created_at)],
  ];
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Instance Information</CardTitle>
        <Tooltip>
          <TooltipTrigger render={<span tabIndex={0} />}>
            <Button variant="outline" size="sm" disabled>
              <Pencil /> Edit
            </Button>
          </TooltipTrigger>
          <TooltipContent>Editing is not available yet (no update API in V1)</TooltipContent>
        </Tooltip>
      </CardHeader>
      <CardContent>
        <KeyValues rows={rows} />
      </CardContent>
    </Card>
  );
}

const TEST_INFO_LABEL: Record<string, string> = {
  server_version: "Server version",
  server_serie: "Server serie",
  protocol_version: "Protocol version",
  uid: "UID",
  db: "Database",
  username: "Username",
};

function ConnectionDetails({ i }: { i: InstanceDetail }) {
  const c = i.connection;
  const st = connectionState(i);
  const info = c?.last_test_info ?? {};
  const known = Object.keys(TEST_INFO_LABEL).filter((k) => k in info);
  const others = Object.keys(info).filter((k) => !(k in TEST_INFO_LABEL));
  const rows: KV[] = [
    [
      "Status",
      <StatusBadge key="s" tone={st.tone} className="text-base">
        {st.label}
      </StatusBadge>,
    ],
    ...[...known, ...others].map((k) => [TEST_INFO_LABEL[k] ?? k, String(info[k]), true] as KV),
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Connection Details</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <KeyValues rows={rows} labelWidth={125} />
        {c?.last_test_error && (
          <div className="rounded-md border border-danger/35 bg-danger/10 p-2 font-mono text-xs text-danger">
            {c.last_test_error}
          </div>
        )}
        {!c?.last_test_at && <p className="meta">Never tested. Run Test Connection.</p>}
      </CardContent>
    </Card>
  );
}

const CAPABILITY_GROUPS = [
  ["VERIFIED", "Verified"],
  ["DECLARED", "Declared"],
  ["UNAVAILABLE", "Unavailable"],
  ["UNKNOWN", "Unknown"],
] as const;

const CAPABILITY_ORDER = [
  "READ",
  "SEARCH",
  "BULK_READ",
  "SCHEMA_DISCOVERY",
  "CREATE",
  "UPDATE",
  "DELETE",
  "BULK_WRITE",
  "EVENTS",
];

const CAPABILITY_LABEL: Record<string, string> = {
  READ: "Search and read records",
  SEARCH: "Search with filters",
  BULK_READ: "Read multiple records",
  SCHEMA_DISCOVERY: "Read metadata / structure",
  CREATE: "Create records",
  UPDATE: "Update records",
  DELETE: "Delete records",
  BULK_WRITE: "Write multiple records",
  EVENTS: "Receive change events",
};

function capabilityText(c: CapabilityOut, connector?: string | null): string {
  switch (c.status) {
    case "VERIFIED":
      return `${CAPABILITY_LABEL[c.capability] ?? c.capability} (tested)`;
    case "DECLARED":
      return "Declared by connector (not tested)";
    case "UNAVAILABLE":
      return connector ? `Not supported by ${connector}` : "Not supported";
    default:
      return c.detail ?? "Unknown";
  }
}

function Capabilities({ i }: { i: InstanceDetail }) {
  const rank = (c: CapabilityOut) => {
    const n = CAPABILITY_ORDER.indexOf(c.capability);
    return n === -1 ? CAPABILITY_ORDER.length : n;
  };
  return (
    <Card className="lg:col-span-2 2xl:col-span-1">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Capabilities</CardTitle>
        <Tooltip>
          <TooltipTrigger className="text-muted-foreground hover:text-foreground" aria-label="Help">
            <CircleHelp className="size-4" />
          </TooltipTrigger>
          <TooltipContent className="max-w-sm">
            Verified: the operation was actually run, read-only. Declared: announced by the connector, never run
            (no writes to an instance in V1). Hover a row to see the technical evidence.
          </TooltipContent>
        </Tooltip>
      </CardHeader>
      <CardContent className="space-y-3">
        {CAPABILITY_GROUPS.map(([status, label]) => {
          const caps = i.capabilities
            .filter((c) => c.status === status)
            .sort((a, b) => rank(a) - rank(b) || a.capability.localeCompare(b.capability));
          if (caps.length === 0) return null;
          return (
            <div key={status}>
              <div className="item-title mb-2">
                {label} ({caps.length})
              </div>
              <div className="space-y-1">
                {caps.map((c) => (
                  <div
                    key={c.capability}
                    className="grid grid-cols-[150px_1fr] items-center gap-4 border-b border-border/60 pb-1 last:border-0"
                    title={c.detail ?? undefined}
                  >
                    <Pill variant="neutral" mono tone={CAPABILITY_TONE[c.status] ?? "neutral"} className="w-full">
                      {c.capability}
                    </Pill>
                    <span className="meta">
                      {capabilityText(c, i.connection?.connector_type)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
        {i.capabilities.length === 0 && (
          <p className="meta">Unknown until Test Connection is run.</p>
        )}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Schéma                                                             */
/* ------------------------------------------------------------------ */

function SchemaCard({
  i,
  selected,
  onSelect,
  onDiscover,
  discovering,
}: {
  i: InstanceDetail;
  selected?: number;
  onSelect: (n: number) => void;
  onDiscover: () => void;
  discovering: boolean;
}) {
  const last = i.last_discovery;
  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="flex items-center gap-2.5">
              <Database className="size-5 text-muted-foreground" />
              <h2 className="section-title">Schema</h2>
            </div>
            {last && (
              <div className="flex flex-wrap items-end gap-x-12 gap-y-3">
                <Stat label="Latest snapshot" value={`#${last.sequence} · ${formatDate(last.discovered_at)}`} plain />
                <Stat label="Entities" value={formatInt(last.entity_count)} />
                <Stat label="Fields" value={formatInt(last.field_count)} />
                <Stat label="Relations" value={formatInt(last.relation_count)} />
                <Stat label="Checksum" value={last.checksum.slice(0, 12)} mono />
                <div className="hidden h-10 w-px bg-border md:block" />
                <Stat label="Baseline" value={formatBaseline(last.base_snapshot_label)} plain />
              </div>
            )}
          </div>
          <div className="flex flex-col items-end gap-2">
            <div className="flex gap-2">
              <Button variant="outline" size="lg" className="h-10 px-4" onClick={onDiscover} disabled={discovering}>
                {discovering ? "Discovering…" : "Discover Schema"}
              </Button>
              {last && (
                <DropdownMenu>
                  <DropdownMenuTrigger
                    className={cn(buttonVariants({ variant: "outline" }), "size-10")}
                    aria-label="Schema actions"
                  >
                    <MoreHorizontal />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={() => navigator.clipboard?.writeText(last.checksum)}>
                      <Copy /> Copy full checksum
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
            {last && (
              <Link
                href={`/snapshots/${last.base_snapshot_id}`}
                className={cn(buttonVariants({ variant: "outline" }), "h-9 px-3")}
              >
                View baseline <ArrowRight />
              </Link>
            )}
            {last && (
              <Link href={`/instances/${i.id}/catalog`} className={cn(buttonVariants(), "h-9 px-3")}>
                Product Catalog Analysis <ArrowRight />
              </Link>
            )}
            {last && (
              <Link
                href={`/instances/${i.id}/product-domain`}
                className={cn(buttonVariants({ variant: "outline" }), "h-9 px-3")}
              >
                Product domain <ArrowRight />
              </Link>
            )}
          </div>
        </div>

        {!last && <p className="meta">No snapshot yet. Run Discover Schema.</p>}

        {i.snapshots.length > 0 && (
          <div>
            <h3 className="item-title mb-1">Snapshot history</h3>
            <table className="w-full text-base tabular-nums">
              <thead>
                <tr className="meta border-b text-left">
                  {["#", "Date", "Entities", "Fields", "Relations", "Checksum", "Baseline", ""].map((h, k) => (
                    <th key={k} className="px-3 py-2 font-normal">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...i.snapshots].reverse().map((s) => (
                  <tr
                    key={s.id}
                    onClick={() => onSelect(s.sequence)}
                    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(s.sequence)}
                    tabIndex={0}
                    aria-selected={selected === s.sequence}
                    title="Show the diff of this snapshot"
                    className={cn(
                      "cursor-pointer border-b last:border-0 hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none",
                      selected === s.sequence && "bg-muted/70 hover:bg-muted/70",
                    )}
                  >
                    <td className="tech px-3 py-2">#{s.sequence}</td>
                    <td className="px-3 py-2">{formatDate(s.discovered_at)}</td>
                    <td className="px-3 py-2">{formatInt(s.entity_count)}</td>
                    <td className="px-3 py-2">{formatInt(s.field_count)}</td>
                    <td className="px-3 py-2">{formatInt(s.relation_count)}</td>
                    <td className="tech px-3 py-2">{s.checksum.slice(0, 12)}</td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {formatBaseline(s.base_snapshot_label)}
                    </td>
                    <td className="px-3 py-1.5">
                      {s.sequence === last?.sequence && <Pill tone="success">Latest</Pill>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, mono, plain }: { label: string; value: string; mono?: boolean; plain?: boolean }) {
  return (
    <div>
      <div className="meta">{label}</div>
      <div className={cn(!plain && !mono && "text-xl font-bold tabular-nums", mono && "tech text-base", plain && "font-medium")}>
        {value}
      </div>
    </div>
  );
}
