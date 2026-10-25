"use client";

/** Envoi vers un système cible relié par son API — composants PARTAGÉS par les domaines (partenaires,
 *  produits) : confirmation explicite, résultat d'un envoi enregistrement par enregistrement (décision sur un
 *  doublon possible, issue inconnue à vérifier, reprise), envois précédents. Les hooks du domaine sont
 *  injectés (`SendHooks`) ; aucun nom de système ni de domaine ici, aucune décision : statuts, messages et
 *  actions viennent du backend. Présentation pure : `lib/partners/target.mjs`. */
import { useState } from "react";

import { ErrorBox, LoadingRows } from "@/components/registry";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  SendItem,
  SendRun,
  useLinkPartner,
  usePartnerMatches,
  usePartnerSend,
  usePartnerSends,
  useResolveUnknown,
  useResumePartnerSend,
} from "@/lib/api/supplier-hooks";
import { confirmationCounts, sendItemStatus, sendRunStatus } from "@/lib/partners/target.mjs";

type Ids = { systemId: string; targetId: string };
export type OnSend = (refs: string[], createNew?: string[]) => void;

/** Hooks d'envoi d'un domaine (mêmes réponses de l'API pour tous les domaines). */
export type SendHooks = {
  useSend: (systemId: string, targetId: string, runId: string | null) => ReturnType<typeof usePartnerSend>;
  useSends: (systemId: string, targetId: string) => ReturnType<typeof usePartnerSends>;
  useResume: (systemId: string, targetId: string) => ReturnType<typeof useResumePartnerSend>;
  useMatches: (
    systemId: string,
    targetId: string,
    reference: string | null,
  ) => ReturnType<typeof usePartnerMatches>;
  useLink: (systemId: string, targetId: string) => ReturnType<typeof useLinkPartner>;
  useResolve: (systemId: string, targetId: string) => ReturnType<typeof useResolveUnknown>;
  /** Phrase affichée quand la cible n'a aucun enregistrement candidat (critère du domaine). */
  noMatch: string;
};

export function Confirmation({
  records,
  targetSystem,
  targetName,
  selected,
  pending,
  onConfirm,
  onCancel,
}: {
  records: { source_reference: string; action: string }[];
  targetSystem: string;
  targetName: string;
  selected: string[];
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const c = confirmationCounts(records, selected);
  return (
    <div className="space-y-2 rounded-md border border-warning/40 bg-warning/10 p-3">
      <div className="font-semibold">
        Write {c.total} record{c.total === 1 ? "" : "s"} to {targetSystem} ({targetName})?
      </div>
      <p className="text-muted-foreground">
        {c.create} new record{c.create === 1 ? "" : "s"} will be created and {c.update} existing one
        {c.update === 1 ? "" : "s"} updated. Existing records that may be the same are never merged: they are
        shown to you first. Nothing is deleted.
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onConfirm} disabled={pending}>
          {pending ? "Sending…" : "Confirm and send"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function SendResult({
  systemId,
  targetId,
  runId,
  onSend,
  hooks,
}: Ids & { runId: string; onSend: OnSend; hooks: SendHooks }) {
  const run = hooks.useSend(systemId, targetId, runId);
  const resume = hooks.useResume(systemId, targetId);
  if (run.error) return <ErrorBox error={run.error} />;
  if (!run.data) return <LoadingRows />;
  const r: SendRun = run.data;
  const status = sendRunStatus(r.status);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-3">
          <span>Send result</span>
          <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="meta">
          {r.counts.sent} sent · {r.counts.failed} not sent · {r.counts.unknown} unknown outcome ·{" "}
          {r.counts.needs_decision} need your decision · {r.counts.skipped} skipped
        </p>
        {r.status === "running" && (
          <Button size="sm" variant="outline" onClick={() => resume.mutate(r.id)} disabled={resume.isPending}>
            Resume this send
          </Button>
        )}
        {resume.error && <ErrorBox error={resume.error} />}
        {r.items.map((item) => (
          <ResultItem
            key={item.id}
            ids={{ systemId, targetId }}
            runId={r.id}
            item={item}
            onSend={onSend}
            hooks={hooks}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function ResultItem({
  ids,
  runId,
  item,
  onSend,
  hooks,
}: {
  ids: Ids;
  runId: string;
  item: SendItem;
  onSend: OnSend;
  hooks: SendHooks;
}) {
  const [choose, setChoose] = useState(false);
  const [createNew, setCreateNew] = useState(false);
  const resolve = hooks.useResolve(ids.systemId, ids.targetId);
  const s = sendItemStatus(item.status);
  const unresolvedUnknown = item.status === "unknown" && !item.resolution;
  return (
    <div className="rounded-card-sm border border-border bg-muted/20 p-4.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="item-title break-words">{item.name ?? item.source_reference}</div>
          <div className="meta">
            Source reference <span className="tech">{item.source_reference}</span>
            {item.operation !== "none" && ` · ${item.operation === "create" ? "Create" : "Update"}`}
          </div>
        </div>
        <StatusBadge tone={s.tone}>{s.label}</StatusBadge>
      </div>
      {item.message && <p className="mt-2">{item.message}</p>}
      {item.fields.length > 0 && <p className="meta mt-1">Fields: {item.fields.join(", ")}</p>}
      {item.resolution && (
        <p className="meta mt-1">
          You checked: {item.resolution === "created" ? "it was created (now linked)" : "it was not created"}.
        </p>
      )}
      {item.status === "needs_decision" && (
        <div className="mt-3 space-y-2">
          <Matches
            ids={ids}
            reference={item.source_reference}
            action="Link to this record"
            hooks={hooks}
            onPick={(id, link) => link.mutate({ reference: item.source_reference, target_record_id: id })}
          />
          {!createNew ? (
            <Button size="sm" variant="outline" onClick={() => setCreateNew(true)}>
              It is a different one: create a new record
            </Button>
          ) : (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 p-2">
              <span>A new record will be written to the target system, next to the existing one.</span>
              <Button size="sm" onClick={() => onSend([item.source_reference], [item.source_reference])}>
                Confirm and send
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setCreateNew(false)}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      )}
      {unresolvedUnknown && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => setChoose(!choose)}>
            I checked: it was created
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={resolve.isPending}
            onClick={() => resolve.mutate({ runId, itemId: item.id, created: false })}
          >
            I checked: it was not created
          </Button>
        </div>
      )}
      {unresolvedUnknown && choose && (
        <div className="mt-2">
          <Matches
            ids={ids}
            reference={item.source_reference}
            action="This is the created record"
            hooks={hooks}
            onPick={(id) => resolve.mutate({ runId, itemId: item.id, created: true, target_record_id: id })}
          />
        </div>
      )}
      {resolve.error && <ErrorBox error={resolve.error} />}
    </div>
  );
}

/** Enregistrements existants de la cible qui pourraient être celui-ci (lecture à la demande). */
function Matches({
  ids,
  reference,
  action,
  hooks,
  onPick,
}: {
  ids: Ids;
  reference: string;
  action: string;
  hooks: SendHooks;
  onPick: (id: string, link: ReturnType<SendHooks["useLink"]>) => void;
}) {
  const matches = hooks.useMatches(ids.systemId, ids.targetId, reference);
  const link = hooks.useLink(ids.systemId, ids.targetId);
  if (matches.error) return <ErrorBox error={matches.error} />;
  if (!matches.data) return <LoadingRows />;
  if (matches.data.length === 0) return <p className="meta">{hooks.noMatch}</p>;
  return (
    <ul className="space-y-1.5">
      {matches.data.map((m) => (
        <li key={m.target_record_id} className="flex flex-wrap items-center justify-between gap-2">
          <span>
            {m.label ?? "Unnamed record"}
            {m.archived && <span className="meta"> (archived)</span>}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={link.isPending}
            onClick={() => onPick(m.target_record_id, link)}
          >
            {action}
          </Button>
        </li>
      ))}
      {link.error && <ErrorBox error={link.error} />}
      {link.isSuccess && <p className="text-success">Linked: select it again and send to update that record.</p>}
    </ul>
  );
}

export function PreviousSends({
  systemId,
  targetId,
  current,
  onOpen,
  hooks,
}: Ids & { current: string | null; onOpen: (id: string) => void; hooks: SendHooks }) {
  const sends = hooks.useSends(systemId, targetId);
  if (!sends.data || sends.data.length === 0) return null;
  return (
    <Card size="sm">
      <CardContent className="space-y-2">
        <div className="section-title">Previous sends</div>
        {sends.data.map((r) => {
          const s = sendRunStatus(r.status);
          return (
            <div key={r.id} className="flex flex-wrap items-center justify-between gap-2">
              <span className="meta">{new Date(r.created_at).toLocaleString()}</span>
              <span className="flex items-center gap-2">
                <StatusBadge tone={s.tone}>{s.label}</StatusBadge>
                <Button size="sm" variant="ghost" onClick={() => onOpen(r.id)} disabled={current === r.id}>
                  Open
                </Button>
              </span>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
