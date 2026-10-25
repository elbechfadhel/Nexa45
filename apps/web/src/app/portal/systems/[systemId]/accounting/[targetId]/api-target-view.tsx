"use client";

/** Pièces comptables d'un système → système cible relié par son API : Preview → Select → Confirm → Results.
 *  Source, cible, pièces, statuts READY / WARNING / BLOCKED, historique d'envoi (D11), montants, TVA, contact,
 *  valeurs de la cible par compte et blocages. Rien n'est écrit dans la cible avant une sélection ET une
 *  confirmation explicite ; seules les pièces que le backend déclare envoyables (prêtes, jamais envoyées) sont
 *  sélectionnables ; la clé d'envoi est générée à l'ouverture de la confirmation (un double clic renvoie le
 *  même envoi) ; une issue inconnue n'est jamais renvoyée d'office. La page ne nomme aucun système et ne
 *  décide rien. Présentation pure : `lib/accounting/api-target.mjs`. */
import { ArrowRight, ChevronDown, Info, Loader2, Send } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { Field } from "@/components/form";
import { PortalGate } from "@/components/portal";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { Pill, StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  type AccountingApiAccount,
  type AccountingApiDocument,
  type AccountingApiPreview,
  type AccountingApiSendItem,
  type AccountingApiSendRun,
  type TargetChoice,
  useAccountingApiOptions,
  useAccountingApiPreview,
  useAccountingApiSend,
  useAccountingApiSends,
  useAccountingApiTargets,
  useChooseAccountValue,
  useClearAccountValue,
  useResolveAccountingUnknown,
  useSendAccountingDocuments,
} from "@/lib/api/accounting-api-target-hooks";
import { useAccountingCompanies } from "@/lib/api/supplier-hooks";
import {
  accountsToReview,
  documentTypeLabel,
  expectedKindLabel,
  groupOptions,
  optionKind,
  rateLabel,
  selectionSummary,
} from "@/lib/accounting/api-target.mjs";
import { formatAmount } from "@/lib/accounting/presentation.mjs";
import {
  filterByStatus,
  keepSelectable,
  preparationStatus,
  selectableRefs,
  sendItemStatus,
  sendRunStatus,
  sendState,
} from "@/lib/partners/target.mjs";
import { cn } from "@/lib/utils";

type Ids = { systemId: string; targetId: string };
type Query = { company_id: string; period: string };

const FILTERS = [
  ["all", "All"],
  ["ready", "Ready"],
  ["warning", "Warning"],
  ["blocked", "Blocked"],
] as const;

export function ApiTargetView({ systemId, targetId }: Ids) {
  return <PortalGate>{() => <View systemId={systemId} targetId={targetId} />}</PortalGate>;
}

function previousMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function View({ systemId, targetId }: Ids) {
  const params = useSearchParams();
  const companies = useAccountingCompanies(systemId, true);
  const targets = useAccountingApiTargets(systemId);
  const [company, setCompany] = useState(params.get("company") ?? "");
  const [period, setPeriod] = useState(params.get("period") ?? previousMonth());
  const [query, setQuery] = useState<Query | null>(
    params.get("company") && params.get("period")
      ? { company_id: params.get("company") ?? "", period: params.get("period") ?? "" }
      : null,
  );
  const preview = useAccountingApiPreview(systemId, targetId, query);
  const target = targets.data?.find((t) => t.id === targetId);
  const selected = company || companies.data?.[0]?.id || "";

  return (
    <div className="page-stack">
      <div>
        <h1 className="hero-badge">Accounting documents → {target?.system ?? "target system"}</h1>
        {preview.data ? (
          <p className="page-description flex flex-wrap items-center gap-x-2">
            <span>{preview.data.source_name}</span>
            <ArrowRight className="size-4 text-muted-foreground" />
            <span>
              {preview.data.target_name} <span className="text-muted-foreground">({preview.data.target})</span>
            </span>
          </p>
        ) : (
          target && <p className="page-description">{target.name}</p>
        )}
      </div>

      <div className="flex items-start gap-2 rounded-md border border-info/30 bg-info/10 p-3">
        <Info className="mt-0.5 size-5 shrink-0 text-info" />
        <div>
          <div className="font-semibold">Nothing is sent until you select documents and confirm.</div>
          <div className="text-muted-foreground">
            1. Preview a company and a month · 2. Select the documents to send · 3. Confirm · 4. Check the results.
            Only documents that are ready and were never sent can be selected.
          </div>
        </div>
      </div>

      <Card size="sm">
        <CardContent>
          <form
            className="grid items-end gap-3 sm:grid-cols-[1fr_200px_auto]"
            onSubmit={(e) => {
              e.preventDefault();
              if (selected && period) setQuery({ company_id: selected, period });
            }}
          >
            <Field label="Company">
              <select
                className="input"
                value={selected}
                onChange={(e) => setCompany(e.target.value)}
              >
                {(companies.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name ?? c.id}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Accounting period">
              <Input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} required />
            </Field>
            <Button type="submit" disabled={!selected || !period || preview.isFetching}>
              {preview.isFetching && <Loader2 className="animate-spin" />} Preview
            </Button>
          </form>
          {companies.error && <ErrorBox error={companies.error} />}
        </CardContent>
      </Card>

      {preview.error && <ErrorBox error={preview.error} />}
      {query && preview.isLoading && <LoadingRows />}
      {preview.data && query && (
        <PreviewBody key={`${query.company_id}-${query.period}`} ids={{ systemId, targetId }} data={preview.data} query={query} />
      )}
      <PreviousSends ids={{ systemId, targetId }} />
    </div>
  );
}

function PreviewBody({ ids, data, query }: { ids: Ids; data: AccountingApiPreview; query: Query }) {
  const [filter, setFilter] = useState<string>("all");
  const [picked, setPicked] = useState<string[]>([]);
  const [confirmKey, setConfirmKey] = useState<string | null>(null);
  const [result, setResult] = useState<AccountingApiSendRun | null>(null);
  const send = useSendAccountingDocuments(ids.systemId, ids.targetId);
  const review = accountsToReview(data.accounts);
  const shown = filterByStatus(data.documents, filter);
  // la page ne décide pas : seules les pièces que le backend déclare envoyables sont sélectionnables
  const allowed = useMemo(() => selectableRefs(data.documents), [data.documents]);
  const selected = keepSelectable(picked, allowed);
  const toggle = (ref: string) =>
    setPicked(selected.includes(ref) ? selected.filter((r) => r !== ref) : [...selected, ref]);
  const confirmSend = () => {
    if (!confirmKey || send.isPending) return; // anti-double-clic : une seule demande par confirmation
    send.mutate(
      { request_key: confirmKey, company_id: query.company_id, period: query.period, references: selected },
      {
        onSuccess: (run) => {
          setResult(run);
          setConfirmKey(null);
          setPicked([]);
        },
      },
    );
  };

  return (
    <>
      <Card size="sm">
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-4">
            <Counter label="Documents" value={data.counts.documents} />
            <Counter label="Ready" value={data.counts.ready} tone="text-success" />
            <Counter label="Warning" value={data.counts.warning} tone="text-warning" />
            <Counter label="Blocked" value={data.counts.blocked} tone="text-danger" />
          </div>
          <p className="meta">
            {data.company ?? "Company"} · {data.period} · {allowed.length} can be sent now
            {review.missing > 0 && ` · ${review.missing} account(s) still need a value`}
          </p>
          {!data.options_checked && (
            <p className="text-warning">
              The values of {data.target_system} could not be read: your choices could not be checked.
            </p>
          )}
          {data.truncated && <p className="text-warning">The list is not complete: more documents exist.</p>}
        </CardContent>
      </Card>

      {result && <SendResult ids={ids} run={result} query={query} onChange={setResult} />}

      <Accounts ids={ids} accounts={data.accounts} targetSystem={data.target_system} />

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span>Documents</span>
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
            <Button
              size="sm"
              onClick={() => setConfirmKey(crypto.randomUUID())}
              disabled={selected.length === 0 || send.isPending || confirmKey !== null}
            >
              <Send className="size-4" /> Send selected ({selected.length})
            </Button>
          </div>
          {confirmKey && selected.length > 0 && (
            <Confirmation
              data={data}
              selected={selected}
              pending={send.isPending}
              onConfirm={confirmSend}
              onCancel={() => setConfirmKey(null)}
            />
          )}
          {send.error && <ErrorBox error={send.error} />}
          {shown.length === 0 && <p className="text-muted-foreground">Nothing to show for this filter.</p>}
          {shown.map((d) => (
            <Document
              key={d.source_reference}
              doc={d}
              targetSystem={data.target_system}
              checked={selected.includes(d.source_reference)}
              onToggle={() => toggle(d.source_reference)}
            />
          ))}
        </CardContent>
      </Card>
    </>
  );
}

function Confirmation({
  data,
  selected,
  pending,
  onConfirm,
  onCancel,
}: {
  data: AccountingApiPreview;
  selected: string[];
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const summary = selectionSummary(data.documents, selected);
  return (
    <div className="space-y-2 rounded-md border border-warning/40 bg-warning/10 p-3">
      <div className="font-semibold">
        Write {summary.rows.length} document{summary.rows.length === 1 ? "" : "s"} to {data.target_system} (
        {data.target_name})?
      </div>
      <ul className="space-y-0.5">
        {summary.rows.map((r) => (
          <li key={r.reference} className="flex flex-wrap justify-between gap-3">
            <span>
              <span className="tech">{r.number}</span> · {r.partner}
            </span>
            <span>{formatAmount(r.amount, r.currency)}</span>
          </li>
        ))}
      </ul>
      <p className="font-semibold">
        Total: {summary.totals.map((t) => formatAmount(t.amount, t.currency || null)).join(" + ")}
      </p>
      <p className="text-muted-foreground">
        Each document is created once in {data.target_system}. A document with the same number that already exists
        there is shown to you instead of being created again. Nothing is updated or deleted.
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

function SendResult({
  ids,
  run,
  query,
  onChange,
}: {
  ids: Ids;
  run: AccountingApiSendRun;
  query: Query;
  onChange: (run: AccountingApiSendRun) => void;
}) {
  const status = sendRunStatus(run.status);
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
          {run.counts.sent} sent · {run.counts.skipped} skipped · {run.counts.unknown} unknown outcome ·{" "}
          {run.counts.failed} not sent · {run.counts.needs_decision} need your decision
        </p>
        {run.items.map((item) => (
          <ResultItem key={item.id} ids={ids} runId={run.id} item={item} query={query} onChange={onChange} />
        ))}
      </CardContent>
    </Card>
  );
}

function ResultItem({
  ids,
  runId,
  item,
  query,
  onChange,
}: {
  ids: Ids;
  runId: string;
  item: AccountingApiSendItem;
  query: Query;
  onChange: (run: AccountingApiSendRun) => void;
}) {
  const resolve = useResolveAccountingUnknown(ids.systemId, ids.targetId);
  const s = sendItemStatus(item.status);
  const open = item.status === "unknown" && !item.resolution;
  const decide = (created: boolean) =>
    resolve.mutate({ runId, itemId: item.id, created, ...query }, { onSuccess: onChange });
  return (
    <div className="rounded-card-sm border border-border bg-muted/20 p-4.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="item-title break-words">{item.number ?? item.source_reference}</div>
          <div className="meta">
            Source reference <span className="tech">{item.source_reference}</span>
          </div>
        </div>
        <StatusBadge tone={s.tone}>{s.label}</StatusBadge>
      </div>
      {item.message && <p className="mt-2">{item.message}</p>}
      {item.status === "needs_decision" && (
        <p className="meta mt-1">Nothing was created. Check the existing document in the target system.</p>
      )}
      {item.resolution && (
        <p className="meta mt-1">
          You checked: {item.resolution === "created" ? "it was created (now recorded as sent)" : "it was not created"}.
        </p>
      )}
      {open && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={resolve.isPending} onClick={() => decide(true)}>
            I checked: it was created
          </Button>
          <Button size="sm" variant="outline" disabled={resolve.isPending} onClick={() => decide(false)}>
            I checked: it was not created
          </Button>
        </div>
      )}
      {resolve.error && <ErrorBox error={resolve.error} />}
    </div>
  );
}

function PreviousSends({ ids }: { ids: Ids }) {
  const sends = useAccountingApiSends(ids.systemId, ids.targetId);
  const [openId, setOpenId] = useState<string | null>(null);
  const run = useAccountingApiSend(ids.systemId, ids.targetId, openId);
  if (!sends.data || sends.data.length === 0) return null;
  return (
    <Card size="sm">
      <CardContent className="space-y-2">
        <div className="section-title">Previous sends</div>
        {sends.data.map((r) => {
          const s = sendRunStatus(r.status);
          return (
            <div key={r.id} className="space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="meta">{new Date(r.created_at).toLocaleString()}</span>
                <span className="flex items-center gap-2">
                  <StatusBadge tone={s.tone}>{s.label}</StatusBadge>
                  <Button size="sm" variant="ghost" onClick={() => setOpenId(openId === r.id ? null : r.id)}>
                    {openId === r.id ? "Hide" : "Details"}
                  </Button>
                </span>
              </div>
              {openId === r.id && run.data && (
                <ul className="meta space-y-0.5 pl-4">
                  {run.data.items.map((i) => (
                    <li key={i.id}>
                      <span className="tech">{i.source_reference}</span> · {sendItemStatus(i.status).label}
                      {i.message && ` — ${i.message}`}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

/** Valeur de la cible pour chaque compte des pièces (choix explicite, enregistré dans UIE). */
function Accounts({ ids, accounts, targetSystem }: { ids: Ids; accounts: AccountingApiAccount[]; targetSystem: string }) {
  const options = useAccountingApiOptions(ids.systemId, ids.targetId, accounts.length > 0);
  const choose = useChooseAccountValue(ids.systemId, ids.targetId);
  const clear = useClearAccountValue(ids.systemId, ids.targetId);
  if (accounts.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your accounts → {targetSystem}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="meta">
          Choose, for each account used by these documents, the value of {targetSystem} it corresponds to. Nothing is
          chosen for you; the list is read from {targetSystem}.
        </p>
        {options.error && <ErrorBox error={options.error} />}
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-[34%]">Account</TableHead>
              <TableHead className="w-[22%]">Expected</TableHead>
              <TableHead>Value in {targetSystem}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {accounts.map((a) => (
              <TableRow key={a.account_reference}>
                <TableCell className="whitespace-normal">
                  <span className="tech">{a.code}</span> {a.name}
                </TableCell>
                <TableCell className="whitespace-normal">{expectedKindLabel(a.expected_kind)}</TableCell>
                <TableCell className="space-y-1 whitespace-normal">
                  <select
                    aria-label={`Value for account ${a.code}`}
                    className="input"
                    value={a.choice_id ?? ""}
                    disabled={!options.data || choose.isPending}
                    onChange={(e) =>
                      e.target.value
                        ? choose.mutate({ account: a.account_reference, choice_id: e.target.value, code: a.code, name: a.name })
                        : clear.mutate(a.account_reference)
                    }
                  >
                    <option value="">— Choose —</option>
                    {groupOptions<TargetChoice>(options.data ?? [], optionKind(a.expected_kind)).map((g) => (
                      <optgroup key={g.group} label={g.group}>
                        {g.options.map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.label}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                  {a.problem && <div className="text-danger">{a.problem}</div>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {(choose.error || clear.error) && <ErrorBox error={choose.error ?? clear.error} />}
      </CardContent>
    </Card>
  );
}

function Document({
  doc: d,
  targetSystem,
  checked,
  onToggle,
}: {
  doc: AccountingApiDocument;
  targetSystem: string;
  checked: boolean;
  onToggle: () => void;
}) {
  const [open, setOpen] = useState(false);
  const status = preparationStatus(d.status);
  const state = sendState(d.state);
  return (
    <div className="rounded-card-sm border border-border bg-muted/20 p-4.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <label className={cn("flex min-w-0 items-start gap-3", d.sendable && "cursor-pointer")}>
          <input
            type="checkbox"
            className="mt-1.5 size-4"
            checked={checked}
            disabled={!d.sendable}
            onChange={onToggle}
            aria-label={`Select ${d.number ?? d.source_reference}`}
          />
          <span className="min-w-0">
            <span className="item-title block break-words">
              {d.number ?? "No number"} · {documentTypeLabel(d.document_type)}
            </span>
            <span className="meta block">
              {d.partner ?? "—"} ·{" "}
              <span className={d.contact_linked ? "text-success" : "text-warning"}>
                {d.contact_linked ? `contact in ${targetSystem}` : `no contact in ${targetSystem}`}
              </span>{" "}
              · Source reference <span className="tech">{d.source_reference}</span>
            </span>
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <Pill tone={state.tone}>{state.label}</Pill>
          <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        </div>
      </div>

      {!d.sendable && d.not_sendable_reason && d.status !== "blocked" && (
        <p className="meta mt-2">Cannot be selected: {d.not_sendable_reason}</p>
      )}

      <dl className="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-4">
        <Fact label="Date">{d.document_date ?? "—"}</Fact>
        <Fact label="Due">{d.due_date ?? "—"}</Fact>
        <Fact label="Total incl. tax">{formatAmount(d.total_amount, d.currency)}</Fact>
        <Fact label="Tax">{formatAmount(d.tax_amount, d.currency)}</Fact>
      </dl>

      {(d.issues.length > 0 || d.items.some((i) => i.issues.length > 0)) && (
        <ul className="mt-3 space-y-1">
          {[...d.issues, ...d.items.flatMap((i) => i.issues)].map((i, n) => (
            <li key={`${n}-${i.message}`} className={i.level === "blocking" ? "text-danger" : "text-warning"}>
              {i.level === "blocking" ? "Blocked: " : "Warning: "}
              {i.message}
            </li>
          ))}
        </ul>
      )}
      {d.changed.length > 0 && <p className="meta mt-2">Changed since sent: {d.changed.join(", ")}</p>}

      {d.items.length > 0 && (
        <Table className="mt-3 table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-[34%]">Account</TableHead>
              <TableHead>Tax</TableHead>
              <TableHead className="text-right">Amount incl. tax</TableHead>
              <TableHead className="text-right">Tax amount</TableHead>
              <TableHead className="w-[26%]">Value in {targetSystem}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {d.items.map((i, n) => (
              <TableRow key={`${i.account_code}-${n}`}>
                <TableCell className="whitespace-normal">
                  <span className="tech">{i.account_code}</span> {i.account_name}
                </TableCell>
                <TableCell className="whitespace-normal">
                  {rateLabel(i.tax_rate)} {i.tax && <span className="meta">({i.tax})</span>}
                </TableCell>
                <TableCell className="text-right">{formatAmount(i.amount, d.currency)}</TableCell>
                <TableCell className="text-right">{formatAmount(i.tax_amount, d.currency)}</TableCell>
                <TableCell className={cn("whitespace-normal", !i.category && "text-warning")}>
                  {i.category ?? "Not chosen"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {d.request && (
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
              {d.request}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="meta">{label}</dt>
      <dd>{children}</dd>
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
