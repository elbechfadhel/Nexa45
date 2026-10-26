"use client";

/** Liste de choix UNIQUE (radio group), style commun de la console (référence `captures/choice-buttons.PNG`) :
 *  une option par ligne, cercle à gauche, libellé en texte normal, sans cadre ni fond ; option choisie = cercle
 *  plein à l'encre de la charte (`--primary`) avec un point blanc. Un vrai `<input type="radio">` (masqué) porte
 *  le clavier (flèches, espace) et l'accessibilité ; focus visible en bleu brand.
 *
 *  `hint` : précision sous le libellé ; `content` : bloc affiché SOUS l'option quand elle est choisie (ex. les
 *  versions d'un système) ; `layout` : `list` (défaut), `columns` (longues listes : 2 ou 3 colonnes) ou `inline`
 *  (deux ou trois options courtes sur une ligne). */
import { type ReactNode, useId } from "react";

import { cn } from "@/lib/utils";

export type RadioOption = {
  value: string;
  label: ReactNode;
  hint?: ReactNode;
  content?: ReactNode;
  disabled?: boolean;
};

export function RadioList({
  options,
  value,
  onChange,
  label,
  name,
  layout = "list",
  className,
}: {
  options: RadioOption[];
  value: string;
  onChange: (value: string) => void;
  /** nom accessible du groupe (la question) */
  label: string;
  name?: string;
  layout?: "list" | "columns" | "inline";
  className?: string;
}) {
  const auto = useId();
  const group = name ?? auto;
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn(
        layout === "inline"
          ? "flex flex-wrap items-center gap-x-6 gap-y-2"
          : layout === "columns"
            ? "grid gap-x-6 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3"
            : "space-y-2.5",
        className,
      )}
    >
      {options.map((o) => (
        <div key={o.value} className="min-w-0">
          <RadioItem
            name={group}
            value={o.value}
            checked={o.value === value}
            disabled={o.disabled}
            onSelect={onChange}
            hint={o.hint}
          >
            {o.label}
          </RadioItem>
          {o.value === value && o.content && <div className="mt-2.5 ml-[30px]">{o.content}</div>}
        </div>
      ))}
    </div>
  );
}

/** Une option d'un radio group (même style partout) : pour une liste construite à la main ; sinon `RadioList`. */
export function RadioItem({
  name,
  value,
  checked,
  onSelect,
  disabled = false,
  hint,
  className,
  children,
}: {
  name: string;
  value: string;
  checked: boolean;
  onSelect: (value: string) => void;
  disabled?: boolean;
  hint?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label
      className={cn(
        "flex w-fit max-w-full items-start gap-3",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
        className,
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={() => onSelect(value)}
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
        <span className="block">{children}</span>
        {hint && <span className="meta block text-muted-foreground">{hint}</span>}
      </span>
    </label>
  );
}
