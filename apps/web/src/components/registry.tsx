"use client";

/** Petits composants d'affichage partagés. Aucune règle métier : ils mettent en forme les
 *  indicateurs calculés par le Core (metadata.technical, metadata.custom, change_type...). */
import { StatusBadge, type Tone } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { ChangeOut, FieldOut } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/** Même convention que `uie registry show` : champs techniques en gris, champs x_ en jaune. */
export function FieldName({ field, className }: { field: FieldOut; className?: string }) {
  const meta = field.metadata as { technical?: boolean; custom?: boolean };
  return (
    <span
      className={cn(
        "tech",
        meta.technical && "text-muted-foreground",
        meta.custom && "rounded bg-warning/15 px-1 text-warning",
        className,
      )}
    >
      {field.technical_name}
    </span>
  );
}

export const CHANGE_TONE: Record<string, Tone> = {
  ADDED: "success",
  REMOVED: "danger",
  CHANGED: "changed",
  UNCHANGED: "neutral",
};

export function ChangeBadge({ type }: { type: ChangeOut["change_type"] }) {
  return (
    <StatusBadge mono tone={CHANGE_TONE[type] ?? "neutral"} className="w-20">
      {type}
    </StatusBadge>
  );
}

export function Flag({ on, label, tone }: { on: boolean; label: string; tone?: "required" }) {
  return on ? (
    <Badge
      variant="secondary"
      className={cn(
        "font-mono text-[11px]",
        tone === "required" && "bg-danger/10 text-danger",
      )}
    >
      {label}
    </Badge>
  ) : null;
}

export function LoadingRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-6 w-full" />
      ))}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="rounded-md border border-danger/35 bg-danger/10 p-3 text-sm text-danger">
      API error: {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

// dates jj/mm/aaaa et séparateur de milliers « 7 055 », comme la maquette validée ; suivra la locale avec l'i18n
export const formatDate = (iso: string) =>
  new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" }).format(new Date(iso));

export const formatInt = (n: number) => new Intl.NumberFormat("fr-FR").format(n);
