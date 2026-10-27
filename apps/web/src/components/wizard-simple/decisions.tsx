"use client";

/** Décisions du mode Simple : celles appliquées d'office (chacune modifiable d'un clic) et les questions posées
 *  (3 au plus), toutes présélectionnées. Les libellés viennent de l'API (jamais une valeur technique affichée). */
import { Fragment, useState } from "react";

import { Button } from "@/components/ui/button";
import { RadioList } from "@/components/ui/radio-list";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { PreparedFile, QuickQuestion, QuickStart } from "@/lib/api/wizard-hooks";
import {
  answerText,
  appliedThemes,
  COLUMN_ANSWER,
  defaultOrigin,
  type Edits,
  questionKey,
  rowRate,
  rowTaxKey,
  settingsSummary,
} from "@/lib/wizard/simple.mjs";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

/** Option « autre valeur » : réponse saisie (jamais une valeur proposée). */
const OTHER = "__other__";

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

  // « Decided for you » par thème (type de décision) : réglages, comptes par catégorie, décisions sur le fichier
  const themes = appliedThemes(start.applied) as Record<"settings" | "rows" | "taxes" | "others", QuickQuestion[]>;
  const valueOf = (q: QuickQuestion) => edits.answers[questionKey(q)] ?? q.default ?? "";
  const values = Object.fromEntries(start.applied.map((q) => [questionKey(q), valueOf(q)]));
  const toggle = (key: string) => setOpen(open === key ? null : key);
  const rowProps = (q: QuickQuestion) => ({
    value: valueOf(q),
    editing: open === questionKey(q),
    onToggle: () => toggle(questionKey(q)),
    onChange: (v: string) => answer(q, v),
  });

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
          <section className="space-y-5">
            <div>
              <h2 className="item-title">{L.appliedHeading}</h2>
              <p className="meta text-muted-foreground">{L.appliedHelp}</p>
            </div>

            {themes.settings.length > 0 && (
              <div className="space-y-3 rounded-lg border p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 space-y-0.5">
                    <h3 className="eyebrow">{L.settingsHeading}</h3>
                    <p className="meta text-muted-foreground">{settingsSummary(themes.settings, values)}</p>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => toggle(SETTINGS)}>
                    {open === SETTINGS ? L.done : L.change}
                  </Button>
                </div>
                {open === SETTINGS && (
                  <ul className="divide-y rounded-lg border">
                    {themes.settings.map((q) => (
                      <AppliedRow key={questionKey(q)} q={q} {...rowProps(q)} />
                    ))}
                  </ul>
                )}
              </div>
            )}

            {themes.rows.length > 0 && (
              <div className="space-y-2">
                <div>
                  <h3 className="eyebrow">{L.mappingsHeading}</h3>
                  <p className="meta text-muted-foreground">{L.mappingsHelp}</p>
                </div>
                <Table className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead>{L.mappingColumns.category}</TableHead>
                      <TableHead className="w-20">{L.mappingColumns.rate}</TableHead>
                      <TableHead className="w-28">{L.mappingColumns.account}</TableHead>
                      <TableHead className="w-48">{L.mappingColumns.taxKey}</TableHead>
                      <TableHead className="w-28" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {themes.rows.map((q) => {
                      const key = questionKey(q);
                      const tax = themes.taxes.find((t) => t.decision === q.mapping?.tax_decision);
                      const editing = open === key;
                      return (
                        <Fragment key={key}>
                          <TableRow>
                            <TableCell className="break-words whitespace-normal">{q.mapping?.category}</TableCell>
                            <TableCell>{rowRate(q)}</TableCell>
                            <TableCell>{answerText(q, valueOf(q))}</TableCell>
                            <TableCell className="whitespace-normal">{rowTaxKey(q, tax, values)}</TableCell>
                            <TableCell className="text-right">
                              <Button variant="outline" size="sm" onClick={() => toggle(key)}>
                                {editing ? L.done : L.change}
                              </Button>
                            </TableCell>
                          </TableRow>
                          {editing && (
                            <TableRow>
                              <TableCell colSpan={5} className="space-y-4 whitespace-normal">
                                <Question
                                  title={q.question.title}
                                  help={q.question.help}
                                  options={q.question.choices}
                                  preselected={q.default ?? null}
                                  freeText={q.question.free_text}
                                  value={valueOf(q)}
                                  onChange={(v) => answer(q, v)}
                                />
                                {tax && (
                                  <Question
                                    title={tax.question.title}
                                    help={L.taxKeyShared(q.mapping?.rate ?? "")}
                                    options={tax.question.choices}
                                    preselected={tax.default ?? null}
                                    freeText={tax.question.free_text}
                                    value={valueOf(tax)}
                                    onChange={(v) => answer(tax, v)}
                                  />
                                )}
                              </TableCell>
                            </TableRow>
                          )}
                        </Fragment>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}

            {themes.others.length > 0 && (
              <div className="space-y-2">
                <h3 className="eyebrow">{L.fileDecisionsHeading}</h3>
                <ul className="divide-y rounded-lg border">
                  {themes.others.map((q) => (
                    <AppliedRow key={questionKey(q)} q={q} {...rowProps(q)} />
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}
      </CardContent>
    </Card>
  );
}

/** Bloc des réglages : son « Change » déplie le détail. */
const SETTINGS = "__settings__";

/** Une décision appliquée sur une ligne : libellé, valeur, origine, « Change » qui ouvre la question. */
function AppliedRow({
  q,
  value,
  editing,
  onToggle,
  onChange,
}: {
  q: QuickQuestion;
  value: string;
  editing: boolean;
  onToggle: () => void;
  onChange: (v: string) => void;
}) {
  return (
    <li className="space-y-3 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <span className="font-medium">{q.question.label}</span>
          <span className="text-muted-foreground"> · {answerText(q, value)}</span>
          {value === q.default && defaultOrigin(q) && (
            <span className="meta text-muted-foreground"> ({defaultOrigin(q)})</span>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={onToggle}>
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
          onChange={onChange}
        />
      )}
    </li>
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
  const [otherMode, setOtherMode] = useState(!known && value !== "");
  const field = (
    <input
      className="input w-full max-w-sm"
      placeholder={L.otherPlaceholder}
      aria-label={options.length > 0 ? L.otherValue : L.yourAnswer}
      value={other}
      onChange={(e) => {
        setOther(e.target.value);
        if (e.target.value.trim()) onChange(e.target.value.trim());
      }}
    />
  );
  return (
    <fieldset className="space-y-2">
      <legend className="font-medium">{title}</legend>
      {help && <p className="meta text-muted-foreground">{help}</p>}
      {options.length > 0 ? (
        <RadioList
          label={title}
          value={otherMode ? OTHER : value}
          onChange={(v) => {
            if (v === OTHER) {
              setOtherMode(true);
              if (other.trim()) onChange(other.trim());
            } else {
              setOtherMode(false);
              onChange(v);
            }
          }}
          options={[
            ...options.map((o) => ({
              value: o.value,
              label: o.label,
              hint: o.value === preselected ? L.preselected : undefined,
            })),
            ...(freeText ? [{ value: OTHER, label: L.otherValue, content: field }] : []),
          ]}
        />
      ) : (
        // sans choix proposé (ex. un numéro du cabinet) : une simple réponse, pas une « autre valeur »
        freeText && (
          <label className="flex flex-wrap items-center gap-3">
            <span>{L.yourAnswer}</span>
            {field}
          </label>
        )
      )}
    </fieldset>
  );
}
