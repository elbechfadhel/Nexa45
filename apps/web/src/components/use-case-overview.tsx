"use client";

/** Rendu des pages Overview : cartes de use cases issues du catalogue (`lib/use-cases/catalog.mjs`), seule source
 *  de vérité. Aucune liste de workflows ici : ce composant AFFICHE ce que le catalogue déclare (titre, flux,
 *  statut, démo, point de départ) et dérive l'action du statut (`canStart`). Il n'exécute aucune intégration. */
import { ArrowRight, ChevronDown, Clock, FileSpreadsheet, Play } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Pill } from "@/components/status-badge";
import { SystemLogo } from "@/components/system-logo";
import { Card, CardContent } from "@/components/ui/card";
import { canStart, groupBySection, sectionTitle, SYSTEM_NAMES } from "@/lib/use-cases/catalog.mjs";
import { cn } from "@/lib/utils";

type UseCase = import("@/lib/use-cases/catalog.mjs").UseCaseDefinition;
export type OverviewVariant = "admin" | "supplier";

const STATUS_PILL: Partial<Record<UseCase["status"], { tone: "info" | "warning"; label: string }>> = {
  coming_soon: { tone: "info", label: "Coming soon" },
  experimental: { tone: "warning", label: "Experimental" },
};

/** Sections et cartes d'une liste de use cases. `startable = false` : carte de référence (autre profil).
 *  Cartes numérotées à la suite dans l'ordre d'affichage, toutes sections confondues, à partir de `firstNumber`
 *  (une page qui affiche plusieurs listes poursuit la numérotation). Présentation seulement. */
export function UseCaseSections({
  useCases,
  variant,
  startable = true,
  note,
  firstNumber = 1,
}: {
  useCases: UseCase[];
  variant: OverviewVariant;
  startable?: boolean;
  note?: string;
  firstNumber?: number;
}) {
  const groups = groupBySection(useCases);
  // numéro de la première carte de chaque section : cartes des sections précédentes + firstNumber
  const offsets = groups.map((_, i) => firstNumber + groups.slice(0, i).reduce((n, g) => n + g.items.length, 0));
  return (
    <div className="page-stack">
      {groups.map(({ section, items }, g) => (
        <section key={section.id} className="space-y-3">
          <h2 className="section-title">{sectionTitle(section, variant)}</h2>
          <div className="grid gap-4.5 md:grid-cols-2 xl:grid-cols-3">
            {items.map((u, i) => (
              <UseCaseCard
                key={u.id}
                useCase={u}
                number={offsets[g] + i}
                variant={variant}
                startable={startable}
                note={note}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/** Flèche du flux des systèmes (source → cible) : icône centrée dans un cercle gris très clair, flèche gris foncé.
 *  Décorative (aria-hidden) : le sens est porté par l'ordre des éléments. */
function FlowArrow({ size = "sm" }: { size?: "sm" | "md" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full bg-muted text-[var(--text-dim)] ring-1 ring-border",
        size === "md" ? "size-7" : "size-5",
      )}
    >
      <ArrowRight className={size === "md" ? "size-4" : "size-3"} strokeWidth={2.25} />
    </span>
  );
}

/** Systèmes concernés par le use case : logos de la source, puis « → » et logos de la cible s'il y en a une. */
function Systems({ systems }: { systems: UseCase["systems"] }) {
  const logos = (codes: UseCase["systems"]["source"]) =>
    codes.map((code) =>
      code === "file" ? (
        <span
          key={code}
          title={SYSTEM_NAMES[code]}
          aria-label={SYSTEM_NAMES[code]}
          className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60 text-muted-foreground"
        >
          <FileSpreadsheet className="size-5" />
        </span>
      ) : code === "go45" ? (
        // la plateforme elle-même : marque de l'en-tête (accent menthe réservé à la marque), pas un logo externe
        <span
          key={code}
          title={SYSTEM_NAMES[code]}
          aria-label={SYSTEM_NAMES[code]}
          className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60 text-[11px] font-extrabold tracking-wide"
        >
          GO<span className="text-brand">45</span>
        </span>
      ) : (
        <span key={code} title={SYSTEM_NAMES[code]} className="flex">
          <SystemLogo code={code} name={SYSTEM_NAMES[code]} className="size-10 rounded-lg" />
        </span>
      ),
    );
  const label = [systems.source, systems.target]
    .filter((side) => side.length > 0)
    .map((side) => side.map((code) => SYSTEM_NAMES[code]).join(", "))
    .join(" to ");
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={`Systems: ${label}`}>
      {logos(systems.source)}
      {systems.target.length > 0 && <FlowArrow size="md" />}
      {logos(systems.target)}
    </div>
  );
}

function UseCaseCard({
  useCase: u,
  number,
  variant,
  startable,
  note,
}: {
  useCase: UseCase;
  number: number;
  variant: OverviewVariant;
  startable: boolean;
  note?: string;
}) {
  const [open, setOpen] = useState(false);
  const pill = STATUS_PILL[u.status];
  const start = startable && canStart(u) ? u.start : undefined;
  const hasDetails = u.steps.length > 0 || u.expected.length > 0;
  const hasSystems = u.systems.source.length > 0 || u.systems.target.length > 0;

  return (
    // carte : titre numéroté, action en lien discret
    <Card size="sm" className={cn(u.status === "coming_soon" && "opacity-80")}>
      <CardContent className="flex h-full flex-col gap-3">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex items-start justify-between gap-2">
              <div className="item-title">
                {/* numéro de la carte : rang d'affichage sur la page */}
                <span className="mr-1.5 text-muted-foreground tabular-nums">{number}.</span>
                {u.title}
              </div>
              {pill && <Pill tone={pill.tone}>{pill.label}</Pill>}
            </div>
            <p className="text-muted-foreground">{u.summary}</p>
          </div>
        </div>
        {hasSystems && <Systems systems={u.systems} />}
        {variant === "admin" && (u.demo?.instance || u.demo?.minutes) && (
          <div className="meta flex flex-wrap items-center gap-x-3">
            {u.demo?.instance && <span>Demo: {u.demo.instance}</span>}
            {u.demo?.minutes && (
              <span className="inline-flex items-center gap-1">
                <Clock className="size-3.5" /> ~{u.demo.minutes} min
              </span>
            )}
          </div>
        )}

        {open && hasDetails && (
          <div className="space-y-3 border-t border-border pt-3 text-sm">
            <div>
              <div className="eyebrow">Goal</div>
              <p>{u.goal}</p>
            </div>
            {u.steps.length > 0 && (
              <div>
                <div className="eyebrow">{variant === "admin" ? "Demo steps" : "Steps"}</div>
                <ol className="list-decimal space-y-0.5 pl-5">
                  {u.steps.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ol>
              </div>
            )}
            {u.expected.length > 0 && (
              <div>
                <div className="eyebrow">Expected result</div>
                <ul className="list-disc space-y-0.5 pl-5">
                  {u.expected.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {u.prerequisites.length > 0 && (
              <p className="meta">Before you start: {u.prerequisites.join(" · ")}</p>
            )}
            {variant === "admin" && u.technical_reference && (
              <p className="meta">Reference: {u.technical_reference}</p>
            )}
          </div>
        )}

        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
          {start ? (
            <Link
              href={start.route}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-brand"
            >
              <Play className="size-4" strokeWidth={1.75} /> {start.label}
            </Link>
          ) : u.status === "coming_soon" ? (
            <span className="meta">Not available yet</span>
          ) : (
            note && <span className="meta">{note}</span>
          )}
          {hasDetails && (
            <button
              type="button"
              className="meta ml-auto inline-flex items-center gap-1 hover:text-foreground"
              onClick={() => setOpen(!open)}
              aria-expanded={open}
            >
              {variant === "admin" ? "How to demo" : "How it works"}
              <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
            </button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
