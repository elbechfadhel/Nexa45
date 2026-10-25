"use client";

/** ACCOUNTING côté fournisseur (phase 3, expérimental) : analyse des pièces comptables (même moteur et mêmes
 *  questions que le catalogue, domaine « accounting »), puis LECTURE d'un mois pour une société choisie
 *  explicitement : factures et avoirs tels qu'ils sont dans le système source (comptes source, taxes,
 *  montants signés vus en Débit / Crédit, solde du backend, anomalies). Lecture seule ; aucun mapping, aucun export DATEV à ce stade. */
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { Fragment, useState } from "react";

import { Field } from "@/components/form";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { StatusBadge, type Tone } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  type AccountingDocument,
  type AccountingRead,
  type SupplierSystem,
  useAccountingCompanies,
  useAnalyzeCatalog,
  useCatalogSummary,
  useReadAccounting,
  useSupplierQuestions,
} from "@/lib/api/supplier-hooks";

import {
  balanceView,
  debitCredit,
  ELIGIBLE_HINT,
  ELIGIBLE_LABEL,
  formatAmount,
  lineTitle,
} from "@/lib/accounting/presentation.mjs";

import { useAccountingApiTargets } from "@/lib/api/accounting-api-target-hooks";

import { Questions } from "./catalog-analysis";
import { DatevPreparation } from "./datev-preparation";

const STEPS = ["Connection checked", "Reading accounting structure", "Identifying accounting fields"];

const STATUS_TONE: Record<string, Tone> = {
  Posted: "success",
  Draft: "neutral",
  Cancelled: "danger",
};

/** Mois précédent (AAAA-MM) : la période comptable la plus souvent exportée. */
function previousMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function AccountingAnalysis({ system }: { system: SupplierSystem }) {
  const summary = useCatalogSummary(system.id, "accounting");
  const analyze = useAnalyzeCatalog(system.id, "accounting");
  const status = summary.data?.status;
  const questionsOpen = status === "questions_open";
  const questions = useSupplierQuestions(system.id, questionsOpen && !analyze.isPending, "accounting");

  if (analyze.isPending) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Analyzing your accounting documents…</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            {STEPS.map((step) => (
              <li key={step} className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> {step}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-3">
          Accounting <StatusBadge tone="info">Experimental</StatusBadge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {summary.error && <ErrorBox error={summary.error} />}
        {analyze.error && (
          <div className="space-y-3">
            <div className="rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">
              We couldn&apos;t analyze your system. Please check the connection and try again.
            </div>
            <Button onClick={() => analyze.mutate()}>Try again</Button>
          </div>
        )}
        {!summary.data && !summary.error && <LoadingRows rows={2} />}

        {!analyze.error && summary.data && (status === "awaiting_setup" || status === "not_analyzed") && (
          <div className="space-y-4">
            <p>Analyze your system to identify your invoices, credit notes, accounts and taxes.</p>
            <Button onClick={() => analyze.mutate()}>Analyze accounting</Button>
          </div>
        )}

        {!analyze.error && questionsOpen && (
          <div className="space-y-4">
            {questions.error && <ErrorBox error={questions.error} />}
            {!questions.data ? (
              <LoadingRows rows={3} />
            ) : (
              <Questions systemId={system.id} questions={questions.data} domain="accounting" />
            )}
          </div>
        )}

        {!analyze.error && status === "complete" && summary.data && (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-success">
                <CheckCircle2 className="size-5" />
                <span className="font-semibold">Accounting analysis complete</span>
              </div>
              <Button variant="outline" onClick={() => analyze.mutate()}>
                <RefreshCw /> Re-analyze
              </Button>
            </div>
            <AccountingReview systemId={system.id} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Système dont le sens des données comptables est DÉCLARÉ par son interface publiée (`declared`, venu de l'API) :
 *  aucune analyse ; même parcours « Read accounting / Prepare for DATEV ». Aucun nom ni condition de système. */
export function DeclaredAccounting({ system }: { system: SupplierSystem }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-3">
          Accounting <StatusBadge tone="info">Experimental</StatusBadge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-muted-foreground">
          The meaning of this system&apos;s accounting data is defined by its published interface: no analysis is
          needed. Documents are read when you ask for them; nothing is stored or changed in your system.
        </p>
        <AccountingReview systemId={system.id} />
      </CardContent>
    </Card>
  );
}

function AccountingReview({ systemId }: { systemId: string }) {
  const companies = useAccountingCompanies(systemId, true);
  const read = useReadAccounting(systemId);
  const [company, setCompany] = useState<string | null>(null);
  const [period, setPeriod] = useState(previousMonth());
  const items = (companies.data ?? []).map((c) => ({
    value: c.id,
    label: c.name ?? c.id,
  }));
  const selected = company ?? (items.length === 1 ? items[0].value : null);

  return (
    <div className="space-y-5">
      <div>
        <h3 className="item-title">Read accounting</h3>
        <p className="meta text-muted-foreground">
          Invoices and credit notes of one company for one month, as they are in your system. Nothing is changed and
          nothing is exported.
        </p>
      </div>
      {companies.error && <ErrorBox error={companies.error} />}
      <form
        className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_200px_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          if (selected && period) read.mutate({ companyId: selected, period });
        }}
      >
        <Field label="Company">
          <Select items={items} value={selected} onValueChange={setCompany}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Choose a company" />
            </SelectTrigger>
            <SelectContent>
              {items.map((c) => (
                <SelectItem key={c.value} value={c.value}>
                  {c.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Accounting period">
          <Input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} required />
        </Field>
        <Button type="submit" disabled={!selected || !period || read.isPending}>
          {read.isPending && <Loader2 className="animate-spin" />} Read accounting
        </Button>
      </form>
      {read.error && <ErrorBox error={read.error} />}
      {read.data && <ReadResult result={read.data} />}
      {read.data && selected && (
        <DatevPreparation
          key={`${selected}-${read.data.period}`}
          systemId={systemId}
          companyId={selected}
          period={read.data.period}
        />
      )}
      {read.data && selected && <ApiTargets systemId={systemId} companyId={selected} period={read.data.period} />}
    </div>
  );
}

/** Systèmes connectés qui reçoivent des pièces par leur API (liste venue de l'API : aucun nom de système ici). */
function ApiTargets({ systemId, companyId, period }: { systemId: string; companyId: string; period: string }) {
  const targets = useAccountingApiTargets(systemId);
  // une liste illisible n'est jamais masquée en silence (ex. API pas à jour) : l'erreur est affichée
  if (targets.error)
    return (
      <div className="space-y-2 border-t border-border pt-4">
        <div className="eyebrow">Send to a connected system</div>
        <ErrorBox error={targets.error} />
      </div>
    );
  if (!targets.data?.length) return null;
  const query = `?company=${encodeURIComponent(companyId)}&period=${encodeURIComponent(period)}`;
  return (
    <div className="space-y-2 border-t border-border pt-4">
      <div className="eyebrow">Send to a connected system</div>
      <div className="flex flex-wrap gap-2">
        {targets.data.map((t) => (
          <Link
            key={t.id}
            href={`/portal/systems/${systemId}/accounting/${t.id}${query}`}
            className={buttonVariants({ variant: "outline" })}
          >
            Prepare for {t.name}
          </Link>
        ))}
      </div>
      <p className="meta">Preview only: nothing is sent from this page.</p>
    </div>
  );
}

function Count({ label, value, tone, hint }: { label: string; value: number; tone?: Tone; hint?: string }) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="meta text-muted-foreground" title={hint}>
          {label}
        </div>
        <div className={tone === "warning" && value > 0 ? "section-title text-warning" : "section-title"}>{value}</div>
      </CardContent>
    </Card>
  );
}

function ReadResult({ result }: { result: AccountingRead }) {
  const c = result.counts;
  // conteneur de requête : la mise en page suit la largeur RÉELLE de la carte, pas celle de la fenêtre
  return (
    <div className="@container space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <Count label="Documents" value={c.documents} />
        <Count label={ELIGIBLE_LABEL} value={c.exportable} hint={ELIGIBLE_HINT} />
        <Count label="With issues" value={c.with_issues} tone="warning" />
      </div>
      <p className="meta text-muted-foreground">{ELIGIBLE_HINT}</p>
      <div className="flex flex-wrap gap-x-6 gap-y-1 meta text-muted-foreground">
        {Object.entries(c.by_type).map(([k, v]) => (
          <span key={k}>
            {k}: {v}
          </span>
        ))}
        {Object.entries(c.by_status).map(([k, v]) => (
          <span key={k}>
            {k}: {v}
          </span>
        ))}
      </div>
      {result.truncated && (
        <div className="rounded-md border border-warning/35 bg-warning/10 p-3 text-warning">
          This month has more documents than can be read at once. Only the first ones are shown.
        </div>
      )}
      {result.unavailable.length > 0 && (
        <div className="space-y-1 rounded-md border border-info/35 bg-info/10 p-3">
          <div className="font-semibold">What your system does not provide</div>
          <ul className="meta list-disc pl-5 text-muted-foreground">
            {result.unavailable.map((u) => (
              <li key={u.message}>{u.message}</li>
            ))}
          </ul>
          <p className="meta text-muted-foreground">Nothing is invented: these values are decided in the mappings below.</p>
        </div>
      )}
      {result.documents.length === 0 ? (
        <p className="text-muted-foreground">No invoices or credit notes for {result.company.name} in this month.</p>
      ) : (
        // largeur FIXE (table-fixed) : le tableau ne dépasse jamais sa carte ; les cellules passent à la ligne au lieu
        // de l'élargir (un nom de partenaire long poussait sinon Débit / Crédit du détail hors de la zone visible)
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead className="w-[31%]">Document</TableHead>
              <TableHead>Partner</TableHead>
              <TableHead className="w-[33%] pr-2 text-right">Total</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {result.documents.map((d, i) => (
              <DocumentRow key={`${d.number ?? d.reference ?? ""}-${i}`} doc={d} />
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="meta text-muted-foreground">{label}</dt>
      <dd className="wrap-anywhere">{children}</dd>
    </div>
  );
}

/** Cellule qui passe à la ligne (y compris dans un identifiant sans espace) au lieu d'élargir le tableau. */
const WRAP = "whitespace-normal wrap-anywhere";
/** Badge de statut d'une ligne du tableau : peut passer à la ligne dans une carte étroite. */
const BADGE_WRAP = "shrink whitespace-normal";

/** Compte de la ligne, ou sa catégorie quand le système source n'a pas de compte (jamais un compte inventé). */
function LineAccount({ line }: { line: AccountingDocument["lines"][number] }) {
  if (!line.account_code && line.category) {
    return <span className="text-muted-foreground">Category {line.category} · no account in your system</span>;
  }
  return (
    <>
      <span className="tech">{line.account_code}</span>{" "}
      <span className="text-muted-foreground">{line.account_name}</span>
    </>
  );
}

function DocumentRow({ doc }: { doc: AccountingDocument }) {
  const [open, setOpen] = useState(false);
  const balance = balanceView(doc.balance, doc.currency);
  const balanceClass = balance.balanced ? "text-success" : "text-danger";
  const lines = doc.lines.map((ln) => {
    const title = lineTitle(ln);
    return {
      title: title.title,
      details: [title.subtitle, ln.taxes.map((t) => t.name).join(", ")].filter(Boolean).join(" · "),
      account: ln,
      side: debitCredit(ln.amount),
    };
  });
  return (
    <Fragment>
      <TableRow className="cursor-pointer" aria-expanded={open} onClick={() => setOpen(!open)}>
        <TableCell>{open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</TableCell>
        <TableCell className={WRAP}>
          <div className="tech">{doc.number ?? "Not yet numbered"}</div>
          <div className="meta text-muted-foreground">{[doc.type, doc.reference].filter(Boolean).join(" · ")}</div>
        </TableCell>
        <TableCell className={WRAP}>{doc.partner?.name ?? "—"}</TableCell>
        <TableCell className="pr-2 pl-2 text-right whitespace-normal">
          <div className="tabular-nums">{formatAmount(doc.total_amount, doc.currency)}</div>
          <div className="mt-1 flex flex-col items-end gap-1">
            <StatusBadge tone={STATUS_TONE[doc.status] ?? "neutral"} className={BADGE_WRAP}>
              {doc.status}
            </StatusBadge>
            {doc.issues.length > 0 && (
              <StatusBadge tone="warning" className={BADGE_WRAP}>
                Needs attention
              </StatusBadge>
            )}
            {doc.balance !== null && !balance.balanced && (
              <StatusBadge tone="danger" className={BADGE_WRAP}>
                Unbalanced
              </StatusBadge>
            )}
          </div>
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={4} className="space-y-4 whitespace-normal">
            {doc.issues.map((x) => (
              <div key={x.message} className="text-warning">
                {x.message}
              </div>
            ))}
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 @xl:grid-cols-4">
              <Fact label="Document">
                <span className="tech">{doc.number ?? "Not yet numbered"}</span>
              </Fact>
              <Fact label="Type">{doc.type}</Fact>
              <Fact label="Partner">{doc.partner ? `${doc.partner.name} (${doc.partner.role})` : "—"}</Fact>
              <Fact label="Currency">{doc.currency ?? "—"}</Fact>
              <Fact label="Document date">{doc.document_date ?? "—"}</Fact>
              <Fact label="Accounting date">{doc.accounting_date ?? "—"}</Fact>
              <Fact label="Due date">{doc.due_date ?? "—"}</Fact>
              <Fact label="Total · Tax">
                {formatAmount(doc.total_amount, doc.currency)} · {formatAmount(doc.tax_amount, doc.currency)}
              </Fact>
            </dl>

            {/* carte assez large : tableau Line | Account | Debit | Credit, colonnes fixes ; Débit / Crédit
                compacts et toujours visibles, Line / Account passent à la ligne */}
            <div className="hidden @xl:block">
              <Table className="table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead>Line</TableHead>
                    <TableHead className="w-[38%]">Account</TableHead>
                    <TableHead className="w-[17%] text-right">Debit</TableHead>
                    <TableHead className="w-[17%] text-right">Credit</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {lines.map((l, i) => (
                    <TableRow key={i}>
                      <TableCell className={`align-top ${WRAP}`}>
                        <div>{l.title}</div>
                        {l.details && <div className="meta text-muted-foreground">{l.details}</div>}
                      </TableCell>
                      <TableCell className={`align-top ${WRAP}`}>
                        <LineAccount line={l.account} />
                      </TableCell>
                      <TableCell className="text-right align-top tabular-nums">{l.side.debit ?? "—"}</TableCell>
                      <TableCell className="text-right align-top tabular-nums">{l.side.credit ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={2} className="font-semibold">
                      Balance
                    </TableCell>
                    <TableCell colSpan={2} className={`text-right font-semibold tabular-nums ${balanceClass}`}>
                      {balance.text}
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>

            {/* carte étroite : une ligne comptable par bloc, montant sous le compte avec son sens */}
            <ul className="divide-y divide-border @xl:hidden">
              {lines.map((l, i) => (
                <li key={i} className="space-y-0.5 py-2.5">
                  <div className="wrap-anywhere">{l.title}</div>
                  {l.details && <div className="meta text-muted-foreground">{l.details}</div>}
                  <div className="wrap-anywhere">
                    <LineAccount line={l.account} />
                  </div>
                  <div className="flex justify-between gap-3 tabular-nums">
                    <span className="text-muted-foreground">
                      {l.side.debit ? "Debit" : l.side.credit ? "Credit" : "—"}
                    </span>
                    <span>
                      {l.side.debit ?? l.side.credit ?? "—"} {doc.currency}
                    </span>
                  </div>
                </li>
              ))}
              <li className="flex justify-between gap-3 py-2.5 font-semibold">
                <span>Balance</span>
                <span className={`tabular-nums ${balanceClass}`}>{balance.text}</span>
              </li>
            </ul>

            {doc.balance !== null && !balance.balanced && (
              <div className="rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">
                Debit and credit of this document do not match.
              </div>
            )}
            <p className="meta text-muted-foreground">
              Debit / Credit is a view of the signed amounts read from your system (debit positive, credit negative).
              Amounts in {doc.currency ?? "the document currency"}.
            </p>
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  );
}
