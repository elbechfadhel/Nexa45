"use client";

/** Préparation DATEV (ACCOUNTING, phase 4, expérimental) : à partir des pièces lues (une société, un mois),
 *  ce que deviendraient les écritures Buchungsstapel — réglages du dossier, correspondances de comptes et de
 *  taxes (suggestion ≠ décision : rien n'est appliqué sans confirmation), statut DATEV de chaque pièce et
 *  détail de ses écritures (valeurs telles qu'elles seraient écrites, lignes d'origine). Aperçu seulement :
 *  aucun fichier, rien n'est envoyé. Toute la logique est côté API ; ici, de l'affichage. */
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import Link from "next/link";
import { Fragment, useState } from "react";

import { Field } from "@/components/form";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { StatusBadge, type Tone } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { RadioList } from "@/components/ui/radio-list";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatAmount } from "@/lib/accounting/presentation.mjs";

import { DatevFileExport } from "./datev-file-export";
import {
  cell,
  mappingStatus,
  mappingTarget,
  partnerSide,
  targetStatus,
  taxDecision,
} from "@/lib/accounting/preparation.mjs";
import {
  type AccountingMapping,
  type PreparedBooking,
  type PreparedDocument,
  useAccountingPreparation,
  useAccountingTargets,
  useAccountingTargetSettings,
  useDecideMapping,
  useSaveAccountingTargetSettings,
} from "@/lib/api/supplier-hooks";

const WRAP = "whitespace-normal wrap-anywhere";

type Props = { systemId: string; companyId: string; period: string };

export function DatevPreparation({ systemId, companyId, period }: Props) {
  const [open, setOpen] = useState(false);
  const targets = useAccountingTargets(open);
  const [version, setVersion] = useState<string | null>(null);
  const target = targets.data?.[0] ?? null;
  const chosen = version ?? target?.versions[0] ?? null;
  const t = target ? { systemId, system: target.system, product: target.product } : null;
  const settings = useAccountingTargetSettings(open ? t : null);
  const ready = !!settings.data?.ready;
  const preview = useAccountingPreparation(
    ready ? t : null,
    ready && chosen ? { companyId, period, version: chosen } : null,
  );

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" onClick={() => setOpen(true)}>
          Prepare for DATEV
        </Button>
        <span className="meta text-muted-foreground">Preview only: no file is created and nothing is sent.</span>
      </div>
    );
  }
  return (
    <Card size="sm">
      <CardContent className="@container space-y-5">
        <div>
          <h3 className="item-title flex flex-wrap items-center gap-3">
            DATEV preparation <StatusBadge tone="info">Experimental</StatusBadge>
          </h3>
          <p className="meta text-muted-foreground">
            How these documents would be booked in DATEV. Preview only: no file is created and nothing is sent.
          </p>
        </div>
        {targets.error && <ErrorBox error={targets.error} />}
        {!targets.data && !targets.error && <LoadingRows rows={1} />}
        {target && (
          <div className="grid gap-4 @xl:grid-cols-[minmax(0,1fr)_160px]">
            <Field label="Format">
              <div className="py-2">{target.name}</div>
            </Field>
            <Field label="Version">
              <Select
                items={target.versions.map((v) => ({ value: v, label: v }))}
                value={chosen}
                onValueChange={setVersion}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Version" />
                </SelectTrigger>
                <SelectContent>
                  {target.versions.map((v) => (
                    <SelectItem key={v} value={v}>
                      {v}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
        )}
        {target && target.versions.length === 0 && (
          <div className="rounded-md border border-warning/35 bg-warning/10 p-3 text-warning">
            This format is not available yet. Please contact us.
          </div>
        )}
        {t && <Settings t={t} />}
        {preview.error && <ErrorBox error={preview.error} />}
        {ready && !preview.data && !preview.error && <LoadingRows rows={3} />}
        {preview.data && t && chosen && (
          <Preview t={t} data={preview.data} query={{ companyId, period, version: chosen }} />
        )}
      </CardContent>
    </Card>
  );
}

type T = { systemId: string; system: string; product: string };

function Settings({ t }: { t: T }) {
  const settings = useAccountingTargetSettings(t);
  const save = useSaveAccountingTargetSettings(t);
  const [values, setValues] = useState<Record<string, string>>({});
  if (settings.error) return <ErrorBox error={settings.error} />;
  if (!settings.data) return <LoadingRows rows={2} />;
  const s = settings.data;
  const shared = s.values.filter((v) => v.shared);
  const sharedMissing = shared.some((v) => v.value === null || v.value === "");
  return (
    <div className="space-y-3">
      <div className="meta text-muted-foreground">
        DATEV client settings{s.shared_with ? ` (from ${s.shared_with}, set once in Exports)` : ""}
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 @xl:grid-cols-3">
        {shared.map((v) => (
          <div key={v.name} className="min-w-0">
            <dt className="meta text-muted-foreground">{v.label}</dt>
            <dd className="wrap-anywhere">{v.value === null || v.value === "" ? "—" : String(v.value)}</dd>
          </div>
        ))}
      </dl>
      {sharedMissing && (
        <div className="rounded-md border border-warning/35 bg-warning/10 p-3 text-warning">
          Set up your DATEV client first in the customers &amp; suppliers export.{" "}
          <Link href="/portal/exports" className="underline">
            Open Exports
          </Link>
        </div>
      )}
      <form
        className="grid gap-4 @xl:grid-cols-2 @xl:items-start"
        onSubmit={(e) => {
          e.preventDefault();
          const own = Object.fromEntries(
            s.params.map((p) => [p.name, values[p.name] ?? s.values.find((v) => v.name === p.name)?.value ?? ""]),
          );
          save.mutate(own);
        }}
      >
        {s.params.map((p) => {
          const current = values[p.name] ?? String(s.values.find((v) => v.name === p.name)?.value ?? "");
          const hint = p.format_help ?? p.help ?? undefined;
          return (
            <Field key={p.name} label={p.required_for_file ? `${p.label} — needed for the file` : p.label} hint={hint}>
              {p.choices.length > 0 ? (
                <RadioList
                  layout="inline"
                  label={p.label}
                  value={current}
                  onChange={(c) => setValues({ ...values, [p.name]: c })}
                  options={p.choices.map((c) => ({ value: c, label: c }))}
                />
              ) : (
                <Input value={current} onChange={(e) => setValues({ ...values, [p.name]: e.target.value })} />
              )}
            </Field>
          );
        })}
        <div className="@xl:col-span-2">
          <Button type="submit" variant="outline" disabled={save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" />} Save
          </Button>
        </div>
      </form>
      {save.error && <ErrorBox error={save.error} />}
      {!s.ready && !sharedMissing && <p className="meta text-warning">Missing: {s.missing.join(", ")}</p>}
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: number; tone?: Tone }) {
  const color = tone === "success" ? "text-success" : tone === "danger" && value > 0 ? "text-danger" : "";
  return (
    <div className="rounded-card-sm border border-border p-3">
      <div className="meta text-muted-foreground">{label}</div>
      <div className={`section-title ${color}`}>{value}</div>
    </div>
  );
}

type Query = { companyId: string; period: string; version: string };

function Preview({
  t,
  data,
  query,
}: {
  t: T;
  data: NonNullable<ReturnType<typeof useAccountingPreparation>["data"]>;
  query: Query;
}) {
  const c = data.counts;
  const accounts = data.mappings.filter((m) => m.kind === "account");
  const taxes = data.mappings.filter((m) => m.kind === "tax");
  // catégories d'une source sans plan comptable : jamais de suggestion, chaque compte est saisi et confirmé
  const categories = data.mappings.filter((m) => m.kind === "category");
  return (
    <div className="space-y-6">
      <div className="meta text-muted-foreground">
        {data.target} · {data.company} · {data.period}
      </div>
      <div className="grid grid-cols-2 gap-3 @xl:grid-cols-3">
        <Count label="Documents" value={c.documents} />
        <Count label="Eligible in your system" value={c.eligible} />
        <Count label="DATEV ready" value={c.ready} tone="success" />
        <Count label="Warnings" value={c.warnings} />
        <Count label="Blocked" value={c.blocked} tone="danger" />
        <Count label="Not prepared" value={c.not_prepared} />
      </div>
      {data.truncated && (
        <div className="rounded-md border border-warning/35 bg-warning/10 p-3 text-warning">
          Only the first documents of this month were read.
        </div>
      )}

      <section className="space-y-3">
        <h4 className="font-semibold">G/L account mappings</h4>
        <p className="meta text-muted-foreground">
          Revenue and expense accounts of your documents → DATEV G/L accounts (Gegenkonto). Customer and supplier
          accounts come from the customers &amp; suppliers export; tax lines are booked through the tax key.
        </p>
        <Mappings t={t} items={accounts} />
      </section>
      {categories.length > 0 && (
        <section className="space-y-3">
          <h4 className="font-semibold">Category mappings</h4>
          <p className="meta text-muted-foreground">
            Your system books by category, without G/L accounts. Enter the DATEV G/L account (Gegenkonto) of each
            category: nothing is proposed, and bookings of a category without a confirmed account stay blocked.
          </p>
          <Mappings t={t} items={categories} />
        </section>
      )}
      <section className="space-y-3">
        <h4 className="font-semibold">Tax mappings</h4>
        <p className="meta text-muted-foreground">
          The DATEV tax key (BU-Schlüssel) of each tax. Nothing is proposed: use the key given by your tax advisor, or
          “No tax key” if the G/L account calculates the tax itself.
        </p>
        <Mappings t={t} items={taxes} />
      </section>

      <section className="space-y-3">
        <h4 className="font-semibold">Documents</h4>
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead className="w-[31%]">Document</TableHead>
              <TableHead>Partner</TableHead>
              <TableHead className="w-[33%] pr-2 text-right">DATEV</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.documents.map((d, i) => (
              <DocumentRow key={`${d.number ?? d.reference ?? ""}-${i}`} doc={d} />
            ))}
          </TableBody>
        </Table>
      </section>

      <DatevFileExport key={`${query.period}-${query.version}`} t={t} query={query} counts={data.counts} />
    </div>
  );
}

function Mappings({ t, items }: { t: T; items: AccountingMapping[] }) {
  if (items.length === 0) return <p className="text-muted-foreground">Nothing to map in this month.</p>;
  return (
    <ul className="divide-y divide-border">
      {items.map((m) => (
        <MappingRow key={`${m.kind}-${m.ref}`} t={t} m={m} />
      ))}
    </ul>
  );
}

function MappingRow({ t, m }: { t: T; m: AccountingMapping }) {
  const decide = useDecideMapping(t);
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(m.status === "missing" || m.status === "invalid");
  const status = mappingStatus(m.status);
  const base = { kind: m.kind, ref: m.ref, sourceCode: m.source_code, sourceName: m.source_name };
  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="wrap-anywhere">
            <span className="tech">{m.source_code}</span>{" "}
            {m.source_name && <span className="text-muted-foreground">{m.source_name}</span>}
          </div>
          <div className="meta text-muted-foreground">
            {m.documents} document{m.documents === 1 ? "" : "s"}
            {m.provenance ? ` · ${m.provenance}` : ""}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 text-right">
          <span className="tech">→ {m.status === "suggested" ? m.suggestion : mappingTarget(m)}</span>
          <StatusBadge tone={status.tone} className="shrink whitespace-normal">
            {status.label}
          </StatusBadge>
        </div>
      </div>
      <p className="meta text-muted-foreground">{m.reason}</p>
      <div className="flex flex-wrap items-center gap-2">
        {m.status === "suggested" && m.suggestion && (
          <Button
            size="sm"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ ...base, value: m.suggestion, acceptSuggestion: true })}
          >
            Confirm {m.suggestion}
          </Button>
        )}
        {!editing && (
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            {m.status === "confirmed" ? "Change" : "Enter another value"}
          </Button>
        )}
        {m.status === "confirmed" && (
          <Button size="sm" variant="ghost" onClick={() => decide.mutate({ ...base, clear: true })}>
            Remove decision
          </Button>
        )}
      </div>
      {editing && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            decide.mutate({ ...base, value: value.trim() }, { onSuccess: () => setEditing(false) });
          }}
        >
          <Input
            className="w-40"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={m.kind === "tax" ? "Tax key" : "DATEV account"}
          />
          <Button size="sm" type="submit" disabled={!value.trim() || decide.isPending}>
            Save
          </Button>
          {m.kind === "tax" && (
            <Button
              size="sm"
              type="button"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ ...base, noValue: true }, { onSuccess: () => setEditing(false) })}
            >
              No tax key
            </Button>
          )}
          {m.status !== "missing" && (
            <Button size="sm" type="button" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          )}
        </form>
      )}
      {decide.error && <ErrorBox error={decide.error} />}
    </li>
  );
}

function DocumentRow({ doc }: { doc: PreparedDocument }) {
  const [open, setOpen] = useState(false);
  const status = targetStatus(doc.target_status);
  return (
    <Fragment>
      <TableRow className="cursor-pointer" aria-expanded={open} onClick={() => setOpen(!open)}>
        <TableCell>{open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</TableCell>
        <TableCell className={WRAP}>
          <div className="tech">{doc.number ?? "Not yet numbered"}</div>
          <div className="meta text-muted-foreground">{[doc.type, doc.reference].filter(Boolean).join(" · ")}</div>
        </TableCell>
        <TableCell className={WRAP}>{doc.partner ?? "—"}</TableCell>
        <TableCell className="pr-2 pl-2 text-right whitespace-normal">
          <div className="tabular-nums">{formatAmount(doc.total_amount, doc.currency)}</div>
          <div className="mt-1 flex flex-col items-end gap-1">
            <StatusBadge tone={status.tone} className="shrink whitespace-normal">
              {status.label}
            </StatusBadge>
            {!doc.eligible && doc.target_status !== null && (
              <span className="meta text-muted-foreground">Not eligible in your system</span>
            )}
          </div>
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={4} className="space-y-3 whitespace-normal">
            {doc.not_prepared_reason && <p className="text-muted-foreground">{doc.not_prepared_reason}</p>}
            {doc.issues.map((x) => (
              <div key={x.message} className={x.level === "blocking" ? "text-danger" : "text-warning"}>
                {x.message}
              </div>
            ))}
            {doc.bookings.length > 0 && (
              <p className="meta text-muted-foreground">
                {doc.bookings.length} DATEV booking{doc.bookings.length === 1 ? "" : "s"} · balance{" "}
                {formatAmount(doc.balance, doc.currency)}
              </p>
            )}
            {doc.bookings.map((b) => (
              <BookingCard key={b.index} b={b} doc={doc} />
            ))}
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  );
}

function BookingCard({ b, doc }: { b: PreparedBooking; doc: PreparedDocument }) {
  const status = targetStatus(b.status);
  const facts: [string, string][] = [
    ["Umsatz", `${cell(b, "001")} ${doc.currency ?? ""}`.trim()],
    ["Soll/Haben", `${cell(b, "002")} · ${partnerSide(b.partner_side, doc.partner_role)}`],
    ["Konto", b.partner_account ?? "—"],
    ["Gegenkonto", `${b.counter_account ?? "—"} (from ${b.counter_source})`],
    ["BU-Schlüssel", taxDecision(b)],
    ["Belegdatum", cell(b, "010")],
    ["Belegfeld 1", cell(b, "011")],
    ["Buchungstext", cell(b, "014")],
  ];
  return (
    <div className="space-y-3 rounded-card-sm border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">Booking {b.index}</span>
        <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
      </div>
      {b.issues.map((x) => (
        <div key={x.message} className={x.level === "blocking" ? "text-danger" : "text-warning"}>
          {x.message}
        </div>
      ))}
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 @md:grid-cols-2">
        {facts.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="meta text-muted-foreground">{label}</dt>
            <dd className="wrap-anywhere">{value}</dd>
          </div>
        ))}
      </dl>
      <div>
        <div className="meta text-muted-foreground">From these lines of your document</div>
        <ul className="divide-y divide-border">
          {b.source_lines.map((sl, i) => (
            <li key={i} className="flex justify-between gap-3 py-1.5">
              <span className="wrap-anywhere">
                {sl.description ?? sl.kind} · <span className="tech">{sl.account_code}</span>
              </span>
              <span className="tabular-nums">{formatAmount(sl.gross_amount ?? sl.amount, doc.currency)}</span>
            </li>
          ))}
        </ul>
        <p className="meta text-muted-foreground">
          Amounts including tax, signed as in your system (debit positive, credit negative).
        </p>
      </div>
      <details>
        <summary className="meta cursor-pointer text-muted-foreground">DATEV fields as they would be written</summary>
        <ul className="mt-2 space-y-1">
          {b.cells.map((c) => (
            <li key={c.field} className="flex justify-between gap-3">
              <span className="text-muted-foreground">
                <span className="tech">{c.field}</span> {c.label}
              </span>
              <span className="tech wrap-anywhere">{c.value}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
