"use client";

/** Colonnes du fichier en mode Simple : celles à confirmer en liste compacte (Confirm all, ou une par une, avec un
 *  autre champ possible), puis, repliées, celles placées automatiquement et celles non importées. Seuls les
 *  libellés des champs sont affichés, jamais leur nom technique. */
import { Check } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { QuickStart } from "@/lib/api/wizard-hooks";
import { simpleExamples } from "@/lib/wizard/review.mjs";
import {
  automaticColumns,
  columnsToConfirm,
  type Edits,
  fieldLabel,
  ignoredColumns,
  takenFields,
} from "@/lib/wizard/simple.mjs";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

/** Valeur du choix « Not imported » dans la liste des champs. */
const NONE = "";

export function Columns({ start, edits, onEdits }: { start: QuickStart; edits: Edits; onEdits: (e: Edits) => void }) {
  const toConfirm = columnsToConfirm(start);
  const automatic = automaticColumns(start);
  const ignored = ignoredColumns(start, edits);
  const confirmed = new Set(edits.confirmed);
  const pending = toConfirm.filter((c) => !confirmed.has(c.position));

  const confirm = (positions: number[]) =>
    onEdits({ ...edits, confirmed: [...new Set([...edits.confirmed, ...positions])] });
  const choose = (position: number, target: string) =>
    onEdits({
      ...edits,
      targets: { ...edits.targets, [position]: target === NONE ? null : target },
      confirmed: [...new Set([...edits.confirmed, position])], // choisir un champ vaut confirmation
    });

  return (
    <Card className="bg-transparent shadow-none">
      <CardContent className="space-y-6">
        {toConfirm.length > 0 && (
          <section className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="item-title">{L.confirmHeading}</h2>
                <p className="meta text-muted-foreground">{L.confirmHelp}</p>
              </div>
              <Button size="sm" onClick={() => confirm(pending.map((c) => c.position))} disabled={pending.length === 0}>
                {L.confirmAll}
              </Button>
            </div>
            <ul className="divide-y rounded-lg border">
              {toConfirm.map((c) => {
                const done = confirmed.has(c.position);
                const current = c.position in edits.targets ? edits.targets[c.position] : c.target;
                const taken = takenFields(start, edits, c.position);
                return (
                  <li key={c.position} className="grid gap-2 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                    <div className="min-w-0">
                      <div className="truncate font-medium">{c.header}</div>
                      <div className="meta truncate text-muted-foreground">
                        {simpleExamples(c.examples).join(" · ")}
                      </div>
                    </div>
                    <select
                      className="input min-w-0"
                      aria-label={c.header}
                      value={current ?? NONE}
                      onChange={(e) => choose(c.position, e.target.value)}
                    >
                      {start.proposal.fields
                        .filter((f) => f.name === current || !taken.has(f.name))
                        .map((f) => (
                          <option key={f.name} value={f.name}>
                            {f.label}
                          </option>
                        ))}
                      <option value={NONE}>{L.notImported}</option>
                    </select>
                    {done ? (
                      <span className="meta flex items-center gap-1 self-center font-semibold text-success">
                        <Check className="size-4" /> {L.confirmed}
                      </span>
                    ) : (
                      <Button variant="outline" size="sm" className="self-center" onClick={() => confirm([c.position])}>
                        {L.confirm}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
            {pending.length > 0 && <p className="meta text-muted-foreground">{L.pendingConfirm(pending.length)}</p>}
          </section>
        )}

        <Folded
          title={L.automaticHeading}
          count={L.automaticCount(automatic.length)}
          items={automatic.map((c) => ({ key: c.position, left: c.header, right: fieldLabel(start, c.target) }))}
        />
        <Folded
          title={L.ignoredHeading}
          count={L.ignoredCount(ignored.length)}
          items={ignored.map((c) => ({ key: c.position, left: c.header, right: simpleExamples(c.examples).join(" · ") }))}
        />
      </CardContent>
    </Card>
  );
}

function Folded({
  title,
  count,
  items,
}: {
  title: string;
  count: string;
  items: { key: number; left: string; right: string }[];
}) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="eyebrow">
          {title} · {count}
        </h3>
        <Button variant="ghost" size="sm" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? L.hideList : L.showList}
        </Button>
      </div>
      {open && (
        <ul className="divide-y rounded-lg border">
          {items.map((i) => (
            <li key={i.key} className="grid gap-1 p-2.5 sm:grid-cols-2">
              <span className="truncate font-medium">{i.left}</span>
              <span className="truncate text-muted-foreground">{i.right}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
