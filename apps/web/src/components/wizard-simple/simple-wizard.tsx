"use client";

/** AI Migration Wizard — mode Simple « fichier d'abord » (défaut) : 1. fichier et cible, 2. progression pendant
 *  l'analyse, 3. synthèse (résultat, décisions appliquées, au plus 3 questions présélectionnées, colonnes à
 *  confirmer, téléchargement). Un appel /quick-start, puis l'aperçu à chaque changement ; le fichier n'est jamais
 *  stocké. Logique pure : `lib/wizard/simple.mjs` ; textes : `lib/wizard/simple-labels.mjs`. Le point d'entrée
 *  (`lib/wizard/entries.mjs` : Wizard général, parcours propre à un système…) ne change que le titre,
 *  l'introduction et les cibles affichées ; le sélecteur Simple / Expert n'existe que si la page le passe. */
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import {
  type Fill,
  type PreparedFile,
  type QuickStart,
  useWizardPrepared,
  useWizardPreview,
  useWizardQuickStart,
  useWizardTargets,
} from "@/lib/api/wizard-hooks";
import { cn } from "@/lib/utils";
import { WIZARD_ENTRIES, type WizardEntry } from "@/lib/wizard/entries.mjs";
import { type Edits, initialEdits, requestOf } from "@/lib/wizard/simple.mjs";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

import { ProgressScreen } from "./progress-screen";
import { ResultScreen } from "./result-screen";
import { StartScreen } from "./start-screen";

type Screen = "start" | "working" | "result";

export function SimpleWizard({
  entry = WIZARD_ENTRIES.general,
  toggle,
}: {
  entry?: WizardEntry;
  toggle?: ReactNode;
}) {
  const [screen, setScreen] = useState<Screen>("start");
  const [file, setFile] = useState<File | null>(null);
  const [target, setTarget] = useState("");
  const [start, setStart] = useState<QuickStart | null>(null);
  const [edits, setEdits] = useState<Edits>({ answers: {}, confirmed: [], targets: {} });
  // décisions sur les valeurs posées par l'aperçu lui-même (rare) : clé → option
  const [extra, setExtra] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<PreparedFile | null>(null);
  const quick = useWizardQuickStart();
  const check = useWizardPreview();
  const prepared = useWizardPrepared();
  const lastKey = useRef("");
  const targets = useWizardTargets();
  const chosenTarget = targets.data?.find((t) => t.value === target);
  const targetLabel = chosenTarget?.label ?? "";

  const input = useCallback(
    (s: QuickStart, e: Edits, more: Record<string, string>, final = false) => {
      const body = requestOf(s, e, final);
      return {
        file: file as File,
        answers: s.answers,
        proposal: s.proposal,
        decisions: body.decisions,
        fills: body.fills as Fill[],
        choices: { ...body.choices, ...more },
        confirmed: body.confirmed,
        mode: "simple" as const,
      };
    },
    [file],
  );

  const refresh = useCallback(
    (s: QuickStart, e: Edits, more: Record<string, string>, then?: () => void) => {
      const request = input(s, e, more);
      lastKey.current = JSON.stringify([request.decisions, request.fills, request.choices]);
      check.mutate(request, {
        onSuccess: (p) => {
          setPreview(p);
          then?.();
        },
      });
    },
    [check, input],
  );

  function analyze(dataType?: string) {
    if (!file || !target) return;
    setScreen("working");
    setPreview(null);
    check.reset();
    quick.mutate(
      { file, target, dataType },
      {
        onSuccess: (s) => {
          const e = initialEdits(s);
          setStart(s);
          setEdits(e);
          setExtra({});
          refresh(s, e, {}, () => setScreen("result"));
        },
      },
    );
  }

  // synthèse : l'aperçu suit chaque changement (réponse, décision appliquée, colonne confirmée)
  useEffect(() => {
    if (screen !== "result" || !start) return;
    const request = input(start, edits, extra);
    const key = JSON.stringify([request.decisions, request.fills, request.choices]);
    if (key === lastKey.current) return;
    const timer = setTimeout(() => refresh(start, edits, extra), 300);
    return () => clearTimeout(timer);
  }, [screen, start, edits, extra, input, refresh]);

  function restart() {
    quick.reset();
    check.reset();
    prepared.reset();
    setStart(null);
    setPreview(null);
    setScreen("start");
  }

  const failed = quick.error ?? (screen === "working" ? check.error : null);
  const stage = quick.isPending ? "analyzing" : "checking";
  const steps = [L.stepStart, L.stepWorking, L.stepResult];
  const current = screen === "start" ? 0 : screen === "working" ? 1 : 2;

  return (
    <div className="page-stack">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="hero-badge">{entry.title}</h1>
          <p className="page-description text-muted-foreground">{entry.intro}</p>
        </div>
        {toggle}
      </div>

      <ol className="flex flex-wrap gap-x-6 gap-y-1" aria-label={L.progress}>
        {steps.map((label, i) => (
          <li
            key={label}
            className={cn(
              "meta flex items-center gap-2",
              i === current ? "font-semibold text-brand" : i < current ? "text-foreground" : "text-muted-foreground",
            )}
          >
            <span
              className={cn(
                "flex size-5 items-center justify-center rounded-full border text-[11px]",
                i <= current ? "border-brand" : "border-border",
              )}
            >
              {i + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>

      {screen === "start" && (
        <StartScreen
          entry={entry}
          file={file}
          onFile={setFile}
          target={target}
          onTarget={setTarget}
          onStart={() => analyze()}
        />
      )}
      {screen === "working" && (
        <ProgressScreen
          target={start?.target_label ?? targetLabel}
          stage={stage}
          receives={chosenTarget?.receives ?? []}
          error={failed}
          onRetry={() => analyze()}
          onBack={restart}
          onChooseType={(dataType) => analyze(dataType)}
        />
      )}
      {screen === "result" && start && preview && (
        <ResultScreen
          start={start}
          preview={preview}
          edits={edits}
          onEdits={setEdits}
          extra={extra}
          onExtra={setExtra}
          updating={check.isPending}
          updateError={check.error}
          downloading={prepared.isPending}
          downloadError={prepared.error}
          onDownload={() => prepared.mutate(input(start, edits, extra, true))}
          onRestart={restart}
        />
      )}
    </div>
  );
}
