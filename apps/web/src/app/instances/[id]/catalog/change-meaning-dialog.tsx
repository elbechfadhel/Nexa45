"use client";

/** « Change meaning » : choix d'une clé du vocabulaire contrôlé (GET /admin/vocabulary/product), ou
 *  « Other / I don't know » avec une note. Jamais de texte libre comme semantic_key : le Core valide. */
import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ErrorBox, LoadingRows } from "@/components/registry";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { BindingView } from "@/lib/api/client";
import { useVocabulary } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";

const OTHER = "other"; // réponse « autre / je ne sais pas » (OTHER_KEY côté Core)

const GROUP_LABEL: Record<string, string> = {
  identity: "Identity",
  commercial: "Commercial",
  description: "Description",
  classification: "Classification",
  logistics: "Logistics",
  variants: "Variants",
  supplier: "Supplier",
  media: "Media",
};

export function ChangeMeaningDialog({
  question,
  pending,
  onCancel,
  onSubmit,
}: {
  question: BindingView | null;
  pending: boolean;
  onCancel: () => void;
  onSubmit: (key: string, note: string | null) => void;
}) {
  return (
    <Dialog open={!!question} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        {/* remonté à chaque question : sélection initiale = proposition ou ancienne réponse */}
        {question && (
          <ChangeForm
            key={question.path}
            question={question}
            pending={pending}
            onCancel={onCancel}
            onSubmit={onSubmit}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ChangeForm({
  question,
  pending,
  onCancel,
  onSubmit,
}: {
  question: BindingView;
  pending: boolean;
  onCancel: () => void;
  onSubmit: (key: string, note: string | null) => void;
}) {
  const vocabulary = useVocabulary();
  const [selected, setSelected] = useState<string>(question.semantic_key ?? "");
  const [search, setSearch] = useState("");
  const [note, setNote] = useState("");
  const list = useRef<HTMLDivElement>(null);

  // la réponse pré-sélectionnée (proposition ou ancienne réponse) est visible à l'ouverture
  useEffect(() => {
    list.current?.querySelector("input:checked")?.scrollIntoView({ block: "center" });
  }, [vocabulary.data]);

  const groups = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (vocabulary.data?.groups ?? [])
      .map((g) => ({
        ...g,
        keys: g.keys.filter(
          (k) => !q || [k.key, k.label, k.description].some((t) => t.toLowerCase().includes(q)),
        ),
      }))
      .filter((g) => g.keys.length > 0);
  }, [vocabulary.data, search]);

  return (
    <>
      <div className="space-y-1">
        <DialogTitle>Change meaning</DialogTitle>
        <DialogDescription>
          {question.field.label ?? question.field_technical_name} · <span className="tech">{question.path}</span>
        </DialogDescription>
      </div>
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search a meaning…"
          className="h-9 pl-8"
          aria-label="Search a meaning"
          autoFocus
        />
      </div>
      <div ref={list} className="-mx-2 min-h-0 flex-1 overflow-y-auto px-2" role="radiogroup" aria-label="Meaning">
        {vocabulary.error ? (
          <ErrorBox error={vocabulary.error} />
        ) : !vocabulary.data ? (
          <LoadingRows />
        ) : (
          <div className="space-y-3">
            {groups.map((g) => (
              <fieldset key={g.group}>
                <legend className="eyebrow mb-1">{GROUP_LABEL[g.group] ?? g.group}</legend>
                <div className="space-y-0.5">
                  {g.keys.map((k) => (
                    <Choice
                      key={k.key}
                      value={k.key}
                      selected={selected}
                      onSelect={setSelected}
                      label={k.label}
                      hint={k.description}
                      badge={k.key === question.semantic_key ? currentBadge(question) : undefined}
                    />
                  ))}
                </div>
              </fieldset>
            ))}
            {groups.length === 0 && <p className="meta">No meaning matches “{search}”.</p>}
            <fieldset>
              <legend className="eyebrow mb-1">Other</legend>
              <Choice
                value={OTHER}
                selected={selected}
                onSelect={setSelected}
                label="Other / I don't know"
                hint="None of these meanings fits. Leave a note for the integration team."
              />
            </fieldset>
          </div>
        )}
      </div>
      {selected === OTHER && (
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          placeholder="What does this field contain? (optional)"
          aria-label="Note"
          className="input"
        />
      )}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button
          onClick={() => onSubmit(selected, selected === OTHER && note.trim() ? note.trim() : null)}
          disabled={pending || !selected}
        >
          {pending ? "Saving…" : "Confirm"}
        </Button>
      </div>
    </>
  );
}

function currentBadge(q: BindingView) {
  return q.meaning_status === "STALE" ? "your previous answer" : "suggested";
}

function Choice({
  value,
  selected,
  onSelect,
  label,
  hint,
  badge,
}: {
  value: string;
  selected: string;
  onSelect: (v: string) => void;
  label: string;
  hint: string;
  badge?: string;
}) {
  const on = selected === value;
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-lg border border-transparent px-2.5 py-1.5 hover:bg-muted/60",
        on && "border-brand/50 bg-muted/60",
      )}
    >
      <input
        type="radio"
        name="meaning"
        value={value}
        checked={on}
        onChange={() => onSelect(value)}
        className="mt-1.5 accent-(--brand)"
      />
      <span>
        <span className="font-medium">{label}</span>
        {badge && <span className="meta ml-2">({badge})</span>}
        <span className="meta block">{hint}</span>
      </span>
    </label>
  );
}
