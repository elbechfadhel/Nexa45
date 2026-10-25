/** Étapes d'un wizard du portail (« Connect a system », « New import ») : terminée = ✓, active = pleine, à venir = contour. Sur petit écran,
 *  seuls les numéros restent, avec le libellé de l'étape active. */
import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

export const STEPS = ["Select system", "Connection details", "Test connection", "Review & save"] as const;

export function Stepper({ current, steps = STEPS }: { current: number; steps?: readonly string[] }) {
  return (
    <ol className="flex items-start" aria-label="Steps">
      {steps.map((label, i) => {
        const n = i + 1;
        const done = n < current;
        const active = n === current;
        return (
          <li key={label} className="relative flex flex-1 flex-col items-center gap-2" aria-current={active ? "step" : undefined}>
            {i > 0 && (
              <span
                aria-hidden
                className={cn(
                  "absolute top-4 right-1/2 h-px w-full -translate-y-1/2",
                  n <= current ? "bg-brand/60" : "bg-border",
                )}
              />
            )}
            <span
              className={cn(
                "relative z-10 flex size-8 items-center justify-center rounded-full border text-sm font-semibold",
                done && "border-brand bg-brand/15 text-brand",
                active && "border-brand bg-brand text-background shadow-[0_0_0_4px] shadow-brand/15",
                !done && !active && "border-border bg-background text-muted-foreground",
              )}
            >
              {done ? <Check className="size-4" /> : n}
            </span>
            <span
              className={cn(
                "text-center text-sm",
                active ? "font-semibold text-foreground" : done ? "text-brand" : "text-muted-foreground",
                !active && "hidden sm:block",
              )}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
