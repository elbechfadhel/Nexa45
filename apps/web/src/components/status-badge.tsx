/** Badges de statut de la charte GO45. Les tons sont sémantiques et lus dans les tokens de globals.css
 *  (--success, --danger...), donc justes dans les deux thèmes. La couleur renforce le libellé, elle ne le
 *  remplace jamais. */
import { cn } from "@/lib/utils";

export type Tone = "success" | "danger" | "warning" | "changed" | "info" | "third-party" | "neutral";

// classes écrites en entier : Tailwind ne détecte pas les noms construits dynamiquement
const TONE: Record<Tone, { text: string; dot: string; tint: string }> = {
  success: { text: "text-success", dot: "bg-success", tint: "border-success/35 bg-success/10" },
  danger: { text: "text-danger", dot: "bg-danger", tint: "border-danger/35 bg-danger/10" },
  warning: { text: "text-warning", dot: "bg-warning", tint: "border-warning/35 bg-warning/10" },
  changed: { text: "text-changed", dot: "bg-changed", tint: "border-changed/35 bg-changed/10" },
  info: { text: "text-info", dot: "bg-info", tint: "border-info/35 bg-info/10" },
  "third-party": { text: "text-third-party", dot: "bg-third-party", tint: "border-third-party/35 bg-third-party/10" },
  neutral: { text: "text-neutral", dot: "bg-neutral", tint: "border-neutral/35 bg-neutral/10" },
};

export const toneDot = (tone: Tone) => TONE[tone].dot;

/** Point à GAUCHE, texte coloré, sans fond : statuts dans le texte (Connected, Last test OK). */
export function StatusBadge({
  tone,
  children,
  title,
  mono = false,
  className,
}: {
  tone: Tone;
  children: React.ReactNode;
  title?: string;
  mono?: boolean;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex w-fit shrink-0 items-center gap-1.5 text-xs font-semibold whitespace-nowrap",
        mono && "font-mono text-[11px]",
        TONE[tone].text,
        className,
      )}
    >
      <span aria-hidden className={cn("size-2 shrink-0 rounded-full", TONE[tone].dot)} />
      {children}
    </span>
  );
}

/** Pastille arrondie, point à DROITE du libellé.
 *  tinted : texte et fond légers de la couleur (Added, +33, Latest) ;
 *  neutral : texte normal, bordure grise, seul le point est coloré (environnement, capabilities). */
export function Pill({
  tone,
  children,
  variant = "tinted",
  mono = false,
  title,
  className,
}: {
  tone: Tone;
  children: React.ReactNode;
  variant?: "tinted" | "neutral";
  mono?: boolean;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-6 w-fit shrink-0 items-center justify-between gap-2 rounded-full border px-2.5 text-xs font-semibold whitespace-nowrap",
        variant === "tinted" ? [TONE[tone].tint, TONE[tone].text] : "border-border bg-card text-foreground",
        mono && "font-mono text-[11px]",
        className,
      )}
    >
      {children}
      <span aria-hidden className={cn("size-2 shrink-0 rounded-full", TONE[tone].dot)} />
    </span>
  );
}

/** prod en rouge (prudence), pré-production en ambre, test / dev en bleu ; valeur inconnue en gris. */
export function environmentTone(env: string): Tone {
  const e = env.toLowerCase();
  if (e.startsWith("prod")) return "danger";
  if (["staging", "uat", "preprod"].includes(e)) return "warning";
  if (["test", "qa", "sandbox"].includes(e) || e.startsWith("dev")) return "info";
  return "neutral";
}

/** Statuts d'instance posés par le Core : draft → connected | connection_failed → discovered. */
export function instanceStatusTone(status: string): Tone {
  if (status === "connected" || status === "discovered") return "success";
  if (status.endsWith("failed")) return "danger";
  return "neutral";
}
