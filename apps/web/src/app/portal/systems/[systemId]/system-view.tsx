"use client";

/** Un système du fournisseur : nom, version, statut de connexion, faits sûrs du compte (jamais d'identifiant
 *  de connexion) et sa suppression. Ce que la page propose dépend de ce que le système SAIT FAIRE ici, d'après
 *  l'API (`provides` : domaines qu'il peut fournir ; `receives` : ce qu'il peut recevoir) — jamais d'après son
 *  nom : aucun système n'est « source » ou « cible » par nature. */
import { CheckCircle2 } from "lucide-react";
import Link from "next/link";

import { PortalGate } from "@/components/portal";
import { ErrorBox, formatDate, LoadingRows } from "@/components/registry";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type SupplierSystem, useSupplierSystem } from "@/lib/api/supplier-hooks";

import { ConnectionBadge } from "../connection-badge";
import { CatalogAnalysis } from "./catalog-analysis";
import { AccountingAnalysis, DeclaredAccounting } from "./accounting-analysis";
import { DeclaredPartners, PartnerAnalysis } from "./partner-analysis";
import { RemoveSystem } from "./remove-system";

export function SystemView({ systemId }: { systemId: string }) {
  return <PortalGate>{() => <System systemId={systemId} />}</PortalGate>;
}

function System({ systemId }: { systemId: string }) {
  const system = useSupplierSystem(systemId);
  if (system.error) return <ErrorBox error={system.error} />;
  if (!system.data) return <LoadingRows />;
  const s = system.data;
  const connected = s.connection.status === "connected";

  return (
    <div className="page-stack max-w-3xl">
      <div>
        <h1 className="hero-badge">{s.name}</h1>
      </div>

      {connected && (
        <div className="flex items-center gap-2 rounded-md border border-success/35 bg-success/10 p-3 text-success">
          <CheckCircle2 className="size-5 shrink-0" />
          Your system is connected successfully.
        </div>
      )}
      {s.connection.status === "failed" && s.connection.message && (
        <div className="rounded-md border border-danger/35 bg-danger/10 p-3 text-danger">{s.connection.message}</div>
      )}

      <Card size="sm">
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <Item label="System name">{s.name}</Item>
            <Item label="System">{s.system}</Item>
            <Item label="Connection">
              <ConnectionBadge system={s} />
              {s.connection.tested_at && (
                <span className="meta ml-2">checked {formatDate(s.connection.tested_at)}</span>
              )}
            </Item>
            {s.server_url && (
              <Item label="Server address">
                <span className="tech break-all">{s.server_url}</span>
              </Item>
            )}
            {s.database && (
              <Item label="Database">
                <span className="tech">{s.database}</span>
              </Item>
            )}
            {s.details.map((d) => (
              <Item key={d.label} label={d.label}>
                {d.value}
              </Item>
            ))}
          </dl>
        </CardContent>
      </Card>

      {s.provides.includes("product") && <CatalogAnalysis system={s} />}

      {connected &&
        s.provides.includes("partner") &&
        (s.declared.includes("partner") ? <DeclaredPartners system={s} /> : <PartnerAnalysis system={s} />)}

      {/* comptabilité déclarée (schéma fixe) : même parcours lecture / préparation, jamais l'analyse */}
      {connected &&
        s.provides.includes("accounting") &&
        (s.declared.includes("accounting") ? <DeclaredAccounting system={s} /> : <AccountingAnalysis system={s} />)}

      {s.receives.length > 0 && <Receives system={s} />}

      <RemoveSystem system={s} />
    </div>
  );
}

const RECEIVED: Record<string, string> = {
  partner: "Customers & suppliers",
  accounting: "Accounting documents (preview through the API only)",
};

/** Ce que ce système peut recevoir d'un autre système connecté (aperçu seulement pour l'instant). */
function Receives({ system }: { system: SupplierSystem }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>What this system can receive</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="list-disc space-y-1 pl-5">
          {system.receives.map((model) => (
            <li key={model}>{RECEIVED[model] ?? model}</li>
          ))}
        </ul>
        <p className="text-muted-foreground">
          Open the system that holds the data, complete its analysis, then choose “Prepare for {system.name}”.
          Nothing is sent to {system.name} until you select records and confirm.
        </p>
        <Link href="/portal/systems" className={buttonVariants({ variant: "outline" })}>
          Open my systems
        </Link>
      </CardContent>
    </Card>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="meta">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
