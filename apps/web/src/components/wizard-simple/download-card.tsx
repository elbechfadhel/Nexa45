"use client";

/** Téléchargement du fichier préparé en mode Simple, avec l'ordre d'import en mots simples quand le fichier
 *  contient des feuilles « (import first) » (ex. les catégories avant les articles). Rien n'est envoyé à un
 *  système ; les choix confirmés sont retenus par la mémoire de l'organisation au téléchargement. */
import { Download, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { PreparedFile, QuickStart } from "@/lib/api/wizard-hooks";
import { importSteps } from "@/lib/wizard/simple.mjs";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

export function DownloadCard({
  start,
  preview,
  learning,
  busy,
  downloading,
  error,
  onDownload,
}: {
  start: QuickStart;
  preview: PreparedFile;
  learning: boolean;
  busy: boolean;
  downloading: boolean;
  error: Error | null;
  onDownload: () => void;
}) {
  const { steps, notes } = importSteps(preview, start.data_type, start.target_label);
  return (
    <Card className="bg-transparent shadow-none">
      <CardContent className="space-y-4">
        <h2 className="item-title">{L.downloadHeading}</h2>
        <section className="space-y-2">
          <h3 className="eyebrow">{L.importOrderHeading}</h3>
          <ol className="list-decimal space-y-1 pl-5">
            {steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
          {notes.map((n) => (
            <p key={n} className="meta text-muted-foreground">
              {n}
            </p>
          ))}
          {preview.left_to_target.length > 0 && (
            <p className="meta text-muted-foreground">{L.targetDefaults(preview.left_to_target.join(", "))}</p>
          )}
        </section>
        {error && <p className="rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">{error.message}</p>}
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={onDownload} disabled={busy}>
            {downloading ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
            {downloading ? L.downloading : L.download}
          </Button>
          {learning && <span className="meta text-muted-foreground">{L.learnNote}</span>}
        </div>
      </CardContent>
    </Card>
  );
}
