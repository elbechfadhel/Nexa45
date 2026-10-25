"use client";

/** Analyse du catalogue côté fournisseur (V2b.2) : une couche d'écran au-dessus du moteur V1 (Analyze = connexion,
 *  structure, champs du catalogue ; puis questions éventuelles, avec les mêmes réponses que V1, libellées pour le
 *  fournisseur : Correct = confirm, Change / Choose meaning = change, Not relevant = ignore). Questions avec
 *  proposition d'abord ; champs sans proposition regroupés dans une section repliée (« Review »). Aucune donnée
 *  technique. Pas de synchronisation ici (F3) : « Continue to catalog » ouvre le catalogue V2a, où « Sync catalog »
 *  lit les produits. Seules les STRUCTURES détectées sont indiquées, jamais des nombres. */
import { CheckCircle2, Circle, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { FormError } from "@/components/form";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { Pill } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type AnalysisDomain,
  type Answer,
  type SupplierQuestion,
  type SupplierSystem,
  useAnalyzeCatalog,
  useAnswerQuestion,
  useCatalogSummary,
  useIgnoreAll,
  useProductTargets,
  useSupplierQuestions,
  useSupplierVocabulary,
} from "@/lib/api/supplier-hooks";
import { cn } from "@/lib/utils";

const STEPS = ["Connection checked", "Reading product structure", "Identifying catalog fields"];

export function CatalogAnalysis({ system }: { system: SupplierSystem }) {
  const summary = useCatalogSummary(system.id);
  const analyze = useAnalyzeCatalog(system.id);
  const status = summary.data?.status ?? system.catalog.status;
  const questionsOpen = status === "questions_open";
  const questions = useSupplierQuestions(system.id, questionsOpen && !analyze.isPending);

  if (analyze.isPending) return <Analyzing />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Catalog analysis</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {analyze.error ? (
          // toujours la même phrase simple, quelle que soit la cause technique (jamais un code HTTP ni une exception)
          <div className="space-y-3">
            <div className="rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">
              We couldn&apos;t analyze your system. Please check the connection and try again.
            </div>
            <Button onClick={() => analyze.mutate()}>Try again</Button>
          </div>
        ) : null}

        {!analyze.error && (status === "awaiting_setup" || status === "not_analyzed") && (
          <div className="space-y-4">
            <p>
              Your system is connected.
              <br />
              Analyze it to identify your product catalog.
            </p>
            <Button onClick={() => analyze.mutate()}>Analyze</Button>
          </div>
        )}

        {!analyze.error && questionsOpen && (
          <div className="space-y-4">
            {questions.error && <ErrorBox error={questions.error} />}
            {!questions.data ? (
              <LoadingRows rows={3} />
            ) : (
              <Questions systemId={system.id} questions={questions.data} domain="catalog" />
            )}
          </div>
        )}

        {!analyze.error && status === "complete" && summary.data && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-success">
              <CheckCircle2 className="size-5" />
              <span className="font-semibold">Catalog analysis complete</span>
            </div>
            <ul className="grid gap-2 sm:grid-cols-2">
              {summary.data.structure.map((s) => (
                <li key={s.label} className={cn("flex items-center gap-2", !s.present && "text-muted-foreground")}>
                  {s.present ? <CheckCircle2 className="size-4 text-success" /> : <Circle className="size-4" />}
                  {s.label} {s.present ? "detected" : "not found"}
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground">No questions need your attention.</p>
            <div className="flex flex-wrap gap-2">
              <Link href={`/portal/catalog?system=${system.id}`} className={buttonVariants()}>
                Continue to catalog
              </Link>
              <Button variant="outline" onClick={() => analyze.mutate()}>
                <RefreshCw /> Re-analyze
              </Button>
            </div>
            <ProductTargets systemId={system.id} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Systèmes connectés qui peuvent recevoir ces produits (liste venue de l'API : aucun nom de système ici). */
function ProductTargets({ systemId }: { systemId: string }) {
  const targets = useProductTargets(systemId);
  // une liste illisible n'est jamais masquée en silence : l'erreur est affichée
  if (targets.error)
    return (
      <div className="space-y-2 border-t border-border pt-4">
        <div className="eyebrow">Send to a connected system</div>
        <ErrorBox error={targets.error} />
      </div>
    );
  if (!targets.data?.length) return null;
  return (
    <div className="space-y-2 border-t border-border pt-4">
      <div className="eyebrow">Send to a connected system</div>
      <div className="flex flex-wrap gap-2">
        {targets.data.map((t) => (
          <Link
            key={t.system_id}
            href={`/portal/systems/${systemId}/products/${t.system_id}`}
            className={buttonVariants({ variant: "outline" })}
          >
            Prepare for {t.name}
          </Link>
        ))}
      </div>
      <p className="meta">Preview only: nothing is sent.</p>
    </div>
  );
}

function Analyzing() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Analyzing your catalog…</CardTitle>
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

type QuestionProps = { systemId: string; question: SupplierQuestion; domain: AnalysisDomain };

function QuestionCard({ systemId, question: q, domain }: QuestionProps) {
  const answer = useAnswerQuestion(systemId, domain);
  const [changing, setChanging] = useState(false);
  const send = (a: Answer) => answer.mutate({ entity: q.field.entity, field: q.field.field, answer: a });
  const s = q.suggestion;

  return (
    <Card size="sm">
      <CardContent className="space-y-3">
        <div>
          <div className="item-title">{q.field.label}</div>
          <div className="meta">
            {q.field.type}
            {q.field.required && " · required"}
          </div>
          {q.field.description && <p className="meta mt-1">{q.field.description}</p>}
        </div>

        {q.changed_since_answer && (
          <p className="rounded-md border border-warning/35 bg-warning/10 p-2 text-sm">
            This field changed since your last answer
            {q.previous_answer ? ` (you said: ${q.previous_answer.label})` : ""}. Please confirm again.
          </p>
        )}

        {s ? (
          <div className="space-y-1">
            <div>
              We think this means: <span className="font-semibold">{s.meaning.label}</span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone="neutral" variant="neutral">
                {s.source === "ai" ? "Suggested by AI" : s.source === "you" ? "Your previous answer" : "Suggested by rules"}
                {s.confidence != null && s.source !== "you" && ` · ${Math.round(s.confidence * 100)}%`}
              </Pill>
              {s.low_confidence && <Pill tone="warning">Low confidence</Pill>}
            </div>
            {s.justification && <p className="meta">{s.justification}</p>}
          </div>
        ) : (
          // la raison d'abstention de l'IA est un texte libre du modèle (il peut citer des noms techniques) :
          // jamais affichée au fournisseur
          <div>We couldn&apos;t identify this field automatically.</div>
        )}

        {answer.error && <FormError error={answer.error} />}

        {changing ? (
          <ChangeMeaning
            onCancel={() => setChanging(false)}
            onPick={(meaning) => send({ action: "change", body: { meaning, note: null } })}
            pending={answer.isPending}
            domain={domain}
          />
        ) : (
          <div className="flex flex-wrap gap-2">
            {s && (
              <Button
                size="sm"
                disabled={answer.isPending}
                onClick={() => send({ action: "confirm", body: { meaning: null, note: null } })}
              >
                Correct
              </Button>
            )}
            <Button size="sm" variant="outline" disabled={answer.isPending} onClick={() => setChanging(true)}>
              Change
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={answer.isPending}
              onClick={() => send({ action: "ignore", body: { note: null } })}
            >
              Not relevant
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Champ sans proposition : ligne compacte, mêmes actions (change = « Choose meaning », ignore = « Not relevant »)
 *  et même sélecteur de vocabulaire que les questions avec proposition. */
function UnsureRow({ systemId, question: q, domain }: QuestionProps) {
  const answer = useAnswerQuestion(systemId, domain);
  const [choosing, setChoosing] = useState(false);
  const send = (a: Answer) => answer.mutate({ entity: q.field.entity, field: q.field.field, answer: a });

  return (
    <li className="space-y-2 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="font-medium">{q.field.label}</div>
          <div className="meta">{q.field.type}</div>
        </div>
        {!choosing && (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={answer.isPending} onClick={() => setChoosing(true)}>
              Choose meaning
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={answer.isPending}
              onClick={() => send({ action: "ignore", body: { note: null } })}
            >
              Not relevant
            </Button>
          </div>
        )}
      </div>
      {choosing && (
        <ChangeMeaning
          onCancel={() => setChoosing(false)}
          onPick={(meaning) => send({ action: "change", body: { meaning, note: null } })}
          pending={answer.isPending}
          domain={domain}
        />
      )}
      {answer.error && <FormError error={answer.error} />}
    </li>
  );
}

/** Questions ouvertes : d'abord celles avec une proposition (règles, IA ou réponse rouverte), puis les champs
 *  sans proposition, regroupés dans une section repliée. Mêmes données, mêmes réponses qu'avant. */
export function Questions({
  systemId,
  questions,
  domain,
}: {
  systemId: string;
  questions: SupplierQuestion[];
  domain: AnalysisDomain;
}) {
  const suggested = questions.filter((q) => q.suggestion);
  const unsure = questions.filter((q) => !q.suggestion);
  // plus aucune proposition : les champs restants sont la seule étape avant « Catalog analysis complete »,
  // la section s'ouvre d'elle-même (le fournisseur peut toujours la replier)
  const [reviewing, setReviewing] = useState<boolean | null>(null);
  const open = reviewing ?? suggested.length === 0;
  const ignoreAll = useIgnoreAll(systemId, domain);
  const [confirmAll, setConfirmAll] = useState(false);
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

  return (
    <div className="space-y-4">
      <div>
        <p className="font-medium">
          {plural(questions.length, "field needs", "fields need")} your attention
        </p>
        <p className="meta">
          {plural(suggested.length, "suggestion", "suggestions")} to review ·{" "}
          {plural(unsure.length, "field", "fields")} we&apos;re not sure about
        </p>
      </div>

      {suggested.length > 0 && (
        <div className="space-y-4">
          {suggested.map((q) => (
            <QuestionCard key={`${q.field.entity}.${q.field.field}`} systemId={systemId} question={q} domain={domain} />
          ))}
        </div>
      )}

      {unsure.length > 0 && (
        <Card size="sm">
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="item-title">Not sure about these fields? ({unsure.length})</div>
                <p className="meta">
                  We couldn&apos;t identify these fields automatically. You can review them or mark them as not
                  relevant.
                </p>
                {suggested.length === 0 && (
                  <p className="mt-1 font-medium">Answer these remaining fields to finish the analysis.</p>
                )}
              </div>
              <Button size="sm" variant="outline" onClick={() => setReviewing(!open)} aria-expanded={open}>
                {open ? "Hide" : "Review"}
              </Button>
            </div>
            {open && (
              <div className="flex flex-wrap items-center gap-2">
                {confirmAll ? (
                  <>
                    <span className="text-sm">
                      Mark these {unsure.length} fields as not relevant?
                    </span>
                    <Button
                      size="sm"
                      disabled={ignoreAll.isPending}
                      onClick={() =>
                        ignoreAll.mutate(
                          unsure.map((q) => ({ entity: q.field.entity, field: q.field.field })),
                          { onSettled: () => setConfirmAll(false) },
                        )
                      }
                    >
                      {ignoreAll.isPending ? "Saving…" : "Yes, mark all"}
                    </Button>
                    <Button size="sm" variant="ghost" disabled={ignoreAll.isPending} onClick={() => setConfirmAll(false)}>
                      Cancel
                    </Button>
                  </>
                ) : (
                  <Button size="sm" variant="outline" onClick={() => setConfirmAll(true)}>
                    Mark all as not relevant
                  </Button>
                )}
              </div>
            )}
            {ignoreAll.error && <FormError error={ignoreAll.error} />}
            {open && (
              <ul className="divide-y divide-border border-t border-border pt-3">
                {unsure.map((q) => (
                  <UnsureRow key={`${q.field.entity}.${q.field.field}`} systemId={systemId} question={q} domain={domain} />
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ChangeMeaning({
  onPick,
  onCancel,
  pending,
  domain,
}: {
  onPick: (meaning: string) => void;
  onCancel: () => void;
  pending: boolean;
  domain: AnalysisDomain;
}) {
  const vocab = useSupplierVocabulary(true, domain);
  const [meaning, setMeaning] = useState("");
  if (!vocab.data) return <LoadingRows rows={1} />;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={meaning}
        onChange={(e) => setMeaning(e.target.value)}
        className="input w-auto min-w-64"
        aria-label="Meaning of this field"
      >
        <option value="">Choose the meaning…</option>
        {vocab.data.groups.map((g) => (
          <optgroup key={g.group} label={g.group}>
            {g.entries.map((e) => (
              <option key={e.key} value={e.key}>
                {e.label}
              </option>
            ))}
          </optgroup>
        ))}
        <option value={vocab.data.other.key}>{vocab.data.other.label}</option>
      </select>
      <Button size="sm" disabled={!meaning || pending} onClick={() => onPick(meaning)}>
        Save
      </Button>
      <Button size="sm" variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}
