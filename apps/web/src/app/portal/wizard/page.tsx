"use client";

/** AI Migration Wizard (portail fournisseur). Deux modes : SIMPLE (défaut, `components/wizard-simple` : le fichier
 *  d'abord, la cible, une synthèse avec au plus 3 questions présélectionnées, le fichier préparé) et EXPERT
 *  (questionnaire puis structure cible complète : configuration, champs, types, statuts, exemples ; une question à
 *  la fois, Back / Continue, progression). Aucune question ni aucun choix en dur : tout vient de l'API ; l'IA n'est appelée que
 *  par le serveur ; le fichier du client n'est jamais stocké ni envoyé à un système. Un fichier préparé n'est
 *  jamais dit « prêt à importer ». Logique pure : `lib/wizard/flow.mjs`. */
import { Download, FileSpreadsheet, FileUp, Loader2, RotateCcw, Sparkles, Upload } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { PortalGate } from "@/components/portal";
import { SimpleWizard } from "@/components/wizard-simple/simple-wizard";
import { TemplateSidebar } from "@/components/wizard-templates";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  type FileMapping,
  type Fill,
  type MissingInfo,
  type MissingQuestion,
  type PreparedFile,
  type TargetProposal,
  type ValueQuestion,
  type WizardAnswer,
  type WizardMode,
  type WizardQuestion,
  type WizardTemplate,
  useWizardExcel,
  useWizardMapping,
  useWizardEarly,
  useWizardMissing,
  useWizardNext,
  useWizardPrepared,
  useWizardPreview,
  useWizardProposal,
} from "@/lib/api/wizard-hooks";
import { cn } from "@/lib/utils";
import {
  answerFromFill,
  answersOf,
  back,
  buildAnswer,
  canContinue,
  dataTypeOf,
  decisionList,
  emptyMissingAnswer,
  fieldName,
  filterChoices,
  fillOf,
  initialDecisions,
  isSuggested,
  LONG_LIST,
  type MissingAnswer,
  needsConfirmation,
  OTHER,
  pendingConfirmations,
  rememberChoices,
  readinessBanner,
  readinessLabel,
  mergeFills,
  earlyShown,
  autoFills,
  progress,
  sectionChoices,
  STORAGE_KEY,
  takenTargets,
  verificationBanner,
  visibleChoices,
} from "@/lib/wizard/flow.mjs";
import {
  answerLabel,
  currencyCode,
  currentDecision,
  expertExamples,
  currentQuestion,
  openQuestions,
  mappingSummary,
  notImportedGroups,
  problemGroups,
  reviewGroups,
  rowsText,
  simpleExamples,
  withChoice,
} from "@/lib/wizard/review.mjs";

type ValueIssue = PreparedFile["value_issues"][number];
import { restoredState } from "@/lib/wizard/templates.mjs";

type Step = { question: WizardQuestion; answer: WizardAnswer };
/** Réponses données AVANT le fichier (questions prévisibles) ; absent : pas encore posées pour cette structure. */
type Early = { fills: Fill[]; shown: { field: string; label: string; value: string }[] };
type Saved = {
  history: Step[];
  current: WizardQuestion | null;
  remaining: number;
  proposal: TargetProposal | null;
  mode: WizardMode;
  early?: Early | null;
};

const EMPTY: Saved = { history: [], current: null, remaining: 5, proposal: null, mode: "simple" };
const STEPS = ["Your answers", "Your file", "Check the mapping", "Download"];
const YOUR_FILE = "wizard-your-file";
/** Section sans fond propre : celui de la page (bordure gardée pour la lisibilité). */
const SECTION = "bg-transparent shadow-none";
/** Cellules qui passent à la ligne : jamais de défilement horizontal. */
const WRAP = "whitespace-normal break-words align-top";

function load(): Saved | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? ({ ...EMPTY, ...(JSON.parse(raw) as Partial<Saved>) } as Saved) : null;
  } catch {
    return null;
  }
}

function save(state: Saved) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // stockage indisponible (navigation privée) : le parcours continue sans reprise
  }
}

export default function WizardPage() {
  return <PortalGate>{() => <WizardModes />}</PortalGate>;
}

const MODE_KEY = "wizard-mode";

/** Mode choisi dans cet onglet ; Simple par défaut. */
function storedMode(): WizardMode {
  try {
    return sessionStorage.getItem(MODE_KEY) === "expert" ? "expert" : "simple";
  } catch {
    return "simple";
  }
}

/** Simple (démarrage par le fichier) ou Expert (questionnaire inchangé) : le sélecteur bascule de l'un à l'autre. */
function WizardModes() {
  const [mode, setMode] = useState<WizardMode>(() => storedMode());
  const choose = useCallback((m: WizardMode) => {
    setMode(m);
    try {
      sessionStorage.setItem(MODE_KEY, m);
    } catch {
      // stockage indisponible : le mode vaut pour cette page seulement
    }
  }, []);
  if (mode === "simple") {
    return <SimpleWizard toggle={<ModeToggle mode="simple" onChange={choose} disabled={false} />} />;
  }
  return <Wizard onSimple={() => choose("simple")} />;
}

function Wizard({ onSimple }: { onSimple: () => void }) {
  const next = useWizardNext();
  const propose = useWizardProposal();
  // reprise du parcours (même onglet) : lu une fois au montage, côté client seulement (portail authentifié)
  const [initial] = useState<Saved>(() => ({ ...(load() ?? EMPTY), mode: "expert" }));
  const [state, setState] = useState<Saved>(initial);
  const [selected, setSelected] = useState("");
  const [otherText, setOtherText] = useState("");
  const [fileStep, setFileStep] = useState(1);
  // choix de correspondance retenus (en-tête → champ) : gardés quand la structure est régénérée
  const [remembered, setRemembered] = useState<Remembered>([]);
  const started = useRef(false);

  const update = useCallback((s: Saved) => {
    setState(s);
    save(s);
  }, []);

  const ask = useCallback(
    (history: Step[], mode: WizardMode) => {
      const answers = answersOf(history);
      next.mutate(
        { answers, mode },
        {
          onSuccess: (step) => {
            if (step.done || !step.question) {
              update({ history, current: null, remaining: 0, proposal: null, mode });
              propose.mutate(answers, {
                onSuccess: (proposal) => update({ history, current: null, remaining: 0, proposal, mode }),
              });
              return;
            }
            update({ history, current: step.question, remaining: step.remaining, proposal: null, mode });
            setSelected("");
            setOtherText("");
          },
        },
      );
    },
    [next, propose, update],
  );

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!initial.current && !initial.proposal) ask(initial.history, initial.mode);
  }, [ask, initial]);

  const busy = next.isPending || propose.isPending;
  const error = next.error ?? propose.error;
  const { history, current, proposal, mode } = state;
  const expert = mode === "expert";
  const answer = current ? buildAnswer(current, selected, otherText) : null;

  function onContinue() {
    if (!current || !answer) return;
    ask([...history, { question: current, answer }], mode);
  }

  function onBack() {
    const b = back(history);
    if (!b) return;
    next.reset();
    propose.reset();
    update({ history: b.history, current: b.current, remaining: state.remaining + 1, proposal: null, mode });
    setSelected(b.selected);
    setOtherText(b.otherText);
  }

  function onRestart() {
    next.reset();
    propose.reset();
    update({ ...EMPTY, mode });
    setRemembered([]);
    setSelected("");
    setOtherText("");
    ask([], mode);
  }

  function onRetry() {
    if (propose.isError) {
      const answers = answersOf(history);
      propose.mutate(answers, {
        onSuccess: (p) => update({ history, current: null, remaining: 0, proposal: p, mode }),
      });
      return;
    }
    ask(history, mode);
  }

  /** « Use » d'un modèle : ses réponses sont restaurées telles quelles, AUCUNE question n'est reposée (ni appel
   * du questionnaire au LLM) ; seule la structure cible est demandée (cache du serveur pour les mêmes réponses),
   * puis l'étape « Your file » s'ouvre. Fichier, correspondances et valeurs repartent de zéro. */
  function onUseTemplate(template: WizardTemplate) {
    next.reset();
    propose.reset();
    setRemembered([]);
    setSelected("");
    setOtherText("");
    const restored = restoredState(template.steps, mode);
    update(restored);
    propose.mutate(answersOf(restored.history), {
      onSuccess: (p) => {
        update({ ...restored, proposal: p });
        requestAnimationFrame(() => document.getElementById(YOUR_FILE)?.scrollIntoView({ behavior: "smooth" }));
      },
    });
  }

  /** Simple = démarrage par le fichier (autre écran) ; les réponses du questionnaire restent gardées ici. */
  function onMode(m: WizardMode) {
    if (m === "simple") {
      onSimple();
      return;
    }
    update({ ...state, mode: m });
  }

  const done = !current && (proposal !== null || propose.isPending);
  const percent = progress(history.length, state.remaining, done);
  const step = !proposal ? 0 : fileStep;

  return (
    <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
      <TemplateSidebar history={history} busy={busy} onNew={onRestart} onUse={onUseTemplate} />
      <div className="page-stack min-w-0 flex-1">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1.5">
            <h1 className="hero-badge">AI Migration Wizard</h1>
            <p className="page-description text-muted-foreground">
              {expert
                ? "Define the target import structure: configuration, fields, types and verification status."
                : "Answer a few questions, add your file and download it prepared for your target system."}{" "}
              Nothing is written to your systems.
            </p>
          </div>
          <ModeToggle mode={mode} onChange={onMode} disabled={busy} />
        </div>

        <div className="space-y-2" aria-label="Progress">
          <ol className="flex flex-wrap gap-x-6 gap-y-1">
            {STEPS.map((label, i) => (
              <li
                key={label}
                className={cn(
                  "meta flex items-center gap-2",
                  i === step ? "font-semibold text-brand" : i < step ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <span
                  className={cn(
                    "flex size-5 items-center justify-center rounded-full border text-[11px]",
                    i <= step ? "border-brand" : "border-border",
                  )}
                >
                  {i + 1}
                </span>
                {label}
              </li>
            ))}
          </ol>
          {!proposal && (
            <>
              <div className="meta flex justify-between text-muted-foreground">
                <span>{done ? "Preparing your target structure" : `Question ${history.length + 1}`}</span>
                <span>{percent} %</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-brand transition-all" style={{ width: `${percent}%` }} />
              </div>
            </>
          )}
        </div>

        {error && (
          <div className="space-y-3 rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">
            <p>{error instanceof Error ? error.message : String(error)}</p>
            <Button variant="outline" onClick={onRetry} disabled={busy}>
              Try again
            </Button>
          </div>
        )}

        {busy && (
          <Thinking label={propose.isPending ? "Preparing the target structure…" : "Preparing the next question…"} />
        )}

        {!busy && current && (
          <QuestionCard
            key={current.id}
            question={current}
            selected={selected}
            otherText={otherText}
            onSelect={setSelected}
            onOther={setOtherText}
            expert={expert}
          />
        )}

        {!busy && proposal && !expert && <SimpleSummary proposal={proposal} history={history} />}
        {!busy && proposal && expert && <ProposalView proposal={proposal} answers={answersOf(history)} />}
        {!busy && proposal && (
          <EarlySection
            key={`early-${proposal.title}`}
            proposal={proposal}
            answers={answersOf(history)}
            mode={mode}
            expert={expert}
            early={state.early ?? null}
            onDone={(early) => update({ ...state, early })}
          />
        )}
        {!busy && proposal && state.early && (
          <FileSection
            key={proposal.title}
            proposal={proposal}
            answers={answersOf(history)}
            expert={expert}
            baseFills={state.early.fills}
            onStep={setFileStep}
            previous={remembered}
            onRemember={setRemembered}
          />
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-2">
            <Button variant="outline" onClick={onBack} disabled={busy || history.length === 0}>
              Back
            </Button>
            <Button variant="ghost" onClick={onRestart} disabled={busy}>
              <RotateCcw className="size-4" /> Start over
            </Button>
          </div>
          {current && (
            <Button onClick={onContinue} disabled={busy || !answer}>
              Continue
            </Button>
          )}
        </div>

        {history.length > 0 && !proposal && <AnswersSoFar history={history} />}
      </div>
    </div>
  );
}

function ModeToggle({
  mode,
  onChange,
  disabled,
}: {
  mode: WizardMode;
  onChange: (m: WizardMode) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex rounded-lg border p-0.5" role="radiogroup" aria-label="Mode">
      {(["simple", "expert"] as const).map((m) => (
        <button
          key={m}
          type="button"
          role="radio"
          aria-checked={mode === m}
          disabled={disabled}
          onClick={() => onChange(m)}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            mode === m ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {m === "simple" ? "Simple" : "Expert"}
        </button>
      ))}
    </div>
  );
}

function SimpleSummary({ proposal, history }: { proposal: TargetProposal; history: Step[] }) {
  const verified = proposal.verification === "verified";
  return (
    <Card className={SECTION}>
      <CardHeader>
        <CardTitle>Your choices</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
          {history.map((s) => (
            <div key={s.answer.question_id} className="min-w-0">
              <dt className="meta text-muted-foreground">{s.question.title}</dt>
              <dd className="break-words">{s.answer.label}</dd>
            </div>
          ))}
        </dl>
        <p className={verified ? "text-success" : "text-warning"}>
          {verified
            ? `Your target structure (${proposal.fields.length} fields) comes from a verified schema.`
            : `Your target structure (${proposal.fields.length} fields) is a proposal, not an official import template.`}{" "}
          <span className="text-muted-foreground">Switch to Expert to see every field.</span>
        </p>
      </CardContent>
    </Card>
  );
}

type Remembered = { header: string; target: string | null }[];

function FileSection({
  proposal,
  answers,
  expert,
  onStep,
  previous,
  onRemember,
  baseFills,
}: {
  proposal: TargetProposal;
  answers: WizardAnswer[];
  expert: boolean;
  baseFills: Fill[];
  onStep: (step: number) => void;
  previous: Remembered;
  onRemember: (choices: Remembered) => void;
}) {
  const mapping = useWizardMapping();
  const preview = useWizardPreview();
  const prepared = useWizardPrepared();
  const missing = useWizardMissing();
  const [fills, setFills] = useState<Fill[]>([]);
  const [missingInfo, setMissingInfo] = useState<MissingInfo | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [applied, setApplied] = useState<{ field: string; label: string; value: string; explanation: string | null }[]>(
    [],
  );
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<FileMapping | null>(null);
  const [decisions, setDecisions] = useState<Record<number, string | null>>({});
  const [confirmed, setConfirmed] = useState<Set<number>>(new Set());
  // décisions sur les VALEURS (question du serveur → option) : liste, séparateur, ordre des dates, unité, devise
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [ready, setReady] = useState<PreparedFile | null>(null);
  const fields = proposal.fields.map((f) => ({ name: f.name, label: f.label }));

  useEffect(() => {
    onStep(ready ? 3 : result ? 2 : 1);
  }, [ready, result, onStep]);

  // chaque choix est retenu : une nouvelle structure (réponses changées) le réapplique au même en-tête
  useEffect(() => {
    if (result) onRemember(rememberChoices(result.columns, decisions, confirmed));
  }, [result, decisions, confirmed, onRemember]);

  function analyze() {
    if (!file) return;
    preview.reset();
    setReady(null);
    resetCompletion();
    mapping.mutate(
      { file, answers, proposal, previous },
      {
        onSuccess: (m) => {
          setResult(m);
          setDecisions(initialDecisions(m.columns));
          setConfirmed(new Set());
          setChoices({});
        },
      },
    );
  }

  function choose(position: number, target: string | null) {
    resetCompletion();
    setDecisions((d) => ({ ...d, [position]: target }));
    setConfirmed((c) => new Set(c).add(position));
    setReady(null);
  }

  function confirm(position: number) {
    setConfirmed((c) => new Set(c).add(position));
    setReady(null);
  }

  const pending = result ? pendingConfirmations(result.columns, confirmed) : [];
  const mode: WizardMode = expert ? "expert" : "simple";
  // réponses données avant le fichier + celles d'après (une par champ, la plus récente gagne)
  const allFills = mergeFills(baseFills, fills);
  const input = file
    ? { file, answers, proposal, decisions: decisionList(decisions), fills: allFills, mode, choices }
    : null;

  /** Une décision sur les valeurs : le serveur refait l'aperçu (questions, transformations, contrôles). */
  function answer(key: string, value: string | null) {
    if (!input) return;
    const next = withChoice(choices, key, value);
    setChoices(next);
    preview.mutate({ ...input, choices: next }, { onSuccess: setReady });
  }
  const openValues = ready ? openQuestions(ready.value_questions ?? []) : [];

  /** Les correspondances changent : les réponses de « Complete missing information » sont à refaire. */
  function resetCompletion() {
    setFills([]);
    setApplied([]);
    setMissingInfo(null);
    missing.reset();
  }

  /** Ouvre l'étape ; `enter` : directement sur ce champ, en saisie de la valeur exacte. */
  function completeMissing(enter?: string) {
    if (!input) return;
    setFocus(enter ?? null);
    missing.mutate(input, { onSuccess: setMissingInfo });
  }

  /** Premier aperçu : les champs qui se DÉDUISENT des réponses (valeur vérifiée) sont remplis d'office. */
  function onPrepared(first: PreparedFile, used = input) {
    setReady(first);
    if (!used || first.missing_required.length === 0) return;
    missing.mutate(used, {
      onSuccess: (info) => {
        const auto = autoFills(info.questions);
        if (!auto.fills.length) return;
        setApplied(auto.applied);
        applyFills([...used.fills.filter((f) => !auto.fills.some((a) => a.field === f.field)), ...auto.fills], used);
      },
    });
  }

  function applyFills(next: Fill[], used = input) {
    if (!used) return;
    setFills(next);
    setMissingInfo(null);
    preview.mutate({ ...used, fills: next }, { onSuccess: setReady });
  }

  /** « Which column of your file contains … ? » : la colonne choisie devient la correspondance du champ (comme
   * dans « Check the mapping ») et le fichier est préparé de nouveau ; jamais une valeur commune. */
  function mapColumn(position: number, field: string) {
    if (!input) return;
    const next = { ...decisions, [position]: field };
    setDecisions(next);
    setConfirmed((c) => new Set(c).add(position));
    resetCompletion();
    const used = { ...input, decisions: decisionList(next), fills: [] };
    preview.mutate(used, { onSuccess: (first) => onPrepared(first, used) });
  }

  return (
    <div className="space-y-5">
      <Card className={SECTION} id={YOUR_FILE}>
        <CardHeader>
          <CardTitle>Your file</CardTitle>
          <p className="text-muted-foreground">
            Add the Excel or CSV file with your data. It is only read to prepare it for your target system: it is not
            stored and nothing is written to your systems.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <FilePicker
            file={file}
            onPick={(f) => {
              setFile(f);
              setResult(null);
              setReady(null);
            }}
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={analyze} disabled={!file || mapping.isPending}>
              {mapping.isPending ? <Loader2 className="size-4 animate-spin" /> : <FileUp className="size-4" />}
              Analyze my file
            </Button>
            {mapping.error && <p className="text-danger">{mapping.error.message}</p>}
          </div>
        </CardContent>
      </Card>

      {result && (
        <MappingReview
          result={result}
          fields={fields}
          decisions={decisions}
          confirmed={confirmed}
          expert={expert}
          dataType={dataTypeOf(answers, "Rows")}
          busy={mapping.isPending}
          onChoose={choose}
          onConfirm={confirm}
          onRetry={analyze}
        >
          <div className="flex flex-wrap items-center gap-3 pt-2">
            <Button
              onClick={() => input && preview.mutate(input, { onSuccess: (first) => onPrepared(first) })}
              disabled={!input || pending.length > 0 || preview.isPending}
            >
              {preview.isPending && <Loader2 className="size-4 animate-spin" />}
              Prepare my file
            </Button>
            {pending.length > 0 && (
              <span className="text-muted-foreground">
                {pending.length} decision{pending.length > 1 ? "s" : ""} still open above.
              </span>
            )}
            {preview.error && <span className="text-danger">{preview.error.message}</span>}
          </div>
        </MappingReview>
      )}

      {ready && !missingInfo && (ready.value_questions ?? []).length > 0 && (
        <ValueDecisions
          questions={ready.value_questions ?? []}
          expert={expert}
          busy={preview.isPending}
          onAnswer={answer}
        />
      )}

      {/* Simple : seulement les décisions nécessaires, le fichier préparé une fois qu'elles sont prises */}
      {ready && input && !missingInfo && (expert || openValues.length === 0) && (
        <PreparedView
          ready={ready}
          answers={answers}
          proposal={proposal}
          expert={expert}
          downloading={prepared.isPending}
          error={prepared.error ?? missing.error}
          completing={missing.isPending}
          applied={applied}
          onComplete={() => completeMissing()}
          onEnter={(field) => completeMissing(field)}
          onDownload={() => prepared.mutate(input)}
        />
      )}

      {missingInfo && (
        <MissingStep
          info={missingInfo}
          expert={expert}
          previous={fills}
          focus={focus}
          onCancel={() => setMissingInfo(null)}
          onDone={(next) => applyFills(next)}
          onMapColumn={mapColumn}
        />
      )}
    </div>
  );
}

/** « Your answers » : questions PRÉVISIBLES posées avant le fichier (valeurs admises, unités, catégories…), avec
 * les mêmes cartes que « Complete missing information ». Les champs déduits des réponses, calculés par la cible ou
 * propres à chaque ligne ne sont pas demandés ici (serveur). Réponses validées par le serveur, puis appliquées au
 * fichier là où il n'a pas de valeur ; « Change » les rouvre sans perdre le fichier. */
function EarlySection({
  proposal,
  answers,
  mode,
  expert,
  early,
  onDone,
}: {
  proposal: TargetProposal;
  answers: WizardAnswer[];
  mode: WizardMode;
  expert: boolean;
  early: Early | null;
  onDone: (early: Early) => void;
}) {
  const ask = useWizardEarly();
  const check = useWizardEarly();
  const [info, setInfo] = useState<MissingInfo | null>(null);
  const [editing, setEditing] = useState(early === null);
  const asked = useRef(false);

  const open = useCallback(() => {
    ask.mutate(
      { answers, proposal, mode },
      {
        onSuccess: (found) => {
          if (found.questions.length === 0) {
            setEditing(false);
            onDone({ fills: [], shown: [] }); // rien de prévisible à demander : directement le fichier
          } else setInfo(found);
        },
      },
    );
  }, [ask, answers, proposal, mode, onDone]);

  useEffect(() => {
    if (!editing || asked.current) return;
    asked.current = true;
    open();
  }, [editing, open]);

  function finish(fills: Fill[]) {
    if (!info) return;
    const questions = info.questions;
    check.mutate(
      { answers, proposal, mode, fills },
      {
        onSuccess: () => {
          setEditing(false);
          setInfo(null);
          asked.current = false;
          onDone({ fills, shown: earlyShown(questions, fills) });
        },
      },
    );
  }

  function close() {
    setEditing(false);
    setInfo(null);
    asked.current = false;
    onDone(early ?? { fills: [], shown: [] });
  }

  if (!editing) {
    if (!early?.shown.length) return null;
    return (
      <Card className={SECTION}>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
          <CardTitle className="item-title">Before your file</CardTitle>
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            Change
          </Button>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1">
            {early.shown.map((x) => (
              <li key={x.field}>
                {x.label}: <span className="font-medium">{x.value}</span>
              </li>
            ))}
          </ul>
          <p className="meta mt-2 text-muted-foreground">Used for every row where your file gives no value.</p>
        </CardContent>
      </Card>
    );
  }
  const error = ask.error ?? check.error;
  return (
    <div className="space-y-3">
      {ask.isPending && <Thinking label="Preparing a few questions before your file…" />}
      {error && (
        <div className="space-y-3 rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">
          <p>{error.message}</p>
          {ask.error && (
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={open}>
                Try again
              </Button>
              <Button variant="ghost" onClick={close}>
                Continue without these answers
              </Button>
            </div>
          )}
        </div>
      )}
      {info && (
        <MissingStep
          info={info}
          expert={expert}
          previous={early?.fills ?? []}
          focus={null}
          eyebrow="Before your file"
          doneLabel="Continue to my file"
          busy={check.isPending}
          onCancel={close}
          onDone={finish}
          onMapColumn={() => undefined}
        />
      )}
    </div>
  );
}

type Column = FileMapping["columns"][number];
type FieldRef = { name: string; label: string };

/** « Check the mapping ». SIMPLE : résumé, puis seulement les décisions nécessaires (correspondance incertaine),
 * une par une, en langage métier ; correspondances trouvées et colonnes non importées repliées, toujours
 * corrigeables, jamais cachées en silence. EXPERT : toutes les colonnes. Les décisions vivent dans FileSection :
 * passer de Simple à Expert ne perd aucun choix. Les décisions sur les VALEURS viennent du serveur
 * (`ValueDecisions`). Logique pure : `lib/wizard/review.mjs`. */
function MappingReview({
  result,
  fields,
  decisions,
  confirmed,
  expert,
  dataType,
  busy,
  onChoose,
  onConfirm,
  onRetry,
  children,
}: {
  result: FileMapping;
  fields: FieldRef[];
  decisions: Record<number, string | null>;
  confirmed: ReadonlySet<number>;
  expert: boolean;
  dataType: string;
  busy: boolean;
  onChoose: (position: number, target: string | null) => void;
  onConfirm: (position: number) => void;
  onRetry: () => void;
  children: ReactNode;
}) {
  const groups = reviewGroups(result.columns, decisions, confirmed);
  const summary = mappingSummary(result.rows, groups, decisions);
  const decision = currentDecision(result.columns, confirmed);
  const current = decision.column;
  const row = (c: Column) => (
    <MappingRow
      key={c.position}
      column={c}
      fields={fields}
      value={decisions[c.position] ?? null}
      taken={takenTargets(decisions, c.position)}
      expert={expert}
      open={c.status === "to_confirm" && !confirmed.has(c.position)}
      onChoose={(target) => onChoose(c.position, target)}
      onConfirm={() => onConfirm(c.position)}
    />
  );
  const settled = [...groups.mapped, ...groups.notImported].sort((a, b) => a.position - b.position);
  return (
    <Card className={SECTION}>
      <CardHeader>
        <CardTitle>Check the mapping</CardTitle>
        <dl className="flex flex-wrap gap-x-8 gap-y-1">
          <Count label={`${dataType} analyzed`} value={summary.rows} />
          <Count label="Matched automatically" value={summary.automatic} />
          {summary.chosen > 0 && <Count label="Chosen by you" value={summary.chosen} />}
          <Count label="To confirm" value={summary.toConfirm} />
          <Count label="Not imported" value={summary.notImported} />
        </dl>
      </CardHeader>
      <CardContent className="space-y-4">
        {result.ai_unavailable && (
          <div className="space-y-2 rounded-md border border-warning/35 bg-warning/10 p-3 text-warning">
            <p className="font-semibold">The AI could not analyze your columns this time</p>
            <p>
              Only columns with the same name as a field were matched: the others are not “not recognized”, they were
              not analyzed. Nothing was decided for them.
            </p>
            <Button size="sm" variant="outline" onClick={onRetry} disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              Analyze again
            </Button>
          </div>
        )}
        {expert ? (
          <div className="space-y-2">{[...groups.pending, ...settled].map(row)}</div>
        ) : current ? (
          <DecisionCard
            key={current.position}
            decision={decision}
            column={current}
            fields={fields}
            taken={takenTargets(decisions, current.position)}
            onChoose={(target) => onChoose(current.position, target)}
            onConfirm={() => onConfirm(current.position)}
          />
        ) : (
          <p className="text-muted-foreground">
            No decision needed: every column with values is matched or listed below.
          </p>
        )}

        {!expert && groups.mapped.length > 0 && (
          <Fold title={`Check automatic matches (${groups.mapped.length})`}>{groups.mapped.map(row)}</Fold>
        )}

        {!expert && groups.notImported.length > 0 && (
          <div className="space-y-2">
            <p className="text-muted-foreground">
              {groups.notImported.length} column{groups.notImported.length > 1 ? "s" : ""} with values will not be
              imported. You can still choose a field for any of them.
            </p>
            <Fold title="Show details">
              {notImportedGroups(groups.notImported).map((g) => (
                <div key={g.reason} className="space-y-2">
                  <p className="eyebrow text-muted-foreground">
                    {g.title} ({g.columns.length})
                  </p>
                  {g.columns.map(row)}
                </div>
              ))}
            </Fold>
          </div>
        )}

        {groups.empty.length > 0 && (
          <p className="meta break-words text-muted-foreground">
            Empty in your file, nothing to import: {groups.empty.map((c) => c.header).join(", ")}.
          </p>
        )}
        {children}
      </CardContent>
    </Card>
  );
}

/** Une décision nécessaire (Simple) : la colonne, quelques exemples courts, la proposition en langage métier. */
function DecisionCard({
  decision,
  column,
  fields,
  taken,
  onChoose,
  onConfirm,
}: {
  decision: { index: number; total: number };
  column: Column;
  fields: FieldRef[];
  taken: Set<string>;
  onChoose: (target: string | null) => void;
  onConfirm: () => void;
}) {
  const [other, setOther] = useState(false);
  const proposed = fields.find((f) => f.name === column.target);
  const examples = simpleExamples(column.examples);
  return (
    <div className="space-y-3 rounded-lg border border-warning/50 bg-warning/5 p-4">
      <p className="eyebrow text-muted-foreground">
        Decision {decision.index} of {decision.total}
      </p>
      <div>
        <p className="item-title break-words">Your column “{column.header}”</p>
        {examples.length > 0 && (
          <p className="meta break-words text-muted-foreground">For example: {examples.join(" · ")}</p>
        )}
      </div>
      <p>
        {proposed ? (
          <>
            Does it contain the <span className="font-medium">{proposed.label}</span>? We think so, but we are not sure.
          </>
        ) : (
          "Which information does it contain?"
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        {proposed && (
          <Button size="sm" onClick={onConfirm}>
            Yes, use {proposed.label}
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => setOther(true)}>
          No, choose another field
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onChoose(null)}>
          Don&apos;t import this column
        </Button>
      </div>
      {other && (
        <TargetSelect
          header={column.header}
          fields={fields}
          value={null}
          taken={taken}
          expert={false}
          onChoose={onChoose}
        />
      )}
    </div>
  );
}

/** Décisions sur les VALEURS, posées par le moteur du serveur : une par colonne et par sorte (liste, séparateur
 * décimal, ordre des dates, unité) et une pour le fichier (devise). SIMPLE : une à la fois, réponses données
 * modifiables ; EXPERT : toutes, avec l'aperçu avant → après de chaque option. Une suggestion (règle ou IA)
 * n'est jamais appliquée seule. */
function ValueDecisions({
  questions,
  expert,
  busy,
  onAnswer,
}: {
  questions: ValueQuestion[];
  expert: boolean;
  busy: boolean;
  onAnswer: (key: string, value: string | null) => void;
}) {
  const step = currentQuestion(questions);
  const answered = questions.filter((q) => q.answer);
  return (
    <Card className={SECTION}>
      <CardHeader>
        <CardTitle>Decisions about your values</CardTitle>
        <p className="text-muted-foreground">
          One decision per column, never per row. Nothing is converted or invented: values that need a decision are not
          written until you choose.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {expert ? (
          questions.map((q) => <ValueQuestionCard key={q.key} question={q} expert busy={busy} onAnswer={onAnswer} />)
        ) : step.question ? (
          <ValueQuestionCard
            key={step.question.key}
            question={step.question}
            decision={step}
            expert={false}
            busy={busy}
            onAnswer={onAnswer}
          />
        ) : (
          <p className="text-muted-foreground">Every decision about your values is made.</p>
        )}
        {!expert && answered.length > 0 && (
          <ul className="meta space-y-1 text-muted-foreground">
            {answered.map((q) => (
              <li key={q.key} className="break-words">
                {q.title}: <span className="font-medium text-foreground">{answerLabel(q)}</span>{" "}
                <button
                  type="button"
                  className="text-brand hover:underline"
                  disabled={busy}
                  onClick={() => onAnswer(q.key, null)}
                >
                  Change
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ValueQuestionCard({
  question: q,
  decision,
  expert,
  busy,
  onAnswer,
}: {
  question: ValueQuestion;
  decision?: { index: number; total: number };
  expert: boolean;
  busy: boolean;
  onAnswer: (key: string, value: string | null) => void;
}) {
  const [other, setOther] = useState("");
  const typed = currencyCode(other);
  const suggested = q.options.find((o) => o.value === q.suggested);
  return (
    <div className={cn("space-y-3 rounded-lg border p-4", !q.answer && "border-warning/50 bg-warning/5")}>
      {decision && (
        <p className="eyebrow text-muted-foreground">
          Decision {decision.index} of {decision.total}
        </p>
      )}
      <div>
        <p className="item-title break-words">{q.title}</p>
        <p className="break-words text-muted-foreground">{q.help}</p>
        {expert && q.field && (
          <p className="meta tech text-muted-foreground">
            {q.column} → {q.field.name} · {q.count} value{q.count > 1 ? "s" : ""}
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {q.options.map((o) => (
          <Button
            key={o.value}
            size="sm"
            variant={q.answer === o.value ? "default" : "outline"}
            aria-pressed={q.answer === o.value}
            disabled={busy}
            onClick={() => onAnswer(q.key, o.value)}
          >
            {o.label}
          </Button>
        ))}
      </div>
      {(expert ? q.options : q.options.slice(0, 2)).some((o) => (o.examples ?? []).length > 0) && (
        <dl className="meta grid gap-x-4 gap-y-1 text-muted-foreground sm:grid-cols-[auto_minmax(0,1fr)]">
          {q.options
            .filter((o) => (o.examples ?? []).length > 0)
            .slice(0, expert ? undefined : 2)
            .map((o) => (
              <div key={o.value} className="contents">
                <dt className="font-medium">{o.label}</dt>
                <dd className="break-words">
                  {(o.examples ?? [])
                    .slice(0, expert ? 3 : 1)
                    .map(([before, after]) => `${simpleExamples([before])[0] ?? before} → ${after}`)
                    .join(" · ")}
                </dd>
              </div>
            ))}
        </dl>
      )}
      {q.allow_other && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="input w-28"
            aria-label="Other currency code"
            placeholder="Other code"
            maxLength={3}
            value={other}
            onChange={(e) => setOther(e.target.value)}
          />
          <Button size="sm" variant="outline" disabled={!typed || busy} onClick={() => typed && onAnswer(q.key, typed)}>
            Use {typed ?? "this code"}
          </Button>
        </div>
      )}
      {!q.answer && suggested && (
        <p className="meta text-muted-foreground">
          Suggested{q.suggested_by === "ai" ? " by AI" : " from your file"}: {suggested.label}. Not applied until you
          choose it.
        </p>
      )}
    </div>
  );
}

/** Une colonne et son champ cible. Exemples : courts et sans longue valeur structurée en Simple, coupés à
 * 150 caractères avec « Show more » en Expert. */
function MappingRow({
  column,
  fields,
  value,
  taken,
  expert,
  open,
  onChoose,
  onConfirm,
}: {
  column: Column;
  fields: FieldRef[];
  value: string | null;
  taken: Set<string>;
  expert: boolean;
  open: boolean;
  onChoose: (target: string | null) => void;
  onConfirm: () => void;
}) {
  const [more, setMore] = useState(false);
  const shown = expert
    ? expertExamples(column.examples, more)
    : { examples: simpleExamples(column.examples), clipped: false };
  return (
    <div
      className={cn(
        "grid items-start gap-3 rounded-lg border px-3 py-2.5 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]",
        open && "border-warning/50 bg-warning/5",
      )}
    >
      <div className="min-w-0">
        <p className="font-medium break-words">{column.header}</p>
        {shown.examples.length > 0 && (
          <p className="meta break-words text-muted-foreground">{shown.examples.join(" · ")}</p>
        )}
        {shown.clipped && (
          <button type="button" className="meta text-brand hover:underline" onClick={() => setMore((m) => !m)}>
            {more ? "Show less" : "Show more"}
          </button>
        )}
        {open && (
          <Button size="sm" variant="outline" className="mt-2 flex" onClick={onConfirm}>
            Confirm
          </Button>
        )}
      </div>
      <TargetSelect
        header={column.header}
        fields={fields}
        value={value}
        taken={taken}
        expert={expert}
        onChoose={onChoose}
      />
    </div>
  );
}

function TargetSelect({
  header,
  fields,
  value,
  taken,
  expert,
  onChoose,
}: {
  header: string;
  fields: FieldRef[];
  value: string | null;
  taken: Set<string>;
  expert: boolean;
  onChoose: (target: string | null) => void;
}) {
  return (
    <select
      className="input w-full"
      aria-label={`Target for ${header}`}
      value={value ?? ""}
      onChange={(e) => onChoose(e.target.value || null)}
    >
      <option value="">Not imported</option>
      {fields.map((f) => (
        <option key={f.name} value={f.name} disabled={taken.has(f.name)}>
          {fieldName(f, expert)}
        </option>
      ))}
    </select>
  );
}

/** Section repliée (fermée par défaut). */
function Fold({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="rounded-lg border px-3 py-2">
      <summary className="cursor-pointer font-medium">{title}</summary>
      <div className="mt-3 space-y-2">{children}</div>
    </details>
  );
}

/** Problèmes d'un groupe : ce qui ne va pas, les lignes du fichier du client concernées et une solution simple (mêmes
 * phrases en Simple ; colonne → champ technique en Expert). */
function ProblemList({ title, issues, expert }: { title: string; issues: ValueIssue[]; expert: boolean }) {
  if (!issues.length) return null;
  return (
    <div className="space-y-1">
      <p className="font-semibold text-warning">{title}</p>
      <ul className="list-disc space-y-2 pl-5">
        {issues.map((i) => (
          <li key={`${i.field.name}-${i.kind}`} className="break-words">
            {expert && i.column && (
              <span className="tech">
                {i.column} → {i.field.name}:{" "}
              </span>
            )}
            <span className="text-warning">{i.message}</span>
            {i.rows.length > 0 && <span className="meta block">{rowsText(i.rows, i.count)}</span>}
            {i.examples.length > 0 && (
              <span className="meta block text-muted-foreground">
                For example: {simpleExamples(i.examples).join(" · ")}
              </span>
            )}
            {i.fix && (
              <span className="block">
                <span className="font-medium">How to fix: </span>
                {i.fix}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Contrôle des valeurs du fichier préparé : valeurs NON écrites (comptées, avec exemples), lignes non importées
 * (autre devise, jamais converties), valeurs dont seule la forme a été corrigée. Rien n'est caché. */
function ValueChecks({ ready, expert }: { ready: PreparedFile; expert: boolean }) {
  const issues = ready.value_issues ?? [];
  const transforms = ready.value_transforms ?? [];
  const excluded = ready.excluded_count ?? 0;
  const { notWritten, toCheck } = problemGroups(issues);
  if (!issues.length && !transforms.length && !excluded) return null;
  return (
    <div className="space-y-2">
      <ProblemList title="Values not written" issues={notWritten} expert={expert} />
      <ProblemList title="To check before import" issues={toCheck} expert={expert} />
      {excluded > 0 && (
        <p className="break-words text-warning">
          <span className="font-semibold">
            {excluded} row{excluded > 1 ? "s" : ""} not imported:{" "}
          </span>
          amounts in another currency than {ready.currency}, never converted (rows{" "}
          {ready.excluded_rows.map((e) => e.row).join(", ")}
          {excluded > ready.excluded_rows.length ? "…" : ""}). The full list is in the prepared file.
        </p>
      )}
      {transforms.length > 0 &&
        (expert ? (
          <Fold title={`Transformations (${transforms.reduce((n, t) => n + t.count, 0)} values)`}>
            <Table className="table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead>Column</TableHead>
                  <TableHead>Transformation</TableHead>
                  <TableHead>Before → after</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {transforms.map((t) => (
                  <TableRow key={`${t.field.name}-${t.kind}`}>
                    <TableCell className="break-words whitespace-normal">
                      <span className="tech">{t.column}</span> → {t.field.label}
                    </TableCell>
                    <TableCell className="break-words whitespace-normal">
                      {t.label} ({t.count})
                    </TableCell>
                    <TableCell className="meta break-words whitespace-normal">
                      {t.examples.map(([before, after]) => `${before} → ${after}`).join(" · ")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Fold>
        ) : (
          <p className="meta text-muted-foreground">
            {transforms.filter((t) => !t.kind.startsWith("convert:")).reduce((n, t) => n + t.count, 0)} values written
            in a standard form without changing them (quotes, number and date formats).
            {(ready.converted_count ?? 0) > 0 &&
              ` ${ready.converted_count} values converted to the unit you chose, with exact factors (your values are kept in the prepared file, sheet “Converted values”).`}{" "}
            Details in Expert mode.
          </p>
        ))}
    </div>
  );
}

/** Choix du fichier : zone de dépôt (comme Imports), puis le fichier choisi affiché À PART (jamais dans le champ
 * natif du navigateur, dont le texte dépend de sa langue). */
function FilePicker({ file, onPick }: { file: File | null; onPick: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const pick = (f: File | undefined) => f && onPick(f);
  return (
    <div className="space-y-3">
      {file ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-4">
          <FileSpreadsheet className="size-8 shrink-0 text-brand" />
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium">{file.name}</div>
            <div className="meta">{formatSize(file.size)}</div>
          </div>
          <Button variant="outline" size="sm" onClick={() => input.current?.click()}>
            Change file
          </Button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            pick(e.dataTransfer.files[0]);
          }}
          className={cn(
            "flex w-full flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center transition-colors",
            dragging ? "border-brand bg-brand/5" : "border-border hover:border-brand/60",
          )}
        >
          <Upload className="size-6 text-muted-foreground" />
          <span className="font-medium">Choose a file or drop it here</span>
          <span className="meta">.xlsx or .csv</span>
        </button>
      )}
      <input
        ref={input}
        type="file"
        accept=".xlsx,.csv"
        className="hidden"
        onChange={(e) => {
          pick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="meta text-muted-foreground">{label}</dt>
      <dd className="item-title">{value}</dd>
    </div>
  );
}

function PreparedView({
  ready,
  answers,
  proposal,
  expert,
  downloading,
  error,
  completing,
  applied,
  onComplete,
  onEnter,
  onDownload,
}: {
  ready: PreparedFile;
  answers: WizardAnswer[];
  proposal: TargetProposal;
  expert: boolean;
  downloading: boolean;
  error: Error | null;
  completing: boolean;
  applied: { field: string; label: string; value: string; explanation: string | null }[];
  onComplete: () => void;
  onEnter: (field: string) => void;
  onDownload: () => void;
}) {
  const banner = readinessBanner(ready.status, ready.missing_required.length);
  const badge = readinessLabel(ready.status, ready.missing_required.length);
  const filledFromAnswers = [
    ...ready.auto_filled,
    ...applied.filter((x) => !ready.auto_filled.some((a) => a.field === x.field)),
  ];
  const target = answers.find((a) => a.topic === "target_system")?.label ?? "";
  return (
    <Card className={SECTION}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3">
          Your prepared file <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          className={cn(
            "rounded-md border p-3 font-semibold",
            banner.tone === "success"
              ? "border-success/35 bg-success/10 text-success"
              : "border-warning/35 bg-warning/10 text-warning",
          )}
        >
          {banner.title}
        </div>
        {ready.checks.length > 0 && (
          <ul className="space-y-1">
            {ready.checks.map((c) => (
              <li key={c.label} className="flex items-center gap-2">
                <StatusBadge tone={c.ok ? "success" : "neutral"}>{c.ok ? "Done" : "Not done"}</StatusBadge>
                {c.label}
              </li>
            ))}
          </ul>
        )}
        {filledFromAnswers.length > 0 && (
          <div className="space-y-1 rounded-md border border-info/35 bg-info/10 p-3 text-info">
            <p className="font-semibold">Filled from your answers</p>
            <ul className="space-y-1">
              {filledFromAnswers.map((x) => (
                <li key={x.field}>
                  {x.label}: <span className="font-medium">{x.value}</span>
                  {x.explanation ? ` — ${x.explanation}` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
        {(ready.set_by_target ?? []).length > 0 && (
          <div className="space-y-1 rounded-md border border-info/35 bg-info/10 p-3 text-info">
            <p className="font-semibold">Set by {target || "your target system"}</p>
            <ul className="space-y-1">
              {(ready.set_by_target ?? []).map((x) => (
                <li key={x.field.name}>
                  {x.field.label} — calculated from {x.source}, nothing to enter.
                </li>
              ))}
            </ul>
          </div>
        )}
        <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
          <Fact label="Target system" value={target} />
          <Fact label="Type of data" value={ready.data_type || dataTypeOf(answers, proposal.title)} />
          <Fact label="Rows analyzed" value={String(ready.rows)} />
          <Fact label="Matches found" value={`${ready.mapped} of ${ready.fields.length} target fields`} />
        </dl>
        {ready.missing_required.length > 0 && (
          <div className="space-y-2">
            <p className="font-semibold text-warning">To complete</p>
            <ul className="space-y-2">
              {ready.missing_required.map((f) => (
                <li key={f.name} className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="font-medium">{fieldName(f, expert)}</span>
                  <ResolveActions onEnter={() => onEnter(f.name)} disabled={completing} />
                </li>
              ))}
            </ul>
            <Button variant="outline" onClick={onComplete} disabled={completing}>
              {completing && <Loader2 className="size-4 animate-spin" />}
              Complete missing information
            </Button>
          </div>
        )}
        {ready.completed.length > 0 && (
          <p>
            <span className="font-semibold">Completed by you: </span>
            {ready.completed.map((f) => fieldName(f, expert)).join(", ")}.
          </p>
        )}
        {ready.not_imported.length > 0 && (
          <p className="text-warning">
            <span className="font-semibold">Not imported from your file: </span>
            {ready.not_imported.join(", ")}. These columns have values but go to no field: choose a field in the mapping
            above if they should be imported.
          </p>
        )}
        {ready.warnings.length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-warning">
            {ready.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
        <ValueChecks ready={ready} expert={expert} />
        <div className="space-y-2">
          <p className="eyebrow text-muted-foreground">Preview (first {ready.preview.length} rows)</p>
          <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
            {ready.preview.map((row, i) => (
              <Card key={i} size="sm" className={SECTION}>
                <CardContent>
                  <dl className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-x-4 gap-y-1.5">
                    {ready.fields.map((f, j) =>
                      row[j] ? (
                        <div key={f.name} className="contents">
                          <dt className="meta break-words text-muted-foreground">{fieldName(f, expert)}</dt>
                          <dd className="break-words">{row[j]}</dd>
                        </div>
                      ) : null,
                    )}
                  </dl>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
        <p className="meta text-muted-foreground">
          Values are copied from your file as they are, without conversion; completed values are added only where your
          file has none.
        </p>
        <Button onClick={onDownload} disabled={downloading}>
          {downloading ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
          Download prepared file
        </Button>
        {error && <p className="text-danger">{error.message}</p>}
      </CardContent>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="meta text-muted-foreground">{label}</dt>
      <dd className="break-words">{value}</dd>
    </div>
  );
}

function Thinking({ label }: { label: string }) {
  return (
    <Card className={SECTION}>
      <CardContent className="flex items-center gap-3 py-6 text-muted-foreground">
        <Loader2 className="size-5 animate-spin" />
        {label}
      </CardContent>
    </Card>
  );
}

function Choice({
  name,
  value,
  checked,
  onSelect,
  children,
}: {
  name: string;
  value: string;
  checked: boolean;
  onSelect: (v: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors",
        checked ? "border-brand bg-brand/5" : "hover:bg-muted/40",
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        onChange={() => onSelect(value)}
        className="mt-1 accent-[var(--brand)]"
      />
      <span className="min-w-0 flex-1 space-y-0.5">{children}</span>
    </label>
  );
}

function ChoiceText({ choice }: { choice: WizardQuestion["choices"][number] }) {
  return (
    <>
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium">{choice.label}</span>
      </span>
      {(choice.description || choice.group) && (
        <span className="meta block text-muted-foreground">
          {[choice.group, choice.description].filter(Boolean).join(" · ")}
        </span>
      )}
    </>
  );
}

function QuestionCard({
  question,
  selected,
  otherText,
  onSelect,
  onOther,
  expert,
}: {
  question: WizardQuestion;
  selected: string;
  otherText: string;
  onSelect: (v: string) => void;
  onOther: (v: string) => void;
  expert: boolean;
}) {
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const long = question.choices.length > LONG_LIST;
  const filtered = long ? filterChoices(question.choices, query) : question.choices;
  const { visible, hidden } = long
    ? visibleChoices(filtered, { query, showAll, selected })
    : { visible: filtered, hidden: 0 };
  const sections = sectionChoices(visible);
  const otherLabel = question.other_label ?? "Other";

  return (
    <Card className={SECTION}>
      <CardHeader>
        <CardTitle className="item-title">{question.title}</CardTitle>
        {question.help && <p className="text-muted-foreground">{question.help}</p>}
      </CardHeader>
      <CardContent className="space-y-4">
        {long && (
          <label className="flex max-w-md flex-col gap-1.5">
            <span className="label">Search system</span>
            <input
              className="input"
              type="search"
              value={query}
              placeholder={`Search among ${question.choices.length}…`}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        )}
        <fieldset className="space-y-5" aria-label={question.title}>
          {sections.map((s) => (
            <div key={s.source} className="space-y-2">
              {expert && s.title && <p className="eyebrow text-muted-foreground">{s.title}</p>}
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {s.choices.map((c) => (
                  <Choice
                    key={c.value}
                    name={question.id}
                    value={c.value}
                    checked={selected === c.value}
                    onSelect={onSelect}
                  >
                    <ChoiceText choice={c} />
                  </Choice>
                ))}
              </div>
            </div>
          ))}
          {long && filtered.length === 0 && (
            <p className="text-muted-foreground">
              No system matches “{query}”. Choose “{otherLabel}” below.
            </p>
          )}
          {hidden > 0 && (
            <Button variant="outline" size="sm" onClick={() => setShowAll(true)}>
              Show all ({hidden} more)
            </Button>
          )}
          {question.allow_other && (
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              <Choice name={question.id} value={OTHER} checked={selected === OTHER} onSelect={onSelect}>
                <span className="block font-medium">{otherLabel}</span>
                {selected === OTHER ? (
                  <input
                    className="input mt-2 w-full"
                    value={otherText}
                    maxLength={300}
                    autoFocus
                    placeholder="Type its name"
                    onChange={(e) => onOther(e.target.value)}
                  />
                ) : (
                  <span className="meta block text-muted-foreground">Not in the list: type it yourself</span>
                )}
              </Choice>
            </div>
          )}
        </fieldset>
      </CardContent>
    </Card>
  );
}

function AnswersSoFar({ history }: { history: Step[] }) {
  return (
    <Card size="sm" className={SECTION}>
      <CardHeader>
        <CardTitle className="eyebrow">Your answers</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
          {history.map((s) => (
            <div key={s.answer.question_id} className="min-w-0">
              <dt className="meta text-muted-foreground">{s.question.title}</dt>
              <dd className="break-words">{s.answer.label}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function ProposalView({ proposal, answers }: { proposal: TargetProposal; answers: WizardAnswer[] }) {
  const excel = useWizardExcel();
  const banner = verificationBanner(proposal.verification);
  return (
    <div className="space-y-5">
      <div
        className={cn(
          "space-y-1 rounded-md border p-3",
          banner.tone === "success"
            ? "border-success/35 bg-success/10 text-success"
            : "border-warning/35 bg-warning/10 text-warning",
        )}
      >
        <p className="font-semibold">{banner.title}</p>
        {proposal.registry_reference && <p className="meta">Registry: {proposal.registry_reference}</p>}
      </div>

      <Card className={SECTION}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="size-4 text-brand" /> {proposal.title}
          </CardTitle>
          {proposal.summary && <p className="text-muted-foreground">{proposal.summary}</p>}
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            {proposal.entity && (
              <span className="flex flex-wrap items-center gap-2">
                <span className="meta text-muted-foreground">Target object</span>
                <span className="tech">{proposal.entity}</span>
              </span>
            )}
            {proposal.template_name && (
              <span>
                <span className="meta text-muted-foreground">Template </span>
                {proposal.template_name}
              </span>
            )}
          </div>
          <Button onClick={() => excel.mutate({ answers, proposal })} disabled={excel.isPending}>
            {excel.isPending ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
            Download sample Excel
          </Button>
          {excel.error && <p className="text-danger">{excel.error.message}</p>}
        </CardContent>
      </Card>

      <Card className={SECTION}>
        <CardHeader>
          <CardTitle>Target configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-8 gap-y-3 md:grid-cols-2 2xl:grid-cols-3">
            {proposal.configuration.map((c) => (
              <div key={`${c.label}-${c.value}`} className="min-w-0 space-y-0.5">
                <dt className="meta text-muted-foreground">{c.label}</dt>
                <dd className="flex flex-wrap items-center gap-x-2 gap-y-1 break-words">{c.value}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>

      <Card className={SECTION}>
        <CardHeader>
          <CardTitle>Target fields ({proposal.fields.length})</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[20%]">Field</TableHead>
                <TableHead className="w-[24%]">Label</TableHead>
                <TableHead className="w-[12%]">Type</TableHead>
                <TableHead className="w-[9%]">Required</TableHead>
                <TableHead>Example</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {proposal.fields.map((f) => (
                <TableRow key={f.name}>
                  <TableCell className={cn(WRAP, "tech")}>{f.name}</TableCell>
                  <TableCell className={WRAP} title={f.description ?? undefined}>
                    {f.label}
                  </TableCell>
                  <TableCell className={WRAP}>
                    {f.type}
                    {f.length ? ` (${f.length})` : ""}
                  </TableCell>
                  <TableCell className={WRAP}>{f.required ? "Yes" : "No"}</TableCell>
                  <TableCell className={cn(WRAP, "tech")}>{f.example}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {proposal.rows.length > 0 && (
        <Card className={SECTION}>
          <CardHeader>
            <CardTitle>Sample records (fictitious)</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {proposal.rows.map((row, i) => (
              <Card key={i} size="sm" className={SECTION}>
                <CardHeader>
                  <CardTitle className="eyebrow">Record {i + 1}</CardTitle>
                </CardHeader>
                <CardContent>
                  <dl className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-x-4 gap-y-1.5">
                    {proposal.fields.map((f, j) =>
                      row[j] ? (
                        <div key={f.name} className="contents">
                          <dt className="meta tech break-words text-muted-foreground">{f.name}</dt>
                          <dd className="break-words">{row[j]}</dd>
                        </div>
                      ) : null,
                    )}
                  </dl>
                </CardContent>
              </Card>
            ))}
          </CardContent>
        </Card>
      )}

      {proposal.notes.length > 0 && (
        <Card size="sm" className={SECTION}>
          <CardHeader>
            <CardTitle className="eyebrow">Before any import</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc space-y-1 pl-5">
              {proposal.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/** Champ de donnée propre à chaque ligne sans colonne : choisir la colonne du fichier qui le contient (devient la
 * correspondance du champ). Jamais une valeur unique pour toutes les lignes. */
function FileColumnPicker({
  columns,
  label,
  onUse,
}: {
  columns: NonNullable<MissingQuestion["file_columns"]>;
  label: string;
  onUse: (position: number) => void;
}) {
  const [position, setPosition] = useState<number | null>(null);
  const chosen = columns.find((c) => c.position === position);
  return (
    <div className="space-y-2">
      <select
        className="input w-full"
        aria-label={`Column for ${label}`}
        value={position ?? ""}
        onChange={(e) => setPosition(e.target.value === "" ? null : Number(e.target.value))}
      >
        <option value="">Choose a column of your file…</option>
        {columns.map((c) => (
          <option key={c.position} value={c.position}>
            {c.header}
          </option>
        ))}
      </select>
      {chosen && chosen.examples.length > 0 && (
        <p className="meta break-words text-muted-foreground">For example: {chosen.examples.join(" · ")}</p>
      )}
      <Button size="sm" disabled={position === null} onClick={() => position !== null && onUse(position)}>
        Use this column for {label}
      </Button>
    </div>
  );
}

function MissingStep({
  info,
  expert,
  previous,
  focus,
  onCancel,
  onDone,
  onMapColumn,
  eyebrow = "Complete missing information",
  doneLabel = "Update my file",
  busy = false,
}: {
  info: MissingInfo;
  expert: boolean;
  previous: Fill[];
  focus: string | null;
  onCancel: () => void;
  onDone: (fills: Fill[]) => void;
  onMapColumn: (position: number, field: string) => void;
  eyebrow?: string;
  doneLabel?: string;
  busy?: boolean;
}) {
  const [index, setIndex] = useState(() =>
    Math.max(
      0,
      info.questions.findIndex((x) => x.field === focus),
    ),
  );
  // réponses de départ : valeurs déduites des réponses précédentes (pré-sélectionnées, à confirmer)
  const [answers, setAnswers] = useState<Record<string, MissingAnswer>>(() =>
    Object.fromEntries(
      info.questions
        .map((x) => [x, previous.find((f) => f.field === x.field)] as const)
        .filter(([x, f]) => f || x.suggested || x.field === focus)
        .map(([x, f]) => [
          x.field,
          // « Enter exact value » : ce champ s'ouvre en saisie libre (valeur déjà saisie gardée)
          x.field === focus ? { ...answerFromFill(x, f), how: "other" as const } : answerFromFill(x, f),
        ]),
    ),
  );
  const q = info.questions[index];
  const sameAs = q.same_as ? answers[q.same_as] : undefined;
  const a = answers[q.field] ?? (sameAs ? { ...sameAs } : emptyMissingAnswer());
  const set = (patch: Partial<MissingAnswer>) => setAnswers((all) => ({ ...all, [q.field]: { ...a, ...patch } }));
  const last = index === info.questions.length - 1;

  function finish(all: Record<string, MissingAnswer>) {
    const kept = previous.filter((f) => !info.questions.some((x) => x.field === f.field));
    // réponse reprise d'un champ de la même sorte : enregistrée même si l'utilisateur n'y a rien changé
    const effective = (x: (typeof info.questions)[number]) => all[x.field] ?? (x.same_as ? all[x.same_as] : undefined);
    const filled = info.questions
      .map((x) => {
        const ans = effective(x);
        return ans ? fillOf(x, ans) : null;
      })
      .filter(Boolean) as Fill[];
    onDone([...kept, ...filled]);
  }

  function skip() {
    // « Skip » : réponse vide explicite (une réponse reprise d'un autre champ n'est pas appliquée)
    const rest = { ...answers, [q.field]: emptyMissingAnswer() };
    if (last) finish(rest);
    else {
      setAnswers(rest);
      setIndex(index + 1);
    }
  }

  return (
    <Card className={SECTION}>
      <CardHeader>
        <p className="meta text-muted-foreground">
          {eyebrow} · {index + 1} of {info.questions.length}
          {q.optional ? " · optional" : ""}
        </p>
        <CardTitle className="item-title">{q.title}</CardTitle>
        {q.help && <p className="text-muted-foreground">{q.help}</p>}
        {expert && <p className="meta tech text-muted-foreground">{q.field}</p>}
      </CardHeader>
      <CardContent className="space-y-4">
        {!answers[q.field] && sameAs && q.same_as && (
          <div className="rounded-md border border-info/35 bg-info/10 p-3 text-info">
            <p className="font-semibold">Same answer as before</p>
            <p>Your answer for {info.questions.find((x) => x.field === q.same_as)?.label} is used here too.</p>
          </div>
        )}
        {q.suggested && isSuggested(q, a) && (
          <div className="rounded-md border border-info/35 bg-info/10 p-3 text-info">
            <p className="font-semibold">From your previous answers</p>
            {q.explanation && <p>{q.explanation}</p>}
          </div>
        )}
        {(q.resolution || (q.kind === "reference" && !q.from_system)) && (
          <div className="space-y-2 rounded-md border border-warning/35 bg-warning/10 p-3 text-warning">
            <p className="font-semibold">To complete</p>
            {q.resolution && <p>{q.resolution}</p>}
            <ResolveActions onEnter={() => set({ how: "other", confirmed: false })} />
          </div>
        )}
        {(q.file_columns ?? []).length > 0 ? (
          <FileColumnPicker
            key={q.field}
            columns={q.file_columns ?? []}
            label={q.label}
            onUse={(position) => onMapColumn(position, q.field)}
          />
        ) : (
          <MissingChoices question={q} answer={a} rows={info.rows} expert={expert} onChange={set} />
        )}
        {needsConfirmation(q, a) && (
          <label className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/5 p-3 text-warning">
            <input
              type="checkbox"
              className="mt-1"
              checked={a.confirmed}
              onChange={(e) => set({ confirmed: e.target.checked })}
            />
            <span>
              {q.warning ?? "This value cannot be verified against the target format."} I confirm this value; the
              warning stays in the prepared file.
            </span>
          </label>
        )}
        {(a.how === "hint" || a.how === "unknown") && (
          <p className="text-warning">
            Nothing is written for this field: it stays flagged in your prepared file so you can add it before
            importing.
          </p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => (index === 0 ? onCancel() : setIndex(index - 1))}>
              Back
            </Button>
            <Button variant="ghost" onClick={skip}>
              Skip this field
            </Button>
          </div>
          <Button
            disabled={busy || !canContinue(q, a)}
            onClick={() => {
              const all = answers[q.field] ? answers : { ...answers, [q.field]: a }; // réponse affichée = réponse
              setAnswers(all);
              if (last) finish(all);
              else setIndex(index + 1);
            }}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            {last ? doneLabel : "Continue"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function MissingChoices({
  question,
  answer,
  rows,
  expert,
  onChange,
}: {
  question: MissingQuestion;
  answer: MissingAnswer;
  rows: MissingInfo["rows"];
  expert: boolean;
  onChange: (patch: Partial<MissingAnswer>) => void;
}) {
  const name = `missing-${question.field}`;
  const reference = question.kind === "reference";
  return (
    <fieldset className="space-y-2" aria-label={question.title}>
      {question.from_system && (
        <p className="meta text-muted-foreground">These values come from your connected system.</p>
      )}
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {question.choices.map((c) => (
          <Choice
            key={c.value}
            name={name}
            value={c.value}
            checked={answer.how === "choice" && answer.value === c.value}
            onSelect={(v) => onChange({ how: "choice", value: v, confirmed: false })}
          >
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{c.label}</span>
            </span>
            {c.description && <span className="meta block text-muted-foreground">{c.description}</span>}
            {expert && c.label !== c.value && <span className="meta tech block text-muted-foreground">{c.value}</span>}
          </Choice>
        ))}
        {question.hints.map((h) => {
          // réponse métier avec un nom standard de la cible : ce nom est proposé (à confirmer, non vérifié)
          const standard = question.hint_names?.[h];
          return (
            <Choice
              key={`hint-${h}`}
              name={name}
              value={`hint:${h}`}
              checked={
                standard
                  ? answer.how === "other" && answer.other === standard
                  : answer.how === "hint" && answer.value === h
              }
              onSelect={() =>
                onChange(
                  standard
                    ? { how: "other", other: standard, confirmed: true }
                    : { how: "hint", value: h, confirmed: false },
                )
              }
            >
              <span className="block font-medium">{h}</span>
              {standard && (
                <span className="meta block text-muted-foreground">Usual name in the target: “{standard}”</span>
              )}
            </Choice>
          );
        })}
        {question.free_text && (
          <Choice
            name={name}
            value="__other__"
            checked={answer.how === "other"}
            onSelect={() => onChange({ how: "other", confirmed: false })}
          >
            <span className="block font-medium">
              {reference
                ? "I know the exact value in my system"
                : question.choices.length
                  ? "Another value"
                  : "Enter the value"}
            </span>
            {answer.how === "other" && (
              <input
                className="input mt-2 w-full"
                value={answer.other}
                maxLength={300}
                autoFocus
                placeholder="Same value for every row"
                onChange={(e) => onChange({ other: e.target.value, confirmed: false })}
              />
            )}
          </Choice>
        )}
        {rows.length > 1 && !reference && (
          <Choice
            name={name}
            value="__per_row__"
            checked={answer.how === "per_row"}
            onSelect={() => onChange({ how: "per_row", confirmed: false })}
          >
            <span className="block font-medium">A different value for each row</span>
            <span className="meta block text-muted-foreground">{rows.length} rows in your file</span>
          </Choice>
        )}
        {rows.length > 1 && reference && question.from_system && (
          <Choice
            name={name}
            value="__per_row__"
            checked={answer.how === "per_row"}
            onSelect={() => onChange({ how: "per_row", confirmed: false })}
          >
            <span className="block font-medium">A different value for each row</span>
            <span className="meta block text-muted-foreground">{rows.length} rows in your file</span>
          </Choice>
        )}
        {question.allow_unknown && (
          <Choice
            name={name}
            value="__unknown__"
            checked={answer.how === "unknown"}
            onSelect={() => onChange({ how: "unknown", confirmed: false })}
          >
            <span className="block font-medium">I don&apos;t know</span>
            <span className="meta block text-muted-foreground">
              The field stays flagged; the help above says where to find it.
            </span>
          </Choice>
        )}
      </div>
      {answer.how === "per_row" && (
        <div className="grid gap-2 md:grid-cols-2">
          {rows.map((r) => (
            <label key={r.index} className="flex min-w-0 flex-col gap-1">
              <span className="meta break-words text-muted-foreground">{r.label}</span>
              {question.choices.length > 0 && !question.free_text ? (
                <select
                  className="input"
                  value={answer.perRow[r.index] ?? ""}
                  onChange={(e) =>
                    onChange({ perRow: { ...answer.perRow, [r.index]: e.target.value }, confirmed: false })
                  }
                >
                  <option value="">—</option>
                  {question.choices.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              ) : question.from_system ? (
                <select
                  className="input"
                  value={answer.perRow[r.index] ?? ""}
                  onChange={(e) =>
                    onChange({ perRow: { ...answer.perRow, [r.index]: e.target.value }, confirmed: false })
                  }
                >
                  <option value="">—</option>
                  {question.choices.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  className="input"
                  value={answer.perRow[r.index] ?? ""}
                  maxLength={300}
                  list={question.choices.length ? `${name}-list` : undefined}
                  onChange={(e) =>
                    onChange({ perRow: { ...answer.perRow, [r.index]: e.target.value }, confirmed: false })
                  }
                />
              )}
            </label>
          ))}
          {question.choices.length > 0 && (
            <datalist id={`${name}-list`}>
              {question.choices.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </datalist>
          )}
        </div>
      )}
    </fieldset>
  );
}

/** Les deux solutions d'un champ obligatoire non résolu : connecter le système (ses vraies valeurs seront lues)
 *  ou saisir la valeur exacte (vérifiée quand le système est connecté, sinon confirmée et signalée). */
function ResolveActions({ onEnter, disabled }: { onEnter: () => void; disabled?: boolean }) {
  return (
    <span className="flex flex-wrap items-center gap-3">
      <Button variant="outline" size="sm" onClick={onEnter} disabled={disabled}>
        Enter exact value
      </Button>
      <Link href="/portal/systems/connect" className={buttonVariants({ variant: "ghost", size: "sm" })}>
        Connect my system (optional)
      </Link>
    </span>
  );
}
