"use client";

/** Product Catalog Analysis (page supplier, V1b). Le moteur a déjà tout classé ; le supplier ne répond
 *  qu'aux questions ambiguës. Tous les compteurs, propositions et raisons viennent de l'API : aucune règle
 *  ni logique sémantique ici, seulement la mise en forme (libellés métier d'abord, noms techniques ensuite). */
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  CircleCheck,
  RotateCcw,
  Sparkles,
  X,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ErrorBox, formatDate, formatInt, LoadingRows } from "@/components/registry";
import { OriginBadge } from "@/components/origin";
import { Pill, type Tone } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ApiError, type AiStatus, type BindingView, type CatalogAnalysisSummary } from "@/lib/api/client";
import {
  useAiStatus,
  useAnalyzeProductDomain,
  useCatalogAnalysis,
  useCatalogDecisions,
  useCatalogExclusions,
  useCatalogQuestions,
  useEnrichAi,
  useFieldDecision,
  useInstance,
} from "@/lib/api/hooks";
import { useUrlState } from "@/lib/use-url-state";
import { cn } from "@/lib/utils";

import { ChangeMeaningDialog } from "./change-meaning-dialog";

// libellés métier des contrôles de structure (rôles d'entité renvoyés par l'API)
const STRUCTURE_LABEL: Record<string, string> = {
  PRIMARY: "Products detected",
  VARIANT: "Variants",
  CATEGORY: "Categories",
  ATTRIBUTE: "Attributes",
  MEDIA: "Images",
};

const TYPE_LABEL: Record<string, string> = {
  string: "Text",
  text: "Long text",
  integer: "Whole number",
  decimal: "Number",
  boolean: "Yes / No",
  date: "Date",
  datetime: "Date and time",
  enum: "Choice list",
  binary: "File / image",
  json: "Structured data",
  reference: "Link",
  collection: "List",
};

/** Type lisible : « Link to project.project », « Text »… */
export function readableType(f: BindingView["field"]): string {
  const base = TYPE_LABEL[f.data_type ?? ""] ?? f.native_type ?? "Unknown";
  return f.relation_target ? `${base} to ${f.relation_target}` : base;
}

type Review = "all" | "extensions";

export function CatalogView({ id }: { id: string }) {
  const url = useUrlState();
  const review = (["all", "extensions"] as const).find((r) => r === url.get("review"));
  const instance = useInstance(id);
  const analysis = useCatalogAnalysis(id);
  const analyze = useAnalyzeProductDomain(id);
  const enriched = !!analysis.data?.enriched;
  const questions = useCatalogQuestions(id, enriched);
  const decisions = useCatalogDecisions(id, enriched);
  const ai = useAiStatus(id, enriched);

  if (instance.error) return <ErrorBox error={instance.error} />;
  if (!instance.data) return <LoadingRows rows={10} />;
  const i = instance.data;
  const neverAnalyzed = analysis.error instanceof ApiError && analysis.error.status === 404;
  const notAnalyzed = neverAnalyzed || (!!analysis.data && !enriched);
  const setReview = (r: Review | undefined) => url.set({ review: r === review ? undefined : r });

  return (
    <div className="page-stack">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="hero-badge">Product Catalog Analysis</h1>
          <p className="page-description text-muted-foreground">
            We analysed the product structure of this system. Only the fields we could not identify with
            certainty need your answer.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/instances/${id}/product-domain`}
            className={cn(buttonVariants({ variant: "outline" }), "h-10 px-4")}
          >
            Advanced view <ArrowRight />
          </Link>
          <Button
            size="lg"
            className="h-10 px-4"
            onClick={() => analyze.mutate()}
            disabled={analyze.isPending || !i.last_discovery}
            title={i.last_discovery ? undefined : "Run Discover Schema on the instance first"}
          >
            {analyze.isPending ? "Analyzing…" : notAnalyzed ? "Analyze" : "Re-analyze"}
          </Button>
        </div>
      </div>

      {!!analyze.error && <ErrorBox error={analyze.error} />}

      {notAnalyzed ? (
        <Card>
          <CardContent className="meta">
            {i.last_discovery
              ? "The catalog of this instance has not been analysed yet. Run Analyze."
              : "No schema snapshot yet. Run Discover Schema on the instance, then Analyze."}
          </CardContent>
        </Card>
      ) : analysis.error ? (
        <ErrorBox error={analysis.error} />
      ) : !analysis.data ? (
        <LoadingRows rows={8} />
      ) : (
        <>
          <Overview s={analysis.data} review={review} onReview={setReview} ai={ai.data} id={id} />
          {review && (
            <ExclusionsReview
              id={id}
              extensionsOnly={review === "extensions"}
              onClose={() => setReview(undefined)}
            />
          )}
          {analysis.data.complete ? (
            <CompleteCard s={analysis.data} />
          ) : (
            <Questions id={id} rows={questions.data} error={questions.error} />
          )}
          <Decisions id={id} rows={decisions.data} error={decisions.error} />
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Synthèse                                                            */
/* ------------------------------------------------------------------ */

function Overview({
  s,
  review,
  onReview,
  ai,
  id,
}: {
  s: CatalogAnalysisSummary;
  review?: Review;
  onReview: (r: Review) => void;
  ai?: AiStatus;
  id: string;
}) {
  return (
    <Card>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {s.structure.map((c) => (
            <span
              key={c.role}
              className={cn(
                "inline-flex items-center gap-1.5 font-medium",
                c.present ? "text-success" : "text-danger",
              )}
            >
              {c.present ? <Check className="size-4" /> : <X className="size-4" />}
              {STRUCTURE_LABEL[c.role] ?? c.role}
            </span>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Counter value={s.identified} label="Identified catalog fields" />
          <Counter
            value={s.excluded_auto}
            label="Automatically excluded"
            action={
              <ReviewButton
                active={review === "all"}
                onClick={() => onReview("all")}
                disabled={!s.excluded_auto}
              />
            }
          />
          <Counter
            value={s.possible}
            label="Possible"
            hint={<AiHint ai={ai} />}
            action={<AskAi id={id} ai={ai} />}
          />
          <Counter
            value={s.pending}
            label="Need your confirmation"
            className={s.pending ? "border-warning/40 bg-warning/5" : undefined}
            valueClassName={s.pending ? "text-warning" : "text-success"}
          />
        </div>
        {s.extensions > 0 && (
          <div className="flex flex-wrap items-center gap-3 border-t pt-4">
            <span>
              <span className="font-semibold">{formatInt(s.extensions_excluded_auto)}</span> of{" "}
              {formatInt(s.extensions)} custom or third-party extensions were automatically excluded as non-catalog
              data
            </span>
            <ReviewButton
              active={review === "extensions"}
              onClick={() => onReview("extensions")}
              disabled={!s.extensions_excluded_auto}
            />
          </div>
        )}
        <p className="meta">
          Analysis of instance snapshot #{s.instance_schema_snapshot_sequence} · {formatDate(s.analyzed_at)}
          {s.rules_version && (
            <>
              {" "}
              · rules <span className="tech">{s.rules_version}</span>
            </>
          )}
        </p>
      </CardContent>
    </Card>
  );
}

function Counter({
  value,
  label,
  hint,
  action,
  className,
  valueClassName,
}: {
  value: number;
  label: string;
  hint?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  valueClassName?: string;
}) {
  return (
    <Card size="sm" className={className}>
      <CardContent className="flex items-end justify-between gap-3">
        <div>
          <div className={cn("text-3xl font-bold tabular-nums", valueClassName)}>{formatInt(value)}</div>
          <div className="font-medium">{label}</div>
          {hint && <div className="meta">{hint}</div>}
        </div>
        {action}
      </CardContent>
    </Card>
  );
}

function ReviewButton({ active, onClick, disabled }: { active: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={onClick} disabled={disabled} aria-expanded={active}>
      Review <ChevronDown className={cn("transition-transform", active && "rotate-180")} />
    </Button>
  );
}

function CompleteCard({ s }: { s: CatalogAnalysisSummary }) {
  return (
    <Card className="border-success/40">
      <CardContent className="flex items-center gap-4">
        <CircleCheck className="size-10 shrink-0 text-success" />
        <div>
          <div className="section-title">Catalog analysis complete</div>
          <div className="text-muted-foreground">{s.pending} fields pending</div>
          <div className="text-muted-foreground">Analysis saved</div>
        </div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Questions                                                           */
/* ------------------------------------------------------------------ */

function Questions({ id, rows, error }: { id: string; rows?: BindingView[]; error: unknown }) {
  // dialogue « Change » ouvert = ?change=<entité.champ> (état partageable, comme ?review=)
  const url = useUrlState();
  const changing = rows?.find((q) => q.path === url.get("change")) ?? null;
  const setChanging = (q: BindingView | null) => url.set({ change: q?.path });
  const decide = useFieldDecision(id);
  if (error) return <ErrorBox error={error} />;
  if (!rows) return <LoadingRows rows={4} />;
  return (
    <section className="space-y-3">
      <h2 className="section-title">Questions ({rows.length})</h2>
      {!!decide.error && <ErrorBox error={decide.error} />}
      {rows.map((q) => {
        const ref = { entity: q.entity_technical_name, field: q.field_technical_name };
        const busy =
          decide.isPending && decide.variables?.entity === ref.entity && decide.variables?.field === ref.field;
        return (
          <QuestionCard
            key={q.path}
            q={q}
            busy={busy}
            onConfirm={() => decide.mutate({ action: "confirm", ...ref })}
            onChange={() => setChanging(q)}
            onIgnore={() => decide.mutate({ action: "ignore", ...ref })}
          />
        );
      })}
      <ChangeMeaningDialog
        question={changing}
        pending={decide.isPending}
        onCancel={() => setChanging(null)}
        onSubmit={(key, note) => {
          if (!changing) return;
          decide.mutate(
            {
              action: "change",
              entity: changing.entity_technical_name,
              field: changing.field_technical_name,
              semantic_key: key,
              note,
            },
            { onSuccess: () => setChanging(null) },
          );
        }}
      />
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Ask AI (V1c) : seul point de la page qui appelle un LLM, sur clic     */
/* ------------------------------------------------------------------ */

function AiHint({ ai }: { ai?: AiStatus }) {
  if (!ai) return <>Kept aside, no answer needed</>;
  if (!ai.configured) return <>Kept aside · AI not configured (see .env.example)</>;
  if (ai.candidates === 0) return <>Kept aside, no answer needed</>;
  return (
    <>
      {formatInt(ai.candidates)} could be clarified by AI
    </>
  );
}

function AskAi({ id, ai }: { id: string; ai?: AiStatus }) {
  const [open, setOpen] = useState(false);
  const enrich = useEnrichAi(id);
  if (!ai?.configured || ai.candidates === 0) return null;
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} disabled={enrich.isPending}>
        <Sparkles /> {enrich.isPending ? "Asking…" : "Ask AI"}
      </Button>
      <Dialog open={open} onOpenChange={(o) => !enrich.isPending && setOpen(o)}>
        <DialogContent>
          <DialogTitle>Ask AI</DialogTitle>
          <DialogDescription>
            Send field metadata of {formatInt(ai.candidates)} fields to {ai.provider}
            {ai.model ? ` (${ai.model})` : ""}? No business data is sent. Every suggestion stays a question for
            you to confirm.
          </DialogDescription>
          {!!enrich.error && <ErrorBox error={enrich.error} />}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setOpen(false)} disabled={enrich.isPending}>
              Cancel
            </Button>
            <Button
              onClick={() => enrich.mutate(undefined, { onSuccess: () => setOpen(false) })}
              disabled={enrich.isPending}
            >
              <Sparkles /> {enrich.isPending ? "Asking…" : "Send"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

function sourceLabel(q: BindingView) {
  if (q.source === "LLM") return "Suggested by AI";
  // décision HUMAN encore en question (reopen, include, STALE) : la clé est-elle l'ancienne réponse ?
  if (q.source === "HUMAN") {
    return q.previous?.semantic_key === q.semantic_key ? "Your previous answer" : "Suggested by rule";
  }
  if (q.source === "STANDARD") return "Declared by the domain pack";
  return "Suggested by rule";
}

function QuestionCard({
  q,
  busy,
  onConfirm,
  onChange,
  onIgnore,
}: {
  q: BindingView;
  busy: boolean;
  onConfirm: () => void;
  onChange: () => void;
  onIgnore: () => void;
}) {
  const stale = q.meaning_status === "STALE";
  const reopened = !stale && !!q.previous;
  return (
    <Card size="sm" className={cn(stale && "border-danger/40")}>
      <CardContent className="space-y-3">
        {reopened && (
          <div className="flex items-start gap-2 rounded-md border border-info/35 bg-info/10 px-3 py-2 text-info">
            <RotateCcw className="mt-0.5 size-4 shrink-0" />
            <span>
              You reopened this answer. Previous answer: <span className="font-semibold">{previousAnswer(q)}</span>
            </span>
          </div>
        )}
        {stale && (
          <div className="flex items-start gap-2 rounded-md border border-danger/35 bg-danger/10 px-3 py-2 text-danger">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>
              This field changed since your last answer. Your previous answer is pre-selected: confirm it or choose
              another meaning.
            </span>
          </div>
        )}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="item-title">{q.field.label ?? q.field_technical_name}</div>
            <div className="meta">
              <span className="tech">{q.path}</span> · {readableType(q.field)}
            </div>
          </div>
          <OriginBadge origin={q.origin} title={q.field.origin_reason} />
        </div>
        <div>
          {q.semantic_key ? (
            <>
              <div>
                We think this means: <span className="font-semibold">{q.semantic_label ?? q.semantic_key}</span>
              </div>
              <div className="meta flex flex-wrap items-center gap-x-1.5">
                {q.source === "LLM" && <Sparkles className="size-3.5 text-info" />}
                {sourceLabel(q)}
                {q.confidence != null && ` · ${Math.round(q.confidence * 100)}% confidence`}
                {q.reason && !stale && q.source !== "LLM" && ` · ${q.reason}`}
                {q.low_confidence && (
                  <Pill tone="warning" className="ml-1">
                    Low confidence
                  </Pill>
                )}
              </div>
              {q.source === "LLM" && q.justification && <div className="italic">“{q.justification}”</div>}
            </>
          ) : q.note_ai ? (
            <>
              <div className="text-muted-foreground">
                No suggestion: AI could not identify this field with confidence.
              </div>
              <div className="meta italic">“{q.note_ai}”</div>
            </>
          ) : (
            <>
              <div className="text-muted-foreground">No suggestion: we could not recognise this field.</div>
              {q.reason && <div className="meta">{q.reason}</div>}
            </>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={onConfirm}
            disabled={busy || !q.semantic_key}
            title={q.semantic_key ? undefined : "No suggestion to confirm: use Change"}
          >
            <Check /> Confirm
          </Button>
          <Button variant="outline" onClick={onChange} disabled={busy}>
            Change
          </Button>
          <Button variant="outline" onClick={onIgnore} disabled={busy}>
            Ignore
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Ancienne réponse d'une question rouverte (`previous` renvoyé par l'API). */
function previousAnswer(q: BindingView): string {
  const prev = q.previous as { meaning_status?: string; semantic_key?: string | null } | null;
  if (prev?.meaning_status === "IGNORED") return "Ignored";
  if (prev?.semantic_key) {
    return prev.semantic_key === q.semantic_key ? (q.semantic_label ?? prev.semantic_key) : prev.semantic_key;
  }
  return "Other / I don't know";
}

/* ------------------------------------------------------------------ */
/* Réponses du supplier : modifiables, ou à rouvrir en cas d'erreur     */
/* ------------------------------------------------------------------ */

const ANSWER: Record<string, { label: string; tone: Tone }> = {
  CONFIRMED: { label: "Confirmed", tone: "success" },
  IGNORED: { label: "Ignored", tone: "neutral" },
  UNRESOLVED: { label: "Other / I don't know", tone: "warning" },
};

function Decisions({ id, rows, error }: { id: string; rows?: BindingView[]; error: unknown }) {
  // même paramètre ?change= que les questions : un champ est soit une question, soit une réponse
  const url = useUrlState();
  const changing = rows?.find((d) => d.path === url.get("change")) ?? null;
  const setChanging = (d: BindingView | null) => url.set({ change: d?.path });
  const decide = useFieldDecision(id);
  if (error) return <ErrorBox error={error} />;
  if (!rows?.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your answers ({rows.length})</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="meta">
          The answers you gave, most recent first. If one is wrong, change it, or reopen it: it becomes a question
          again.
        </p>
        {!!decide.error && <ErrorBox error={decide.error} />}
        <div className="overflow-x-auto">
          <table className="w-full text-base">
            <thead>
              <tr className="meta border-b text-left">
                <th className="py-2 pr-3 font-normal">Field</th>
                <th className="px-3 py-2 font-normal">Your answer</th>
                <th className="px-3 py-2 font-normal">Answered</th>
                <th className="py-2 pl-3 font-normal" />
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => {
                const ref = { entity: d.entity_technical_name, field: d.field_technical_name };
                const busy =
                  decide.isPending &&
                  decide.variables?.entity === ref.entity &&
                  decide.variables?.field === ref.field;
                const answer = ANSWER[d.meaning_status] ?? { label: d.meaning_status, tone: "neutral" as Tone };
                return (
                  <tr key={d.path} className="border-b align-top last:border-0">
                    <td className="py-2 pr-3">
                      <div className="font-medium">{d.field.label ?? d.field_technical_name}</div>
                      <div className="meta">
                        <span className="tech">{d.path}</span> · {readableType(d.field)}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Pill tone={answer.tone}>{answer.label}</Pill>
                        {d.semantic_key && (
                          <span className="font-semibold">{d.semantic_label ?? d.semantic_key}</span>
                        )}
                      </div>
                      {d.note && <div className="meta">Note: {d.note}</div>}
                    </td>
                    <td className="meta px-3 py-2 whitespace-nowrap">
                      {formatDate(d.confirmed_at ?? d.updated_at)}
                    </td>
                    <td className="py-2 pl-3">
                      <div className="flex justify-end gap-2">
                        <Button variant="outline" size="sm" disabled={busy} onClick={() => setChanging(d)}>
                          Change
                        </Button>
                        {d.meaning_status !== "IGNORED" && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy}
                            onClick={() => decide.mutate({ action: "ignore", ...ref })}
                          >
                            Ignore
                          </Button>
                        )}
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={() => decide.mutate({ action: "reopen", ...ref })}
                          title="Ask this question again"
                        >
                          <RotateCcw /> Reopen
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <ChangeMeaningDialog
          question={changing}
          pending={decide.isPending}
          onCancel={() => setChanging(null)}
          onSubmit={(key, note) => {
            if (!changing) return;
            decide.mutate(
              {
                action: "change",
                entity: changing.entity_technical_name,
                field: changing.field_technical_name,
                semantic_key: key,
                note,
              },
              { onSuccess: () => setChanging(null) },
            );
          }}
        />
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Review des exclusions automatiques                                  */
/* ------------------------------------------------------------------ */

function ExclusionsReview({
  id,
  extensionsOnly,
  onClose,
}: {
  id: string;
  extensionsOnly: boolean;
  onClose: () => void;
}) {
  const rows = useCatalogExclusions(id, extensionsOnly, true);
  const decide = useFieldDecision(id);
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>
          {extensionsOnly ? "Excluded extensions" : "Automatically excluded fields"}
          {rows.data && ` (${rows.data.length})`}
        </CardTitle>
        <Button variant="outline" size="sm" onClick={onClose}>
          Close
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="meta">
          These fields were excluded by a rule. If one of them belongs to your catalog, include it: it becomes a
          question.
        </p>
        {!!decide.error && <ErrorBox error={decide.error} />}
        {rows.error ? (
          <ErrorBox error={rows.error} />
        ) : !rows.data ? (
          <LoadingRows />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-base">
              <thead>
                <tr className="meta border-b text-left">
                  <th className="py-2 pr-3 font-normal">Field</th>
                  <th className="px-3 py-2 font-normal">Why it was excluded</th>
                  <th className="px-3 py-2 font-normal">Origin</th>
                  <th className="py-2 pl-3 font-normal" />
                </tr>
              </thead>
              <tbody>
                {rows.data.map((r) => (
                  <tr key={r.path} className="border-b align-top last:border-0">
                    <td className="py-2 pr-3">
                      <div className="font-medium">{r.field.label ?? r.field_technical_name}</div>
                      <div className="meta">
                        <span className="tech">{r.path}</span> · {readableType(r.field)}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      {r.reason}
                      {r.rule_id && (
                        <Pill tone="neutral" mono className="ml-2 align-middle">
                          {r.rule_id}
                        </Pill>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <OriginBadge origin={r.origin} title={r.field.origin_reason} />
                    </td>
                    <td className="py-2 pl-3 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={decide.isPending}
                        onClick={() =>
                          decide.mutate({
                            action: "include",
                            entity: r.entity_technical_name,
                            field: r.field_technical_name,
                          })
                        }
                      >
                        Include
                      </Button>
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
