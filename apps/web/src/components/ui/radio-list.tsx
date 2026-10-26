"use client";

/** Liste de choix UNIQUE (radio group), style commun de la console (référence `captures/choice-buttons.PNG`) :
 *  une option par ligne, cercle à gauche, libellé en texte normal, sans cadre ni fond ; option choisie = cercle
 *  plein à l'encre de la charte (`--primary`) avec un point blanc. Un vrai `<input type="radio">` (masqué) porte
 *  le clavier (flèches, espace) et l'accessibilité ; focus visible en bleu brand. */
import { type ReactNode, useId } from "react";

import { cn } from "@/lib/utils";

export type RadioOption = { value: string; label: ReactNode; hint?: ReactNode };

export function RadioList({
  options,
  value,
  onChange,
  label,
  name,
  className,
}: {
  options: RadioOption[];
  value: string;
  onChange: (value: string) => void;
  /** nom accessible du groupe (la question) */
  label: string;
  name?: string;
  className?: string;
}) {
  const auto = useId();
  const group = name ?? auto;
  return (
    <div role="radiogroup" aria-label={label} className={cn("space-y-2.5", className)}>
      {options.map((o) => {
        const checked = o.value === value;
        return (
          <label key={o.value} className="flex w-fit cursor-pointer items-start gap-3">
            <input
              type="radio"
              name={group}
              value={o.value}
              checked={checked}
              onChange={() => onChange(o.value)}
              className="peer sr-only"
            />
            <span
              aria-hidden
              className={cn(
                "mt-0.5 flex size-[18px] shrink-0 items-center justify-center rounded-full border transition-colors",
                "peer-focus-visible:ring-2 peer-focus-visible:ring-brand/40 peer-focus-visible:ring-offset-1",
                checked ? "border-primary bg-primary" : "border-border bg-card hover:border-muted-foreground",
              )}
            >
              {checked && <span className="size-1.5 rounded-full bg-card" />}
            </span>
            <span className="min-w-0">
              <span className="block">{o.label}</span>
              {o.hint && <span className="meta block text-muted-foreground">{o.hint}</span>}
            </span>
          </label>
        );
      })}
    </div>
  );
}
