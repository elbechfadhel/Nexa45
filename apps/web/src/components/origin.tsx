/** Origine d'un élément (classification STANDARD / CUSTOM / THIRD_PARTY / UNKNOWN produite par le Core) :
 *  une seule convention de couleur pour toute la console (Instance Diff, Version Compare, Product Domain).
 *  STANDARD gris, CUSTOM jaune, THIRD_PARTY bleu, UNKNOWN rouge (non classable : à examiner). */
import { CircleHelp, Diamond, Shield, Triangle } from "lucide-react";

import { StatusBadge, type Tone } from "@/components/status-badge";

export const ORIGINS = ["STANDARD", "CUSTOM", "THIRD_PARTY", "UNKNOWN"] as const;
export type Origin = (typeof ORIGINS)[number];

export const ORIGIN_TONE: Record<Origin, Tone> = {
  STANDARD: "neutral",
  CUSTOM: "warning",
  THIRD_PARTY: "info",
  UNKNOWN: "danger",
};

export const ORIGIN_LABEL: Record<Origin, string> = {
  STANDARD: "Standard",
  CUSTOM: "Custom",
  THIRD_PARTY: "Third-party",
  UNKNOWN: "Unknown",
};

// classes écrites en entier : Tailwind ne détecte pas les noms construits dynamiquement
export const ORIGIN_ICON: Record<Origin, React.ReactNode> = {
  STANDARD: <Shield className="size-4 fill-neutral text-neutral" />,
  CUSTOM: <Diamond className="size-4 fill-warning text-warning" />,
  THIRD_PARTY: <Triangle className="size-3.5 fill-info text-info" />,
  UNKNOWN: <CircleHelp className="size-4 text-danger" />,
};

export const originTone = (origin: string | null | undefined): Tone =>
  ORIGIN_TONE[origin as Origin] ?? "neutral";

export function OriginBadge({ origin, title }: { origin: string; title?: string | null }) {
  return (
    <StatusBadge mono tone={originTone(origin)} title={title ?? undefined}>
      {origin}
    </StatusBadge>
  );
}
