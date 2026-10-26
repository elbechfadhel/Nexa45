"use client";

/** Produits d'un système → système cible relié par son API : Preview → Select → Send → Results (articles 1a/1b).
 *  Deux décisions explicites, jamais devinées : la base des prix de la source (nets ou bruts) et le taux de
 *  chaque taxe de vente (valeurs admises par la cible, venues de l'API). Rien n'est écrit dans la cible avant
 *  une sélection ET une confirmation explicite (clé d'envoi générée à l'ouverture de la confirmation : un double
 *  clic renvoie le même envoi). La page ne nomme aucun système et ne décide rien : envoyable, action, statuts
 *  viennent du backend. Envoi : composants partagés `components/target-send.tsx`. */
import { ArrowRight, ChevronDown, Info, Send } from "lucide-react";
import { useMemo, useState } from "react";

import { PortalGate } from "@/components/portal";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { Pill, StatusBadge } from "@/components/status-badge";
import { Confirmation, PreviousSends, type SendHooks, SendResult } from "@/components/target-send";
import { Button } from "@/components/ui/button";
import { RadioList } from "@/components/ui/radio-list";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type PreparedProduct,
  type ProductTargetPreview,
  useLinkProduct,
  useProductMatches,
  useProductSend,
  useProductSends,
  useProductTargetPreview,
  useResolveProductUnknown,
  useResumeProductSend,
  useSendProducts,
  useSetPriceBasis,
  useSetTaxRate,
  useUnlinkProduct,
} from "@/lib/api/supplier-hooks";
import {
  actionLabel,
  filterByStatus,
  keepSelectable,
  preparationStatus,
  selectableRefs,
  sendState,
} from "@/lib/partners/target.mjs";
import { cn } from "@/lib/utils";

const FILTERS = [
  ["all", "All"],
  ["ready", "Ready"],
  ["warning", "Warning"],
  ["blocked", "Blocked"],
] as const;
const BASIS_LABEL = { net: "Net prices (without tax)", gross: "Gross prices (tax included)" } as const;
const KIND_LABEL = { goods: "Goods", service: "Service", unknown: "Unknown type" } as const;

type Ids = { systemId: string; targetId: string };

const HOOKS: SendHooks = {
  useSend: useProductSend,
  useSends: useProductSends,
  useResume: useResumeProductSend,
  useMatches: useProductMatches,
  useLink: useLinkProduct,
  useResolve: useResolveProductUnknown,
  noMatch: "No existing article found with the same article number.",
};

export function PreviewView({ systemId, targetId }: Ids) {
  return <PortalGate>{() => <Preview systemId={systemId} targetId={targetId} />}</PortalGate>;
}

function Preview({ systemId, targetId }: Ids) {
  const preview = useProductTargetPreview(systemId, targetId);
  const [filter, setFilter] = useState<string>("all");
  const [picked, setPicked] = useState<string[]>([]);
  const [confirmKey, setConfirmKey] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const send = useSendProducts(systemId, targetId);
  const allowed = useMemo(() => selectableRefs(preview.data?.products ?? []), [preview.data]);
  if (preview.error)
    return (
      <div className="page-stack">
        <ErrorBox error={preview.error} />
      </div>
    );
  if (!preview.data)
    return (
      <div className="page-stack">
        <LoadingRows />
      </div>
    );
  const p = preview.data;
  const shown = filterByStatus(p.products, filter);
  const selected = keepSelectable(picked, allowed);
  const toggle = (ref: string) =>
    setPicked(selected.includes(ref) ? selected.filter((r) => r !== ref) : [...selected, ref]);
  const confirmSend = (refs: string[], createNew: string[] = []) =>
    send.mutate(
      { request_key: confirmKey ?? crypto.randomUUID(), references: refs, create_new: createNew },
      {
        onSuccess: (run) => {
          setRunId(run.id);
          setConfirmKey(null);
          setPicked([]);
        },
      },
    );

  return (
    <div className="page-stack">
      <div>
        <h1 className="hero-badge">Products → {p.target_system}</h1>
        <p className="page-description flex flex-wrap items-center gap-x-2">
          <span>
            {p.source_name} <span className="text-muted-foreground">({p.source_system})</span>
          </span>
          <ArrowRight className="size-4 text-muted-foreground" />
          <span>
            {p.target_name} <span className="text-muted-foreground">({p.target})</span>
          </span>
        </p>
      </div>

      <div className="flex items-start gap-2 rounded-md border border-info/30 bg-info/10 p-3">
        <Info className="mt-0.5 size-5 shrink-0 text-info" />
        <div>
          <div className="font-semibold">{p.notice}</div>
          <div className="text-muted-foreground">
            One article per product, or per variant. Choose below whether your prices are net or gross and the tax
            rate of each sales tax: nothing is guessed. Then select the articles to send and confirm. Blocked
            articles can never be sent.
          </div>
        </div>
      </div>

      <div className="grid gap-4.5 lg:grid-cols-2">
        <PriceBasis ids={{ systemId, targetId }} preview={p} />
        <TaxRates ids={{ systemId, targetId }} preview={p} />
      </div>

      <Card size="sm">
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-5">
            <Counter label="Prepared" value={p.counts.products} />
            <Counter label="Ready" value={p.counts.ready} tone="text-success" />
            <Counter label="Warning" value={p.counts.warning} tone="text-warning" />
            <Counter label="Blocked" value={p.counts.blocked} tone="text-danger" />
            <Counter label="Archived" value={p.counts.archived} tone="text-muted-foreground" />
          </div>
          <p className="meta">
            Archived products of your system are never prepared. Send history: {p.history.new} new ·{" "}
            {p.history.already_sent} already sent · {p.history.changed_since_sent} changed since sent.
          </p>
          {p.truncated && (
            <p className="text-warning">The list is not complete: your system holds more products than were read.</p>
          )}
        </CardContent>
      </Card>

      {runId && (
        <SendResult systemId={systemId} targetId={targetId} runId={runId} onSend={confirmSend} hooks={HOOKS} />
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span>Prepared articles</span>
            <span className="flex flex-wrap gap-1.5">
              {FILTERS.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setFilter(value)}
                  aria-pressed={filter === value}
                  className={cn(
                    "rounded-md border px-2.5 py-1 text-sm font-normal",
                    filter === value ? "border-brand bg-brand/10" : "border-border hover:border-brand/50",
                  )}
                >
                  {label}
                </button>
              ))}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setPicked(allowed)} disabled={allowed.length === 0}>
              Select all that can be sent ({allowed.length})
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setPicked([])} disabled={selected.length === 0}>
              Clear
            </Button>
            <Button size="sm" onClick={() => setConfirmKey(crypto.randomUUID())} disabled={selected.length === 0}>
              <Send className="size-4" /> Send selected ({selected.length})
            </Button>
          </div>
          {confirmKey && selected.length > 0 && (
            <Confirmation
              records={p.products}
              targetSystem={p.target_system}
              targetName={p.target_name}
              selected={selected}
              pending={send.isPending}
              onConfirm={() => confirmSend(selected)}
              onCancel={() => setConfirmKey(null)}
            />
          )}
          {send.error && <ErrorBox error={send.error} />}
          {shown.length === 0 && <p className="text-muted-foreground">Nothing to show for this filter.</p>}
          {shown.map((product) => (
            <Product
              key={product.source_reference}
              ids={{ systemId, targetId }}
              product={product}
              checked={selected.includes(product.source_reference)}
              onToggle={() => toggle(product.source_reference)}
            />
          ))}
        </CardContent>
      </Card>

      <PreviousSends systemId={systemId} targetId={targetId} current={runId} onOpen={setRunId} hooks={HOOKS} />
    </div>
  );
}

function PriceBasis({ ids, preview }: { ids: Ids; preview: ProductTargetPreview }) {
  const set = useSetPriceBasis(ids.systemId, ids.targetId);
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Settings</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <fieldset className="space-y-2" disabled={set.isPending}>
          <legend className="label mb-2">The prices in {preview.source_name} are</legend>
          <RadioList
            name="price-basis"
            label={`The prices in ${preview.source_name} are`}
            value={preview.price_basis ?? ""}
            onChange={(b) => set.mutate(b as (typeof preview.price_bases)[number])}
            options={preview.price_bases.map((b) => ({ value: b, label: BASIS_LABEL[b], disabled: set.isPending }))}
          />
        </fieldset>
        {preview.price_basis === null && <p className="text-warning">Choose one: nothing is assumed.</p>}
        {set.error && <ErrorBox error={set.error} />}
      </CardContent>
    </Card>
  );
}

function TaxRates({ ids, preview }: { ids: Ids; preview: ProductTargetPreview }) {
  const set = useSetTaxRate(ids.systemId, ids.targetId);
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Tax rates</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {preview.taxes.length === 0 && (
          <p className="text-muted-foreground">No product of your system has exactly one sales tax.</p>
        )}
        {preview.taxes.map((t) => (
          <div key={t.tax_reference} className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="font-semibold break-words">{t.label}</div>
              <div className="meta">
                {t.products} article{t.products === 1 ? "" : "s"}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="input w-auto"
                aria-label={`Tax rate for ${t.label}`}
                value={t.rate ?? ""}
                disabled={set.isPending}
                onChange={(e) => set.mutate({ tax: t.tax_reference, label: t.label, rate: e.target.value || null })}
              >
                <option value="">Choose…</option>
                {preview.tax_rates.map((r) => (
                  <option key={r} value={r}>
                    {r} %
                  </option>
                ))}
              </select>
            </div>
          </div>
        ))}
        <p className="meta">Only the rates accepted by {preview.target_system} can be chosen.</p>
        {set.error && <ErrorBox error={set.error} />}
      </CardContent>
    </Card>
  );
}

function Counter({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div>
      <div className="meta">{label}</div>
      <div className={cn("text-2xl font-semibold", tone)}>{value}</div>
    </div>
  );
}

function Product({
  ids,
  product: p,
  checked,
  onToggle,
}: {
  ids: Ids;
  product: PreparedProduct;
  checked: boolean;
  onToggle: () => void;
}) {
  const [open, setOpen] = useState(false);
  const unlink = useUnlinkProduct(ids.systemId, ids.targetId);
  const status = preparationStatus(p.status);
  const state = sendState(p.state);
  const action = actionLabel(p.action);
  return (
    <div className="rounded-card-sm border border-border bg-muted/20 p-4.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <label className={cn("flex min-w-0 items-start gap-3", p.sendable && "cursor-pointer")}>
          <input
            type="checkbox"
            className="mt-1.5 size-4 accent-[var(--brand)]"
            checked={checked}
            disabled={!p.sendable}
            onChange={onToggle}
            aria-label={`Select ${p.title}`}
          />
          <span className="min-w-0">
            <span className="item-title block break-words">{p.title}</span>
            <span className="meta block">
              {KIND_LABEL[p.kind]} · SKU {p.sku ? <span className="tech">{p.sku}</span> : "none"} ·{" "}
              Source reference <span className="tech">{p.source_reference}</span>
            </span>
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          {action && <Pill tone={action.tone}>{action.label}</Pill>}
          <Pill tone={state.tone}>{state.label}</Pill>
          <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        </div>
      </div>

      {(p.issues.length > 0 || p.source_notes.length > 0 || p.changed.length > 0 || p.outcome_unknown) && (
        <ul className="mt-3 space-y-1">
          {p.outcome_unknown && (
            <li className="text-warning">
              A previous send has an unknown outcome: open it below and tell us what you found first.
            </li>
          )}
          {p.issues.map((i) => (
            <li key={i.message} className={i.level === "blocking" ? "text-danger" : "text-warning"}>
              {i.level === "blocking" ? "Blocked: " : "Warning: "}
              {i.message}
            </li>
          ))}
          {p.source_notes.map((n) => (
            <li key={n} className="text-muted-foreground">
              Source: {n}
            </li>
          ))}
          {p.changed.length > 0 && (
            <li className="text-muted-foreground">Changed since sent: {p.changed.join(", ")}</li>
          )}
        </ul>
      )}

      {p.linked_by_match && (
        <p className="meta mt-2 flex flex-wrap items-center gap-2">
          Linked by you to an existing article: sending will update it.
          <Button size="sm" variant="ghost" onClick={() => unlink.mutate(p.source_reference)} disabled={unlink.isPending}>
            Remove link
          </Button>
        </p>
      )}
      {unlink.error && <ErrorBox error={unlink.error} />}

      {p.values.length > 0 && (
        <dl className="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
          {p.values.map((v) => (
            <div key={v.label} className="flex justify-between gap-3 border-b border-border/60 py-1">
              <dt className="meta shrink-0">{v.label}</dt>
              <dd className="min-w-0 text-right break-words">{v.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {p.request && (
        <div className="mt-3">
          <Button
            variant="ghost"
            size="sm"
            className="meta px-0"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
          >
            Technical details: prepared request
            <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
          </Button>
          {open && (
            <pre className="tech mt-2 overflow-x-auto rounded-md border border-border bg-background/60 p-3">
              {p.request}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
