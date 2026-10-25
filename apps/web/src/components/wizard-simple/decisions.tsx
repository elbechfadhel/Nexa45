"use client";

/** Décisions du mode Simple : celles appliquées d'office (chacune modifiable d'un clic) et les questions posées
 *  (3 au plus), toutes présélectionnées. Les libellés viennent de l'API (jamais une valeur technique affichée). */
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { PreparedFile, QuickQuestion, QuickStart } from "@/lib/api/wizard-hooks";
import { cn } from "@/lib/utils";
import { answerText, COLUMN_ANSWER, defaultOrigin, type Edits, questionKey } from "@/lib/wizard/simple.mjs";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

type Option = { value: string; label: string };

export function Decisions({
  start,
  preview,
  edits,
  onEdits,
  extra,
  onExtra,
}: {
  start: QuickStart;
  preview: PreparedFile;
  edits: Edits;
  onEdits: (e: Edits) => void;
  extra: Record<string, string>;
  onExtra: (e: Record<string, string>) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const answer = (q: QuickQuestion, value: string) =>
    onEdits({ ...edits, answers: { ...edits.answers, [questionKey(q)]: value } });
  // décisions sur les valeurs que l'aperçu pose encore (aucune réponse) : jamais appliquées seules ; une décision
  // déjà posée par le démarrage n'est jamais montrée deux fois
  const known = new Set([...start.questions, ...start.applied].map((q) => q.decision).filter(Boolean));
  const openValues = preview.value_questions.filter((q) => !q.answer && !extra[q.key] && !known.has(q.key));
  const asked = start.questions.length + openValues.length;
  const preselected =
    start.questions.filter((q) => q.default).length + openValues.filter((q) => q.suggested).length;

  if (start.applied.length === 0 && asked === 0) return null;
  return (
    <Card className="bg-transparent shadow-none">
      <CardContent className="space-y-6">
        {asked > 0 && (
          <section className="space-y-4">
            <div>
              <h2 className="item-title">{L.questionsHeading}</h2>
              <p className="meta text-muted-foreground">{L.questionsHelp(asked, preselected)}</p>
            </div>
            {start.questions.map((q) => (
              <Question
                key={questionKey(q)}
                title={q.question.title}
                help={q.question.help}
                options={optionsOf(q)}
                preselected={q.default ?? null}
                freeText={!hasColumns(q) && (q.question.free_text || q.question.choices.length === 0)}
                value={edits.answers[questionKey(q)] ?? ""}
                onChange={(v) => answer(q, v)}
              />
            ))}
            {openValues.map((q) => (
              <Question
                key={q.key}
                title={q.title}
                help={q.help}
                options={q.options}
                preselected={q.suggested ?? null}
                freeText={q.allow_other}
                value={extra[q.key] ?? ""}
                onChange={(v) => onExtra({ ...extra, [q.key]: v })}
              />
            ))}
          </section>
        )}

        {start.applied.length > 0 && (
          <section className="space-y-3">
            <div>
              <h2 className="item-title">{L.appliedHeading}</h2>
              <p className="meta text-muted-foreground">{L.appliedHelp}</p>
            </div>
            <ul className="divide-y rounded-lg border">
              {start.applied.map((q) => {
                const key = questionKey(q);
                const value = edits.answers[key] ?? q.default ?? "";
                const editing = open === key;
                return (
                  <li key={key} className="space-y-3 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <span className="font-medium">{q.question.label}</span>
                        <span className="text-muted-foreground"> · {answerText(q, value)}</span>
                        {value === q.default && defaultOrigin(q) && (
                          <span className="meta text-muted-foreground"> ({defaultOrigin(q)})</span>
                        )}
                      </div>
                      <Button variant="outline" size="sm" onClick={() => setOpen(editing ? null : key)}>
                        {editing ? L.done : L.change}
                      </Button>
                    </div>
                    {editing && (
                      <Question
                        title={q.question.title}
                        help={q.question.help}
                        options={q.question.choices}
                        preselected={q.default ?? null}
                        freeText={q.question.free_text}
                        value={value}
                        onChange={(v) => answer(q, v)}
                      />
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        )}
      </CardContent>
    </Card>
  );
}

/** Une question « quelle colonne de votre fichier… » propose les colonnes du fichier ; sinon ses choix. */
function hasColumns(q: QuickQuestion): boolean {
  return (q.question.file_columns ?? []).length > 0;
}

function optionsOf(q: QuickQuestion): Option[] {
  if (!hasColumns(q)) return q.question.choices;
  return q.question.file_columns.map((c) => ({
    value: `${COLUMN_ANSWER}${c.position}`,
    label: L.columnOption(c.header, c.examples),
  }));
}

function Question({
  title,
  help,
  options,
  preselected,
  freeText,
  value,
  onChange,
}: {
  title: string;
  help?: string | null;
  options: Option[];
  preselected: string | null;
  freeText: boolean;
  value: string;
  onChange: (value: string) => void;
}) {
  const known = options.some((o) => o.value === value);
  const [other, setOther] = useState(!known && value !== "" ? value : "");
  return (
    <fieldset className="space-y-2">
      <legend className="font-medium">{title}</legend>
      {help && <p className="meta text-muted-foreground">{help}</p>}
      <div className="space-y-1.5" role="radiogroup">
        {options.map((o) => (
          <label
            key={o.value}
            className={cn(
              "flex cursor-pointer items-start gap-3 rounded-lg border p-2.5 transition-colors",
              value === o.value ? "border-brand bg-brand/5" : "border-border hover:border-brand/60",
            )}
          >
            <input
              type="radio"
              className="mt-1 accent-[var(--brand)]"
              checked={value === o.value}
              onChange={() => onChange(o.value)}
            />
            <span className="min-w-0 flex-1">{o.label}</span>
            {o.value === preselected && <span className="meta text-muted-foreground">{L.preselected}</span>}
          </label>
        ))}
        {freeText && (
          <label className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-2.5">
            {options.length > 0 && (
              <input
                type="radio"
                className="accent-[var(--brand)]"
                checked={!known && value !== ""}
                onChange={() => other && onChange(other)}
              />
            )}
            {/* sans choix proposé (ex. un numéro du cabinet) : une simple réponse, pas une « autre valeur » */}
            <span>{options.length > 0 ? L.otherValue : L.yourAnswer}</span>
            <input
              className="input min-w-0 flex-1"
              placeholder={L.otherPlaceholder}
              value={other}
              onChange={(e) => {
                setOther(e.target.value);
                if (e.target.value.trim()) onChange(e.target.value.trim());
              }}
            />
          </label>
        )}
      </div>
    </fieldset>
  );
}
