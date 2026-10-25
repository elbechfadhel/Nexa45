"use client";

/** Bascule sombre / clair. La classe « dark » est posée avant l'affichage par le script du layout ;
 *  on la lit directement dans le DOM, sans état dupliqué. */
import { Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

import { THEME_KEY } from "@/lib/theme";
import { cn } from "@/lib/utils";

function subscribe(onChange: () => void) {
  const obs = new MutationObserver(onChange);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => obs.disconnect();
}

export function ThemeToggle({ className, withLabel = false }: { className?: string; withLabel?: boolean }) {
  const dark = useSyncExternalStore(
    subscribe,
    () => document.documentElement.classList.contains("dark"),
    () => true,
  );
  const next = dark ? "light" : "dark";
  const label = dark ? "Switch to light theme" : "Switch to dark theme";
  return (
    <button
      type="button"
      onClick={() => {
        document.documentElement.classList.toggle("dark", next === "dark");
        try {
          localStorage.setItem(THEME_KEY, next);
        } catch {}
      }}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex items-center gap-2 rounded-lg p-2 text-nav-link transition-colors hover:bg-foreground/5 hover:text-foreground",
        className,
      )}
    >
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
      {withLabel && <span className="text-sm">{dark ? "Light theme" : "Dark theme"}</span>}
    </button>
  );
}
