"use client";

/** Product Domain d'une instance (vue technique « Advanced », lecture seule).
 *  Tout vient de l'API (domaine effectif, champs filtrés, historique) : aucune règle de domaine ici,
 *  seulement la mise en forme. La page supplier « Product Catalog Analysis » viendra en V1b. */
import { ArrowRight, Boxes } from "lucide-react";
import Link from "next/link";

import { ORIGIN_TONE, OriginBadge, ORIGINS } from "@/components/origin";
import { ErrorBox, formatDate, formatInt, LoadingRows } from "@/components/registry";
import { Pill, StatusBadge, type Tone } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  ApiError,
  type DomainFieldDef,
  type DomainSnapshotHistoryOut,
  type DomainSnapshotOut,
  type Schemas,
} from "@/lib/api/client";
import {
  useAnalyzeProductDomain,
  useInstance,
  useProductDomain,
  useProductDomainFields,
  useProductDomainHistory,
} from "@/lib/api/hooks";
import { useUrlState } from "@/lib/use-url-state";
import { cn } from "@/lib/utils";

type FieldGroup = Schemas["FieldGroup"];
type Relevance = Schemas["Relevance"];

// Valeurs des enums de l'API (schema.d.ts), dans l'ordre d'affichage.
const GROUPS: FieldGroup[] = [
  "identity",
  "commercial",
  "description",
  "classification",
  "logistics",
  "variants",
  "supplier",
  "media",
  "other",
  "technical",
];
const RELEVANCES: Relevance[] = ["RELEVANT", "POSSIBLE", "NOT_RELEVANT", "UNRESOLVED"];
const ROLES: Schemas["EntityRole"][] = [
  "PRIMARY",
  "VARIANT",
  "CATEGORY",
  "ATTRIBUTE",
  "ATTRIBUTE_VALUE",
  "ATTRIBUTE_LINE",
  "VARIANT_VALUE",
  "UOM",
  "SUPPLIER_INFO",
  "MEDIA",
  "OTHER",
];
const MEDIA_ROLES = ["main", "derivative", "variant", "additional"] as const;

// UNRESOLVED = à trancher par Semantic Enrichment (V1b)
const RELEVANCE_TONE: Record<Relevance, Tone> = {
  RELEVANT: "success",
  POSSIBLE: "neutral",
  NOT_RELEVANT: "neutral",
  UNRESOLVED: "changed",
};
const MEDIA_TONE: Record<string, Tone> = { main: "success", derivative: "neutral", variant: "info", additional: "info" };

const label = (v: string) => v.replaceAll("_", " ");

export function ProductDomainView({ id }: { id: string }) {
  const url = useUrlState();
  const selectedId = url.get("domain");
  const instance = useInstance(id);
  const domain = useProductDomain(id, selectedId);
  const history = useProductDomainHistory(id);
  const analyze = useAnalyzeProductDomain(id);

  const notAnalyzed = domain.error instanceof ApiError && domain.error.status === 404 && !selectedId;
  const runAnalyze = () => analyze.mutate(undefined, { onSuccess: () => url.set({ domain: undefined }) });

  if (instance.error) return <ErrorBox error={instance.error} />;
  if (!instance.data) return <LoadingRows rows={10} />;
  const i = instance.data;
  const d = domain.data;

  return (
    <div className="page-stack">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-4">
            <h1 className="hero-badge">Product Domain</h1>
            <Pill variant="neutral" tone="info">
              Advanced
            </Pill>
          </div>
          <p className="page-description text-muted-foreground">
            What belongs to the catalog in this instance: standard definition, extensions and missing standard
            elements. Read-only, computed from persisted snapshots.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {(history.data?.length ?? 0) > 0 && (
            <HistorySelect
              items={history.data!}
              value={d?.id}
              onChange={(v) => url.set({ domain: v === history.data![0].id ? undefined : v })}
            />
          )}
          <Button
            size="lg"
            className="h-10 px-4"
            onClick={runAnalyze}
            disabled={analyze.isPending || !i.last_discovery}
            title={i.last_discovery ? undefined : "Run Discover Schema on the instance first"}
          >
            {analyze.isPending ? "Analyzing…" : "Analyze"}
          </Button>
        </div>
      </div>

      {!!analyze.error && <ErrorBox error={analyze.error} />}
      {analyze.data && (
        <p className="meta">
          {analyze.data.created
            ? `New domain snapshot created for instance snapshot #${analyze.data.instance_schema_snapshot_sequence}.`
            : `Already analyzed: existing domain snapshot returned (instance snapshot #${analyze.data.instance_schema_snapshot_sequence}).`}
        </p>
      )}

      {notAnalyzed ? (
        <Card>
          <CardContent className="meta">
            {i.last_discovery
              ? "No product domain analyzed yet. Run Analyze."
              : "No schema snapshot yet. Run Discover Schema on the instance, then Analyze."}
          </CardContent>
        </Card>
      ) : domain.error ? (
        <ErrorBox error={domain.error} />
      ) : !d ? (
        <LoadingRows rows={10} />
      ) : (
        <>
          <DomainHeader d={d} />
          <div className="grid gap-4.5 xl:grid-cols-[1.1fr_0.9fr]">
            <EntitiesCard d={d} />
            <MediaCard d={d} />
          </div>
          <ExtensionsCard d={d} />
          <MissingCard d={d} />
          <FieldsCard instanceId={id} d={d} />
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* En-tête : snapshots liés, définition, historique                    */
/* ------------------------------------------------------------------ */

function historyLabel(h: DomainSnapshotHistoryOut, latest: boolean) {
  return `#${h.instance_schema_snapshot_sequence} · ${formatDate(h.analyzed_at)}${latest ? " (latest)" : ""}`;
}

function HistorySelect({
  items,
  value,
  onChange,
}: {
  items: DomainSnapshotHistoryOut[];
  value?: string;
  onChange: (v: string) => void;
}) {
  const options = items.map((h, k) => ({ value: h.id, label: historyLabel(h, k === 0) }));
  return (
    <Select items={options} value={value ?? null} onValueChange={(v) => v && onChange(v)}>
      <SelectTrigger className="h-10 w-64" aria-label="Domain snapshot history">
        <SelectValue placeholder="History" />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function Stat({ label, children, tech }: { label: string; children: React.ReactNode; tech?: boolean }) {
  return (
    <div>
      <div className="meta">{label}</div>
      <div className={cn("font-medium", tech && "tech")}>{children}</div>
    </div>
  );
}

function Count({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <div className="meta">{label}</div>
      <div className={cn("text-xl font-bold tabular-nums", className)}>{value}</div>
    </div>
  );
}

function DomainHeader({ d }: { d: DomainSnapshotOut }) {
  const s = d.snapshot;
  const sum = d.summary;
  const declared = s.entities.length;
  const present = declared - sum.absent_entities.length;
  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2.5">
          <Boxes className="size-5 text-muted-foreground" />
          <h2 className="section-title">{s.domain_key}</h2>
        </div>
        <div className="flex flex-wrap items-end gap-x-10 gap-y-3">
          <Stat label="Instance snapshot" tech>
            #{d.instance_schema_snapshot_sequence} · {s.instance_schema_snapshot_checksum.slice(0, 12)}
          </Stat>
          <Stat label="Baseline">
            <Link href={`/snapshots/${s.base_schema_snapshot_id}`} className="inline-flex items-center gap-1 hover:underline">
              Standard snapshot <ArrowRight className="size-4" />
            </Link>
          </Stat>
          <Stat label="Standard definition" tech>
            @{s.definition_version} · {s.standard_definition_checksum.slice(0, 12)}
          </Stat>
          <Stat label="Analyzed">{formatDate(s.analyzed_at)}</Stat>
          <Stat label="Domain checksum" tech>
            {s.checksum.slice(0, 12)}
          </Stat>
        </div>
        <div className="flex flex-wrap items-end gap-x-12 gap-y-3 border-t pt-4">
          <Count label="Entities" value={`${present} / ${declared}`} />
          <Count label="Fields" value={formatInt(sum.field_count)} />
          <Count label="Extensions" value={formatInt(sum.extension_count)} className={sum.extension_count ? "text-warning" : undefined} />
          <Count label="Missing standard" value={formatInt(sum.missing_count)} className={sum.missing_count ? "text-danger" : undefined} />
          <Count label="Media fields" value={formatInt(sum.media.length)} />
        </div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Entités, médias                                                     */
/* ------------------------------------------------------------------ */

function EntitiesCard({ d }: { d: DomainSnapshotOut }) {
  const rank = (r: string) => ROLES.indexOf(r as Schemas["EntityRole"]);
  const entities = [...d.snapshot.entities].sort((a, b) => rank(a.role) - rank(b.role));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Entities by role</CardTitle>
      </CardHeader>
      <CardContent>
        <table className="w-full text-base">
          <thead>
            <tr className="meta border-b text-left">
              <th className="py-2 pr-3 font-normal">Role</th>
              <th className="px-3 py-2 font-normal">Entity</th>
              <th className="px-3 py-2 font-normal">Name</th>
              <th className="py-2 pl-3 font-normal">Status</th>
            </tr>
          </thead>
          <tbody>
            {entities.map((e) => (
              <tr key={e.technical_name} className={cn("border-b last:border-0", !e.present && "text-muted-foreground")}>
                <td className="py-2 pr-3">
                  <Pill variant="neutral" tone="neutral" mono>
                    {e.role}
                  </Pill>
                </td>
                <td className="tech px-3 py-2">{e.technical_name}</td>
                <td className="px-3 py-2">{e.display_name ?? "—"}</td>
                <td className="py-2 pl-3">
                  <StatusBadge tone={e.present ? "success" : "danger"}>{e.present ? "Present" : "Absent"}</StatusBadge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function MediaCard({ d }: { d: DomainSnapshotOut }) {
  const media = d.summary.media;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Media ({media.length})</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {media.length === 0 && <p className="meta">No image field detected.</p>}
        {MEDIA_ROLES.map((role) => {
          const items = media.filter((m) => m.media_role === role);
          if (items.length === 0) return null;
          return (
            <div key={role}>
              <div className="mb-1.5 flex items-center gap-2">
                <Pill tone={MEDIA_TONE[role]}>{role}</Pill>
                <span className="meta">{items.length}</span>
              </div>
              <ul className="grid gap-x-4 [overflow-wrap:anywhere]">
                {items.map((m) => (
                  <li key={m.path} className="tech">
                    {m.path}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Tableau de champs (partagé : extensions, champs par groupe)          */
/* ------------------------------------------------------------------ */

function fieldType(f: DomainFieldDef) {
  const t = f.native_type ?? f.data_type ?? "—";
  return f.relation_target ? `${t} → ${f.relation_target}` : t;
}

function FieldTable({ fields, provenance = false }: { fields: DomainFieldDef[]; provenance?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-base">
        <thead>
          <tr className="meta border-b text-left">
            <th className="py-2 pr-3 font-normal">Field</th>
            <th className="px-3 py-2 font-normal">Label</th>
            <th className="px-3 py-2 font-normal">Type</th>
            <th className="px-3 py-2 font-normal">Origin</th>
            <th className="px-3 py-2 font-normal">Relevance</th>
            <th className="py-2 pl-3 font-normal">{provenance ? "Modules / reason" : "Flags"}</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => (
            <tr key={`${f.entity_technical_name}.${f.field_technical_name}`} className="border-b align-top last:border-0">
              <td className="py-1.5 pr-3">
                <span className="tech text-muted-foreground">{f.entity_technical_name}.</span>
                <span className={cn("tech", f.technical && "text-muted-foreground")}>{f.field_technical_name}</span>
              </td>
              <td className="px-3 py-1.5">{f.label ?? "—"}</td>
              <td className="tech px-3 py-1.5 text-muted-foreground">{fieldType(f)}</td>
              <td className="px-3 py-1.5">
                <OriginBadge origin={f.origin} title={f.origin_reason} />
              </td>
              <td className="px-3 py-1.5">
                <StatusBadge mono tone={RELEVANCE_TONE[f.relevance]}>
                  {f.relevance}
                </StatusBadge>
              </td>
              <td className="meta py-1.5 pl-3">
                {provenance ? (
                  <>
                    {f.modules.length > 0 && <span className="tech">{f.modules.join(", ")}</span>}
                    {f.origin_reason && <div>{f.origin_reason}</div>}
                  </>
                ) : (
                  <span className="flex flex-wrap gap-1">
                    {f.required && <Pill tone="danger">req</Pill>}
                    {f.media_role && <Pill tone={MEDIA_TONE[f.media_role]}>{f.media_role}</Pill>}
                    {f.listed && <Pill tone="neutral">listed</Pill>}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExtensionsCard({ d }: { d: DomainSnapshotOut }) {
  const ext = d.snapshot.extensions;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Extensions ({ext.length})</CardTitle>
        <p className="meta">
          CUSTOM and THIRD_PARTY fields added on entities of the domain. Relevance stays UNRESOLVED until
          semantic enrichment (V1b).
        </p>
      </CardHeader>
      <CardContent>
        {ext.length === 0 ? <p className="meta">No extension.</p> : <FieldTable fields={ext} provenance />}
      </CardContent>
    </Card>
  );
}

function MissingCard({ d }: { d: DomainSnapshotOut }) {
  const missing = d.snapshot.missing_standard;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Missing standard ({missing.length})</CardTitle>
      </CardHeader>
      <CardContent>
        {missing.length === 0 ? (
          <p className="meta">No standard element of the domain is missing from this instance.</p>
        ) : (
          <table className="w-full text-base">
            <thead>
              <tr className="meta border-b text-left">
                <th className="py-2 pr-3 font-normal">Type</th>
                <th className="px-3 py-2 font-normal">Element</th>
                <th className="px-3 py-2 font-normal">Role / group</th>
                <th className="py-2 pl-3 font-normal">Reason</th>
              </tr>
            </thead>
            <tbody>
              {missing.map((m) => (
                <tr key={`${m.entity_technical_name}.${m.field_technical_name ?? ""}`} className="border-b last:border-0">
                  <td className="py-1.5 pr-3">
                    <StatusBadge tone="danger">{m.object_type}</StatusBadge>
                  </td>
                  <td className="tech px-3 py-1.5">
                    {m.entity_technical_name}
                    {m.field_technical_name && `.${m.field_technical_name}`}
                  </td>
                  <td className="tech px-3 py-1.5">{m.role ?? m.group ?? "—"}</td>
                  <td className="meta py-1.5 pl-3">{m.reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Champs : filtres dans l'URL (?group=&origin=&relevance=), filtrage par l'API */
/* ------------------------------------------------------------------ */

function FilterRow<T extends string>({
  name,
  values,
  counts,
  active,
  tone,
  onChange,
}: {
  name: string;
  values: readonly T[];
  counts: Record<string, number>;
  active?: T;
  tone: (v: T) => Tone;
  onChange: (v: T | undefined) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="meta w-20">{name}</span>
      {values
        .filter((v) => counts[v])
        .map((v) => (
          <button
            key={v}
            onClick={() => onChange(active === v ? undefined : v)}
            aria-pressed={active === v}
            className={cn(
              "rounded-md border px-2 py-0.5 transition-colors hover:bg-muted",
              active === v && "border-brand bg-muted",
              active && active !== v && "opacity-60",
            )}
          >
            <StatusBadge mono tone={tone(v)}>
              {label(v)} <span className="font-normal opacity-70">{counts[v]}</span>
            </StatusBadge>
          </button>
        ))}
    </div>
  );
}

function FieldsCard({ instanceId, d }: { instanceId: string; d: DomainSnapshotOut }) {
  const url = useUrlState();
  const pick = <T extends string>(k: string, values: readonly T[]) => {
    const v = url.get(k);
    return values.includes(v as T) ? (v as T) : undefined;
  };
  const filters = {
    group: pick("group", GROUPS),
    origin: pick("origin", ORIGINS),
    relevance: pick("relevance", RELEVANCES),
  };
  const fields = useProductDomainFields(instanceId, d.id, filters);
  const sum = d.summary;
  const anyFilter = !!(filters.group || filters.origin || filters.relevance);

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>
          Fields ({fields.data ? formatInt(fields.data.length) : "…"}
          {anyFilter && ` of ${formatInt(sum.field_count)}`})
        </CardTitle>
        {anyFilter && (
          <Button variant="outline" size="sm" onClick={() => url.set({ group: undefined, origin: undefined, relevance: undefined })}>
            Clear filters
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <FilterRow
            name="Group"
            values={GROUPS}
            counts={sum.fields_by_group}
            active={filters.group}
            tone={() => "neutral"}
            onChange={(v) => url.set({ group: v })}
          />
          <FilterRow
            name="Origin"
            values={ORIGINS}
            counts={sum.fields_by_origin}
            active={filters.origin}
            tone={(v) => ORIGIN_TONE[v]}
            onChange={(v) => url.set({ origin: v })}
          />
          <FilterRow
            name="Relevance"
            values={RELEVANCES}
            counts={sum.fields_by_relevance}
            active={filters.relevance}
            tone={(v) => RELEVANCE_TONE[v]}
            onChange={(v) => url.set({ relevance: v })}
          />
          <p className="meta pl-20">Counts are for the whole domain; filters combine.</p>
        </div>
        {fields.error ? (
          <ErrorBox error={fields.error} />
        ) : !fields.data ? (
          <LoadingRows />
        ) : fields.data.length === 0 ? (
          <p className="meta">No field matches these filters.</p>
        ) : (
          <div className={cn("space-y-3", fields.isPlaceholderData && "opacity-60")}>
            {GROUPS.map((g) => {
              const items = fields.data.filter((f) => f.group === g);
              if (items.length === 0) return null;
              // « other » et « technical » repliés par défaut (volumineux), sauf s'ils sont filtrés
              const open = anyFilter || (g !== "other" && g !== "technical");
              return (
                <details key={`${g}-${open}`} open={open} className="group/fg rounded-card-sm border px-4 py-2">
                  <summary className="item-title flex cursor-pointer items-center gap-2 py-1">
                    {g} <span className="meta">{items.length}</span>
                  </summary>
                  <FieldTable fields={items} />
                </details>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
