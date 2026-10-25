"use client";

/** Clients et fournisseurs d'un système → système cible relié par son API : Preview → Select → Send → Results.
 *  Rien n'est écrit dans la cible avant une sélection ET une confirmation explicite ; la clé d'envoi est générée
 *  à l'ouverture de la confirmation (un double clic renvoie le même envoi). La page ne nomme aucun système (les
 *  noms viennent de l'API) et ne décide rien : envoyable, action, statuts viennent du backend. Présentation
 *  pure : `lib/partners/target.mjs`. */
import { ArrowRight, ChevronDown, Info, Send } from "lucide-react";
import { useMemo, useState } from "react";

import { PortalGate } from "@/components/portal";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { Pill, StatusBadge } from "@/components/status-badge";
import { Confirmation, PreviousSends, type SendHooks, SendResult } from "@/components/target-send";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type PreparedPartner,
  useLinkPartner,
  usePartnerMatches,
  usePartnerSend,
  usePartnerSends,
  usePartnerTargetPreview,
  useResolveUnknown,
  useResumePartnerSend,
  useSendPartners,
  useUnlinkPartner,
} from "@/lib/api/supplier-hooks";
import {
  actionLabel,
  countsSentence,
  filterByStatus,
  keepSelectable,
  kindLabel,
  preparationStatus,
  rolesLabel,
  selectableRefs,
  sendable,
  sendState,
} from "@/lib/partners/target.mjs";
import { cn } from "@/lib/utils";

const FILTERS = [
  ["all", "All"],
  ["ready", "Ready"],
  ["warning", "Warning"],
  ["blocked", "Blocked"],
] as const;

type Ids = { systemId: string; targetId: string };

const HOOKS: SendHooks = {
  useSend: usePartnerSend,
  useSends: usePartnerSends,
  useResume: useResumePartnerSend,
  useMatches: usePartnerMatches,
  useLink: useLinkPartner,
  useResolve: useResolveUnknown,
  noMatch: "No existing record found with the same name or e-mail.",
};

export function PreviewView({ systemId, targetId }: Ids) {
  return <PortalGate>{() => <Preview systemId={systemId} targetId={targetId} />}</PortalGate>;
}

function Preview({ systemId, targetId }: Ids) {
  const preview = usePartnerTargetPreview(systemId, targetId);
  const [filter, setFilter] = useState<string>("all");
  const [picked, setPicked] = useState<string[]>([]);
  const [confirmKey, setConfirmKey] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const send = useSendPartners(systemId, targetId);
  const allowed = useMemo(() => selectableRefs(preview.data?.partners ?? []), [preview.data]);
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
  const shown = filterByStatus(p.partners, filter);
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
        <h1 className="hero-badge">Customers &amp; suppliers → {p.target_system}</h1>
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
            1. Preview what would be prepared · 2. Select the records to send · 3. Confirm · 4. Check the results.
            Blocked records can never be sent.
          </div>
        </div>
      </div>

      <Card size="sm">
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-4">
            <Counter label="Read" value={p.counts.partners} />
            <Counter label="Ready" value={p.counts.ready} tone="text-success" />
            <Counter label="Warning" value={p.counts.warning} tone="text-warning" />
            <Counter label="Blocked" value={p.counts.blocked} tone="text-danger" />
          </div>
          <p className="meta">
            {countsSentence(p.counts)}. {sendable(p.counts)} could be sent as prepared; blocked ones need a fix in
            the source system first.
          </p>
          <p className="meta">
            Send history: {p.history.new} new · {p.history.already_sent} already sent ·{" "}
            {p.history.changed_since_sent} changed since sent.
          </p>
          {p.truncated && (
            <p className="text-warning">The list is not complete: the source holds more records than were read.</p>
          )}
        </CardContent>
      </Card>

      {runId && (
        <SendResult systemId={systemId} targetId={targetId} runId={runId} onSend={confirmSend} hooks={HOOKS} />
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span>Prepared records</span>
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
              records={p.partners}
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
          {shown.map((partner) => (
            <Partner
              key={partner.source_reference}
              ids={{ systemId, targetId }}
              partner={partner}
              checked={selected.includes(partner.source_reference)}
              onToggle={() => toggle(partner.source_reference)}
            />
          ))}
        </CardContent>
      </Card>

      <PreviousSends systemId={systemId} targetId={targetId} current={runId} onOpen={setRunId} hooks={HOOKS} />
    </div>
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

function Partner({
  ids,
  partner: p,
  checked,
  onToggle,
}: {
  ids: Ids;
  partner: PreparedPartner;
  checked: boolean;
  onToggle: () => void;
}) {
  const [open, setOpen] = useState(false);
  const unlink = useUnlinkPartner(ids.systemId, ids.targetId);
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
            aria-label={`Select ${p.name}`}
          />
          <span className="min-w-0">
            <span className="item-title block break-words">{p.name}</span>
            <span className="meta block">
              {kindLabel(p.kind)} · {rolesLabel(p.roles)} · Source reference{" "}
              <span className="tech">{p.source_reference}</span>
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
          Linked by you to an existing record: sending will update it.
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
          <button
            type="button"
            className="meta inline-flex items-center gap-1 hover:text-foreground"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
          >
            Technical details: prepared request
            <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
          </button>
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
