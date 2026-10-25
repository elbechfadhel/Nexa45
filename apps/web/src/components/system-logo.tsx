/** Tuile d'un système (portail : Connect a system ; console : Systems ; Overview).
 *
 *  Un logo n'est affiché que s'il est LISTÉ ici (`LOGOS`) et présent dans `public/systems/` : aucun logo de
 *  marque n'est dessiné ni deviné. Tout autre système reçoit une tuile NEUTRE avec son nom en toutes lettres.
 *  C'est le cas de tous les systèmes tant que nous n'avons pas l'accord d'utiliser leur logo de marque. */
import { Database } from "lucide-react";

import { cn } from "@/lib/utils";

/** Code de système → extension du fichier `public/systems/<code>.<ext>`. */
const LOGOS: Record<string, "svg" | "png"> = {
  custom: "png",
};

export function SystemLogo({ code, name, className }: { code: string; name: string; className?: string }) {
  const ext = LOGOS[code];
  const box = cn(
    "flex size-14 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-border",
    className,
  );
  if (ext) {
    // .svg : logo détouré, posé sur une tuile claire ; .png : tuile complète (fond compris), bord à bord
    return (
      <span className={cn(box, ext === "svg" ? "bg-white/95 p-2" : "p-0")}>
        {/* fichier statique des assets du projet : pas d'optimisation next/image nécessaire */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={`/systems/${code}.${ext}`}
          alt={`${name} logo`}
          className={ext === "svg" ? "max-h-full max-w-full object-contain" : "size-full object-cover"}
        />
      </span>
    );
  }
  // tuile neutre : le nom, sur une largeur libre (jamais un logo de marque)
  return (
    <span
      className={cn(box, "w-auto bg-muted/60 px-2.5 text-xs font-semibold whitespace-nowrap text-muted-foreground")}
      aria-label={name}
    >
      {code === "custom" ? <Database className="size-6" /> : name}
    </span>
  );
}
