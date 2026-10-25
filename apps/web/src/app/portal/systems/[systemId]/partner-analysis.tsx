"use client";

/** Analyse PARTNER côté fournisseur : clients et fournisseurs du système. Même moteur et mêmes questions que
 *  l'analyse du catalogue (D8, domaine « partners ») ; aucune donnée technique. Une fois complète : « Continue
 *  to export » ouvre l'export (aperçu, fichier). Rien n'est lu ici en dehors de la structure du système. */
import { CheckCircle2, Circle, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";

import { ErrorBox, LoadingRows } from "@/components/registry";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type SupplierSystem,
  useAnalyzeCatalog,
  useCatalogSummary,
  usePartnerTargets,
  useSupplierQuestions,
} from "@/lib/api/supplier-hooks";
import { cn } from "@/lib/utils";

import { Questions } from "./catalog-analysis";

const STEPS = ["Connection checked", "Reading partner structure", "Identifying partner fields"];

/** Systèmes connectés qui peuvent recevoir ces partenaires (liste venue de l'API : aucun nom de système ici). */
function Targets({ systemId }: { systemId: string }) {
  const targets = usePartnerTargets(systemId);
  // une liste illisible n'est jamais masquée en silence (ex. API pas à jour) : l'erreur est affichée
  if (targets.error)
    return (
      <div className="space-y-2 border-t border-border pt-4">
        <div className="eyebrow">Send to a connected system</div>
        <ErrorBox error={targets.error} />
      </div>
    );
  if (!targets.data?.length) return null;
  return (
    <div className="space-y-2 border-t border-border pt-4">
      <div className="eyebrow">Send to a connected system</div>
      <div className="flex flex-wrap gap-2">
        {targets.data.map((t) => (
          <Link
            key={t.system_id}
            href={`/portal/systems/${systemId}/partners/${t.system_id}`}
            className={buttonVariants({ variant: "outline" })}
          >
            Prepare for {t.name}
          </Link>
        ))}
      </div>
      <p className="meta">Nothing is sent until you select records and confirm.</p>
    </div>
  );
}

/** Système dont le sens des données est DÉCLARÉ par son interface publiée (`declared`, venu de l'API) : rien à
 *  analyser, les partenaires sont lus à la demande. Aucun nom ni condition de système ici. */
export function DeclaredPartners({ system }: { system: SupplierSystem }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Customers &amp; suppliers</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2 text-success">
          <CheckCircle2 className="size-5" />
          <span className="font-semibold">Ready to export</span>
        </div>
        <p>
          The meaning of this system&apos;s customer and supplier data is defined by its published interface: no
          analysis is needed. Your partners are read when you preview an export; nothing is stored or changed.
        </p>
        <div className="flex flex-wrap gap-2">
          <Link href={`/portal/exports?system=${system.id}`} className={buttonVariants()}>
            Continue to export
          </Link>
        </div>
        <Targets systemId={system.id} />
      </CardContent>
    </Card>
  );
}

export function PartnerAnalysis({ system }: { system: SupplierSystem }) {
  const summary = useCatalogSummary(system.id, "partners");
  const analyze = useAnalyzeCatalog(system.id, "partners");
  const status = summary.data?.status;
  const questionsOpen = status === "questions_open";
  const questions = useSupplierQuestions(system.id, questionsOpen && !analyze.isPending, "partners");

  if (analyze.isPending) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Analyzing your customers and suppliers…</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            {STEPS.map((step) => (
              <li key={step} className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> {step}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Customers &amp; suppliers</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {summary.error && <ErrorBox error={summary.error} />}
        {analyze.error && (
          <div className="space-y-3">
            <div className="rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">
              We couldn&apos;t analyze your system. Please check the connection and try again.
            </div>
            <Button onClick={() => analyze.mutate()}>Try again</Button>
          </div>
        )}
        {!summary.data && !summary.error && <LoadingRows rows={2} />}

        {!analyze.error && summary.data && (status === "awaiting_setup" || status === "not_analyzed") && (
          <div className="space-y-4">
            <p>Analyze your system to identify your customers and suppliers, so they can be exported.</p>
            <Button onClick={() => analyze.mutate()}>Analyze partners</Button>
          </div>
        )}

        {!analyze.error && questionsOpen && (
          <div className="space-y-4">
            {questions.error && <ErrorBox error={questions.error} />}
            {!questions.data ? (
              <LoadingRows rows={3} />
            ) : (
              <Questions systemId={system.id} questions={questions.data} domain="partners" />
            )}
          </div>
        )}

        {!analyze.error && status === "complete" && summary.data && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-success">
              <CheckCircle2 className="size-5" />
              <span className="font-semibold">Partner analysis complete</span>
            </div>
            <ul className="grid gap-2 sm:grid-cols-2">
              {summary.data.structure.map((s) => (
                <li key={s.label} className={cn("flex items-center gap-2", !s.present && "text-muted-foreground")}>
                  {s.present ? <CheckCircle2 className="size-4 text-success" /> : <Circle className="size-4" />}
                  {s.label} {s.present ? "detected" : "not found"}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-2">
              <Link href={`/portal/exports?system=${system.id}`} className={buttonVariants()}>
                Continue to export
              </Link>
              <Button variant="outline" onClick={() => analyze.mutate()}>
                <RefreshCw /> Re-analyze
              </Button>
            </div>
            <Targets systemId={system.id} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
