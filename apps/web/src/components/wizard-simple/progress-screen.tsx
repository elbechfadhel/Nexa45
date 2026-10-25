"use client";

/** Écran 2 du mode Simple : progression de l'analyse, jamais un écran vide. L'analyse du fichier est un seul appel
 *  au serveur : la lecture laisse place à l'analyse des colonnes après un court délai, et la vérification avec la
 *  cible correspond à l'aperçu réellement en cours. Le temps écoulé est affiché. */
import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { WizardFailure } from "@/lib/api/wizard-hooks";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

/** Lecture du fichier : quelques instants avant l'analyse des colonnes (même appel au serveur). */
const READING_MS = 1500;

export function ProgressScreen({
  target,
  receives,
  stage,
  error,
  onRetry,
  onBack,
  onChooseType,
}: {
  target: string;
  receives: string[];
  stage: "analyzing" | "checking";
  error: Error | null;
  onRetry: () => void;
  onBack: () => void;
  onChooseType: (dataType: string) => void;
}) {
  const [dataType, setDataType] = useState("");
  // type de données non reconnu : l'utilisateur le choisit, l'analyse reprend (jamais un arrêt sans issue)
  const choices = error instanceof WizardFailure ? error.dataTypes : [];
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 250);
    return () => clearInterval(timer);
  }, []);
  const reading = stage === "analyzing" && seconds * 1000 < READING_MS;
  const active = stage === "checking" ? 2 : reading ? 0 : 1;
  const steps = L.workingSteps(target, receives);

  return (
    <Card className="bg-transparent shadow-none">
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="item-title">{L.workingHeading}</h2>
          {!error && <span className="meta tech text-muted-foreground">{L.elapsed(seconds)}</span>}
        </div>
        <ol className="space-y-4">
          {steps.map((s, i) => {
            const done = i < active;
            const running = i === active && !error;
            return (
              <li key={s.title} className="flex gap-3">
                {done ? (
                  <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" aria-label={L.stepDone} />
                ) : running ? (
                  <Loader2 className="mt-0.5 size-5 shrink-0 animate-spin text-brand" aria-label={L.stepRunning} />
                ) : (
                  <Circle className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-label={L.stepWaiting} />
                )}
                <div className="min-w-0">
                  <div className={cn("font-medium", !done && !running && "text-muted-foreground")}>{s.title}</div>
                  <div className="meta text-muted-foreground">{s.help}</div>
                </div>
              </li>
            );
          })}
        </ol>
        {error && choices.length > 0 ? (
          <div className="space-y-3 rounded-md border border-border p-3">
            <div>
              <p className="font-medium">{L.dataTypeHeading}</p>
              <p className="meta text-muted-foreground">{error.message}</p>
              <p className="meta text-muted-foreground">{L.dataTypeHelp}</p>
            </div>
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={L.dataTypeHeading}>
              {choices.map((c) => (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={c === dataType}
                  onClick={() => setDataType(c)}
                  className={cn(
                    "rounded-lg border px-3 py-2 text-left transition-colors",
                    c === dataType ? "border-brand bg-brand/5" : "border-border hover:border-brand/60",
                  )}
                >
                  {c}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => onChooseType(dataType)} disabled={!dataType}>
                {L.continue}
              </Button>
              <Button variant="ghost" onClick={onBack}>
                {L.otherFile}
              </Button>
            </div>
          </div>
        ) : error ? (
          <div className="space-y-3 rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">
            <p className="font-medium">{L.failed}</p>
            <p>{error.message}</p>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={onRetry}>
                {L.retry}
              </Button>
              <Button variant="ghost" onClick={onBack}>
                {L.otherFile}
              </Button>
            </div>
          </div>
        ) : (
          <p className="meta text-muted-foreground">{L.workingNote}</p>
        )}
      </CardContent>
    </Card>
  );
}
