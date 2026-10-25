"use client";

/** Carte « Instance Diff » : compteurs et classification (résumé calculé par le Core), puis la liste des
 *  changements reçus de l'API, à plat. Aucun calcul de diff ici : on regroupe seulement, pour l'affichage,
 *  un champ et la relation qu'il porte (même entité, même nom, même type de changement). */
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  MoreVertical,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import Link from "next/link";
import { Fragment, useMemo, useState } from "react";

import { ORIGIN_ICON, ORIGIN_LABEL, ORIGIN_TONE, ORIGINS } from "@/components/origin";
import { ErrorBox, formatInt, LoadingRows } from "@/components/registry";
import { Pill, StatusBadge, toneDot, type Tone } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { InstanceDetail, SchemaDiffOut } from "@/lib/api/client";
import { useDebounced, useUrlState } from "@/lib/use-url-state";
import { cn } from "@/lib/utils";

import { capitalize, formatBaseline } from "./format";

type Change = SchemaDiffOut["changes"][number];
type Row = Change & { relation?: Change };

const TABS = ["ALL", "ADDED", "REMOVED", "CHANGED"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = { ALL: "All", ADDED: "Added", REMOVED: "Removed", CHANGED: "Changed" };

const CHANGE_PILL: Record<string, { label: string; tone: Tone }> = {
  ADDED: { label: "Added", tone: "success" },
  REMOVED: { label: "Removed", tone: "danger" },
  CHANGED: { label: "Changed", tone: "changed" },
};

// convention de couleur commune à toute la console (components/origin.tsx)
const ORIGIN: Record<string, { label: string; tone: Tone; icon: React.ReactNode }> = Object.fromEntries(
  ORIGINS.map((o) => [o, { label: ORIGIN_LABEL[o], tone: ORIGIN_TONE[o], icon: ORIGIN_ICON[o] }]),
);

const RELATION_LABEL: Record<string, string> = {
  many_to_one: "Many2one",
  one_to_many: "One2many",
  many_to_many: "Many2many",
  one_to_one: "One2one",
};

const PAGE = 50;

export function InstanceDiffCard({
  i,
  diff,
  error,
  pending,
}: {
  i: InstanceDetail;
  diff?: SchemaDiffOut;
  error: unknown;
  pending: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1">
          <CardTitle>Instance Diff</CardTitle>
          {diff && (
            <p className="meta">
              Standard {formatBaseline(diff.base_snapshot.label)} → {i.name} (snapshot #
              {diff.instance_snapshot.sequence})
            </p>
          )}
        </div>
        {diff && (
          <Button variant="outline" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Show less" : "View all"} <ArrowRight />
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        {!!error && <ErrorBox error={error} />}
        {pending && <LoadingRows rows={6} />}
        {diff && <DiffBody d={diff} showAll={showAll} />}
      </CardContent>
    </Card>
  );
}

function DiffBody({ d, showAll }: { d: SchemaDiffOut; showAll: boolean }) {
  const url = useUrlState();
  const tab = (TABS as readonly string[]).includes(url.get("tab") ?? "") ? (url.get("tab") as Tab) : "ALL";
  const rawOrigins = url.get("origins");
  const origins = new Set<string>(rawOrigins ? rawOrigins.split(",").filter(Boolean) : ORIGINS);
  const [q, setQ] = useState(url.get("q") ?? "");
  const query = useDebounced(q.trim().toLowerCase());
  const [open, setOpen] = useState<Set<string>>(new Set());

  const rows = useMemo(() => toRows(d.changes), [d.changes]);
  const addedEntities = useMemo(
    () => new Set(d.changes.filter((c) => c.object_type === "entity" && c.change_type === "ADDED").map((c) => c.entity)),
    [d.changes],
  );

  const setOrigins = (next: Set<string>) => {
    const all = ORIGINS.every((o) => next.has(o));
    url.set({ origins: all ? undefined : ORIGINS.filter((o) => next.has(o)).join(",") || "none" });
  };
  const toggleOrigin = (o: string) => {
    const next = new Set(origins);
    if (next.has(o)) next.delete(o);
    else next.add(o);
    setOrigins(next);
  };

  const base = rows.filter(
    (r) => (!r.origin || origins.has(r.origin)) && (!query || r.path.toLowerCase().includes(query)),
  );
  const count = (t: Tab) => (t === "ALL" ? base.length : base.filter((r) => r.change_type === t).length);
  const visible = tab === "ALL" ? base : base.filter((r) => r.change_type === tab);
  const shown = showAll ? visible : visible.slice(0, PAGE);
  const s = d.summary;

  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_auto_minmax(0,1fr)]">
        <div className="grid gap-3 sm:grid-cols-3">
          {(
            [
              ["Entities", s.entities],
              ["Fields", s.fields],
              ["Relations", s.relations],
            ] as const
          ).map(([label, c]) => (
            <div key={label} className="rounded-lg border px-4 py-3">
              <div className="mb-2 font-medium">{label}</div>
              <div className="tech flex flex-wrap gap-1.5 xl:flex-nowrap">
                <Pill tone="success" className="h-7 px-2.5 text-sm">
                  +{formatInt(c.added)}
                </Pill>
                <Pill tone="danger" className="h-7 px-2.5 text-sm">
                  -{formatInt(c.removed)}
                </Pill>
                <Pill tone="changed" className="h-7 px-2.5 text-sm">
                  ~{formatInt(c.changed)}
                </Pill>
              </div>
            </div>
          ))}
        </div>
        <div className="hidden w-px bg-border xl:block" />
        <div>
          <div className="mb-2 font-semibold">By classification</div>
          <div className="grid grid-cols-2 gap-2 2xl:grid-cols-4">
            {ORIGINS.map((o) => (
              <button
                key={o}
                onClick={() => toggleOrigin(o)}
                aria-pressed={origins.has(o)}
                title={origins.has(o) ? `Hide ${ORIGIN[o].label}` : `Show ${ORIGIN[o].label}`}
                className={cn(
                  "rounded-lg border px-2.5 py-2 text-left transition-colors hover:bg-muted/60",
                  !origins.has(o) && "border-dashed opacity-50",
                )}
              >
                <div className="flex items-start gap-2">
                  <span className={cn("mt-1.5 size-2.5 shrink-0 rounded-full", toneDot(ORIGIN[o].tone))} />
                  <div>
                    <div className="meta whitespace-nowrap">{ORIGIN[o].label}</div>
                    <div className="text-lg font-bold tabular-nums">{formatInt(s.by_origin[o] ?? 0)}</div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-end justify-between gap-3 border-b">
        <div className="flex gap-1" role="tablist">
          {TABS.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => url.set({ tab: t === "ALL" ? undefined : t })}
              className={cn(
                "-mb-px border-b-2 px-4 py-2 text-sm transition-colors",
                tab === t
                  ? "border-foreground font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {TAB_LABEL[t]} ({formatInt(count(t))})
            </button>
          ))}
        </div>
        <div className="mb-2 flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                url.set({ q: e.target.value || undefined });
              }}
              placeholder="Search entity or field..."
              className="h-9 w-64 pl-9"
              aria-label="Search entity or field"
            />
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger
              className={cn(buttonVariants({ variant: "outline", size: "icon-lg" }))}
              aria-label="Filter by classification"
            >
              <SlidersHorizontal />
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuLabel>Classification</DropdownMenuLabel>
              {ORIGINS.map((o) => (
                <DropdownMenuCheckboxItem
                  key={o}
                  checked={origins.has(o)}
                  onCheckedChange={() => toggleOrigin(o)}
                  closeOnClick={false}
                >
                  {ORIGIN[o].icon}
                  {ORIGIN[o].label}
                </DropdownMenuCheckboxItem>
              ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setOrigins(new Set(ORIGINS))}>Show all</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="-mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="meta border-b text-left">
              <th className="w-8" />
              <th className="py-2 pr-3 font-normal">Change</th>
              <th className="py-2 pr-3 font-normal">Classification</th>
              <th className="py-2 pr-3 font-normal">Entity / Field</th>
              <th className="py-2 pr-3 font-normal">Details</th>
              <th className="py-2 pr-3 font-normal">Module</th>
              <th className="py-2 pr-3 font-normal">Type</th>
              <th className="w-8" />
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const isOpen = open.has(r.path + r.change_type);
              const toggle = () => {
                const next = new Set(open);
                if (isOpen) next.delete(r.path + r.change_type);
                else next.add(r.path + r.change_type);
                setOpen(next);
              };
              const inBaseline = !addedEntities.has(r.entity);
              return (
                <Fragment key={r.path + r.change_type}>
                  <tr className={cn("border-b align-top hover:bg-muted/40", isOpen && "bg-muted/40")}>
                    <td className="py-2">
                      <button
                        onClick={toggle}
                        aria-expanded={isOpen}
                        aria-label={isOpen ? "Hide details" : "Show details"}
                        className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                      >
                        {isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                      </button>
                    </td>
                    <td className="py-2 pr-3">
                      <Pill tone={CHANGE_PILL[r.change_type]?.tone ?? "neutral"}>
                        {CHANGE_PILL[r.change_type]?.label ?? r.change_type}
                      </Pill>
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      {r.origin && (
                        <span className="inline-flex items-center gap-2" title={r.reason ?? undefined}>
                          {ORIGIN[r.origin]?.icon}
                          {ORIGIN[r.origin]?.label ?? r.origin}
                        </span>
                      )}
                    </td>
                    <td className="min-w-64 py-2 pr-3 [overflow-wrap:anywhere]">{r.path}</td>
                    <td className="py-2 pr-3">
                      <Details r={r} />
                    </td>
                    <td className="py-2 pr-3 text-muted-foreground">{moduleOf(r)}</td>
                    <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">{typeOf(r)}</td>
                    <td className="py-2">
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                          aria-label="Actions"
                        >
                          <MoreVertical className="size-4" />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent>
                          <DropdownMenuItem onClick={() => navigator.clipboard?.writeText(r.path)}>
                            <Copy /> Copy path
                          </DropdownMenuItem>
                          {inBaseline && (
                            <DropdownMenuItem
                              render={
                                <Link href={`/snapshots/${d.base_snapshot.id}?entity=${encodeURIComponent(r.entity)}`} />
                              }
                            >
                              <ExternalLink /> Open in standard schema
                            </DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b bg-muted/20">
                      <td />
                      <td colSpan={7} className="py-3 pr-3">
                        <RowDetail r={r} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {visible.length === 0 && (
          <p className="meta py-6 text-center">No difference for this filter.</p>
        )}
        {!showAll && visible.length > PAGE && (
          <p className="meta pt-3 text-center">
            Showing {formatInt(PAGE)} of {formatInt(visible.length)} rows. Use View all to show everything.
          </p>
        )}
      </div>
    </>
  );
}

/** Lignes affichées : les entités modifiées sont des agrégats (déjà comptés) ; un champ absorbe sa relation. */
function toRows(changes: Change[]): Row[] {
  const relations = new Map<string, Change>();
  for (const c of changes) {
    if (c.object_type !== "relation") continue;
    const m = /^(.*)\[(.+?)->/.exec(c.path);
    if (m) relations.set(`${c.change_type}|${m[1]}.${m[2]}`, c);
  }
  const used = new Set<Change>();
  const rows: Row[] = [];
  for (const c of changes) {
    if (c.object_type === "entity" && c.change_type === "CHANGED") continue;
    if (c.object_type === "field") {
      const rel = relations.get(`${c.change_type}|${c.path}`);
      if (rel) used.add(rel);
      rows.push({ ...c, relation: rel });
    } else rows.push(c);
  }
  return rows.filter((r) => !used.has(r));
}

function targetOf(relationPath: string) {
  return /->(.+)]$/.exec(relationPath)?.[1];
}

function typeOf(r: Row): string {
  const rel = r.object_type === "relation" ? r : r.relation;
  if (rel) {
    const t = (rel.details as { relation_type?: string }).relation_type;
    return `${RELATION_LABEL[t ?? ""] ?? t ?? "Relation"} → ${targetOf(rel.path) ?? "?"}`;
  }
  const det = r.details as { data_type?: string; fields?: number; selection?: unknown };
  if (r.object_type === "entity") return det.fields !== undefined ? `Entity · ${det.fields} fields` : "Entity";
  if (det.data_type) return det.data_type === "enum" ? "Selection" : capitalize(det.data_type);
  if (det.selection) return "Selection";
  return "—";
}

function moduleOf(r: Row): string {
  const m = (r.details as { module?: string }).module;
  return m ?? (r.modules.length ? r.modules.join(", ") : "—");
}

type Option = { value: string; label?: string };
type FromTo = { from?: unknown; to?: unknown };

function Details({ r }: { r: Row }) {
  const det = r.details as Record<string, unknown>;
  if (r.change_type === "CHANGED") {
    return (
      <div className="space-y-1">
        {Object.entries(det).map(([k, v]) =>
          k === "selection" ? <SelectionChange key={k} v={v as FromTo} /> : <KeyFromTo key={k} k={k} v={v as FromTo} />,
        )}
      </div>
    );
  }
  if (r.object_type === "entity") return <KeyValue k="fields" v={det.fields} />;
  if (r.object_type === "relation") return <KeyValue k="relation" v={det.relation_type} />;
  return <KeyValue k="label" v={det.label} />;
}

function KeyValue({ k, v }: { k: string; v: unknown }) {
  if (v === undefined || v === null) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="tech">
      <span className="text-muted-foreground">{k}:</span> {String(v)}
    </span>
  );
}

function KeyFromTo({ k, v }: { k: string; v: FromTo }) {
  return (
    <div className="tech">
      <span className="text-muted-foreground">{k}:</span> <span className="text-danger">{fmt(v?.from)}</span>
      {" → "}
      <span className="text-success">{fmt(v?.to)}</span>
    </div>
  );
}

/** Mise en forme d'un détail « selection » déjà calculé par le Core : valeurs présentes d'un seul côté. */
function SelectionChange({ v }: { v: FromTo }) {
  const from = Array.isArray(v?.from) ? (v.from as Option[]) : [];
  const to = Array.isArray(v?.to) ? (v.to as Option[]) : [];
  const fromKeys = new Set(from.map((o) => o.value));
  const toKeys = new Set(to.map((o) => o.value));
  const added = to.filter((o) => !fromKeys.has(o.value));
  const removed = from.filter((o) => !toKeys.has(o.value));
  return (
    <div className="space-y-1">
      <div className="meta">Selection values modified:</div>
      <div className="flex flex-wrap gap-1">
        {added.map((o) => (
          <Chip key={`+${o.value}`} tone="success">
            + {o.value}
            {o.label && ` (${o.label})`}
          </Chip>
        ))}
        {removed.map((o) => (
          <Chip key={`-${o.value}`} tone="danger">
            − {o.value}
            {o.label && ` (${o.label})`}
          </Chip>
        ))}
        {added.length + removed.length === 0 && <span className="meta">labels / order</span>}
      </div>
    </div>
  );
}

function Chip({ tone, children }: { tone: "success" | "danger"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 font-mono text-[11px]",
        tone === "success" ? "border-success/35 bg-success/10 text-success" : "border-danger/35 bg-danger/10 text-danger",
      )}
    >
      {children}
    </span>
  );
}

function RowDetail({ r }: { r: Row }) {
  const items: [string, React.ReactNode][] = [
    ["Path", r.path],
    ["Object", r.object_type],
    ["Classification", <StatusBadge key="o" tone={ORIGIN[r.origin ?? ""]?.tone ?? "neutral"}>{r.origin ?? "—"}</StatusBadge>],
    ["Reason", r.reason ?? "—"],
    ["Modules", r.modules.length ? r.modules.join(", ") : "—"],
    ...Object.entries(r.details).map(([k, v]) => [k, fmt(v)] as [string, string]),
  ];
  if (r.relation) items.push(["Relation", `${r.relation.path} · ${fmt(r.relation.details)}`]);
  return (
    <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1 text-[0.8125rem]">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="tech break-all">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function fmt(v: unknown): string {
  if (v === undefined) return "—";
  if (v === null) return "null";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
