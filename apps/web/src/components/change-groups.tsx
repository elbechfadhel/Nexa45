"use client";

/** Rendu d'une liste de changements déjà calculés par l'API (Version Compare, Instance Diff) :
 *  groupés par entité, filtre par type (et par origine si fournie), REMOVED en tête. Aucun calcul de diff. */
import { ORIGIN_TONE, OriginBadge, ORIGINS } from "@/components/origin";
import { CHANGE_TONE, ChangeBadge, formatInt } from "@/components/registry";
import { StatusBadge, type Tone } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useUrlState } from "@/lib/use-url-state";
import { cn } from "@/lib/utils";

export type ChangeLike = {
  object_type: string;
  entity: string;
  path: string;
  change_type: string; // ADDED | REMOVED | CHANGED | UNCHANGED (valeurs de l'API)
  details: { [key: string]: unknown };
  origin?: string;
  reason?: string | null;
};

const CHANGE_TYPES = ["ADDED", "REMOVED", "CHANGED"] as const;
type ChangeType = (typeof CHANGE_TYPES)[number];

const TYPE_SIGN: Record<ChangeType, string> = { ADDED: "+", REMOVED: "−", CHANGED: "~" };

/** Bouton de filtre : bordure neutre, badge coloré si actif ; gris et barré si masqué. */
function FilterToggle({
  on,
  tone,
  onClick,
  children,
}: {
  on: boolean;
  tone: Tone;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      className={cn(
        "rounded-md border px-2.5 py-1 transition-colors hover:bg-muted",
        !on && "border-dashed opacity-60",
      )}
    >
      <StatusBadge mono tone={on ? tone : "neutral"} className={cn(!on && "font-normal line-through")}>
        {children}
      </StatusBadge>
    </button>
  );
}

/** Regroupe les changements (déjà calculés par l'API) par entité, dans l'ordre reçu. */
function groupByEntity(changes: ChangeLike[]) {
  const groups = new Map<string, { head?: ChangeLike; items: ChangeLike[] }>();
  for (const c of changes) {
    const g = groups.get(c.entity) ?? { items: [] };
    if (c.object_type === "entity") g.head = c;
    else g.items.push(c);
    groups.set(c.entity, g);
  }
  return [...groups.entries()];
}

/** Affichage : filtre par type, REMOVED en tête de chaque entité (tri stable, sinon ordre de l'API). */
function visibleGroups(changes: ChangeLike[], types: Set<string>) {
  return groupByEntity(changes).flatMap(([name, g]) => {
    const items = g.items
      .filter((c) => types.has(c.change_type))
      .sort((a, b) => Number(b.change_type === "REMOVED") - Number(a.change_type === "REMOVED"));
    const headVisible = !!g.head && g.head.change_type !== "CHANGED" && types.has(g.head.change_type);
    return items.length > 0 || headVisible ? [{ name, head: g.head, items }] : [];
  });
}

export function ChangeGroups({
  changes,
  counts,
  header,
  showOrigin = false,
}: {
  changes: ChangeLike[];
  counts: { [key: string]: number };
  header?: React.ReactNode;
  showOrigin?: boolean;
}) {
  const url = useUrlState();
  const raw = url.get("types");
  const types = new Set<string>(raw ? raw.split(",").filter(Boolean) : CHANGE_TYPES);
  const toggle = (t: ChangeType) => {
    const next = new Set(types);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    const all = CHANGE_TYPES.every((x) => next.has(x));
    url.set({ types: all ? undefined : CHANGE_TYPES.filter((x) => next.has(x)).join(",") || "none" });
  };
  const rawOrigins = url.get("origins");
  const origins = new Set<string>(rawOrigins ? rawOrigins.split(",").filter(Boolean) : ORIGINS);
  const toggleOrigin = (o: string) => {
    const next = new Set(origins);
    if (next.has(o)) next.delete(o);
    else next.add(o);
    const all = ORIGINS.every((x) => next.has(x));
    url.set({ origins: all ? undefined : ORIGINS.filter((x) => next.has(x)).join(",") || "none" });
  };
  const originCount = (o: string) =>
    changes.filter((c) => c.origin === o && !(c.object_type === "entity" && c.change_type === "CHANGED")).length;
  const filtered = showOrigin ? changes.filter((c) => !c.origin || origins.has(c.origin)) : changes;
  const groups = visibleGroups(filtered, types);
  const shown = groups.reduce((n, g) => n + g.items.length, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        {header}
        <div className="flex gap-1.5" role="group" aria-label="Filter by change type">
          {CHANGE_TYPES.map((t) => (
            <FilterToggle key={t} on={types.has(t)} tone={CHANGE_TONE[t]} onClick={() => toggle(t)}>
              {TYPE_SIGN[t]}
              {formatInt(counts[t] ?? 0)} {t}
            </FilterToggle>
          ))}
        </div>
        {showOrigin && (
          <div className="flex gap-1.5" role="group" aria-label="Filter by origin">
            {ORIGINS.map((o) => (
              <FilterToggle key={o} on={origins.has(o)} tone={ORIGIN_TONE[o]} onClick={() => toggleOrigin(o)}>
                {o} {formatInt(originCount(o))}
              </FilterToggle>
            ))}
          </div>
        )}
        <span className="meta">
          {groups.length} entities · {formatInt(shown)} rows shown
        </span>
      </div>
      {groups.length === 0 && <p className="meta">No difference for this filter.</p>}
      {groups.map(({ name, head, items }) => (
        <Card key={name} size="sm" className="gap-0 py-0">
          <CardHeader className="flex flex-row items-center gap-3 border-b py-3">
            {head && <ChangeBadge type={head.change_type} />}
            <CardTitle>
              <span className="tech text-base font-semibold">{name}</span>
            </CardTitle>
            {showOrigin && head?.origin && head.change_type !== "CHANGED" && (
              <OriginBadge origin={head.origin} title={head.reason} />
            )}
            {head && <span className="meta">{summary(head)}</span>}
          </CardHeader>
          {items.length > 0 && (
            <CardContent className="divide-y p-0">
              {items.map((c) => (
                <ChangeRow key={c.path} change={c} entity={name} showOrigin={showOrigin} />
              ))}
            </CardContent>
          )}
        </Card>
      ))}
    </div>
  );
}

function summary(head: ChangeLike) {
  const d = head.details as { fields?: number; changes?: number; module?: string };
  if (head.change_type === "CHANGED") return `${d.changes} change(s)`;
  return [d.fields !== undefined && `${d.fields} fields`, d.module && `module ${d.module}`].filter(Boolean).join(" · ");
}

function ChangeRow({ change, entity, showOrigin }: { change: ChangeLike; entity: string; showOrigin: boolean }) {
  // Chemin relatif à l'entité : « .email » (champ) ou « [country_id->res.country] » (relation)
  const rel = change.path.startsWith(entity) ? change.path.slice(entity.length) : change.path;
  const name = rel.startsWith(".") ? rel.slice(1) : rel;
  const details = Object.entries(change.details);
  return (
    <div
      className={cn(
        "grid items-start gap-3 px-4 py-2 text-sm",
        showOrigin ? "grid-cols-[88px_104px_minmax(180px,280px)_1fr]" : "grid-cols-[88px_minmax(180px,280px)_1fr]",
      )}
    >
      <ChangeBadge type={change.change_type} />
      {showOrigin && (change.origin ? <OriginBadge origin={change.origin} title={change.reason} /> : <span />)}
      <span className="tech break-all">
        {change.object_type === "relation" && <span className="text-muted-foreground">relation </span>}
        {name}
      </span>
      <div className="space-y-0.5">
        {showOrigin && change.reason && <div className="meta italic">{change.reason}</div>}
        {details.map(([k, v]) => (
          <div key={k} className="text-[0.8125rem]">
            <span className="text-muted-foreground">{k} </span>
            {isFromTo(v) ? (
              <>
                <Value v={v.from} className="text-danger line-through decoration-danger/40" />
                <span className="px-1.5 text-muted-foreground">→</span>
                <Value v={v.to} className="text-success" />
              </>
            ) : (
              <Value v={v} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

const isFromTo = (v: unknown): v is { from: unknown; to: unknown } =>
  typeof v === "object" && v !== null && "from" in v && "to" in v;

function Value({ v, className }: { v: unknown; className?: string }) {
  let text: string;
  if (v === null || v === undefined) text = "∅";
  else if (Array.isArray(v))
    text = v
      .map((x) =>
        typeof x === "object" && x !== null && "value" in x
          ? `${(x as { value: unknown }).value}${"label" in x ? ` (${(x as { label: unknown }).label})` : ""}`
          : String(x),
      )
      .join(", ");
  else if (typeof v === "object") text = JSON.stringify(v);
  else text = String(v);
  return <span className={`font-mono ${className ?? ""}`}>{text}</span>;
}
