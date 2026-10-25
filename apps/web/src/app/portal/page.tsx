"use client";

/** Overview fournisseur : « Que voulez-vous faire ? ». Générée depuis le catalogue des use cases (audience
 *  supplier) : simple, orientée métier, sans concept interne (Registry, snapshots, Compare). */
import { OtherOrganizations, PortalGate } from "@/components/portal";
import { UseCaseSections } from "@/components/use-case-overview";
import { listUseCases } from "@/lib/use-cases/catalog.mjs";

export default function PortalHome() {
  return (
    <PortalGate>
      {(org, me) => (
        <div className="page-stack">
          <div className="space-y-1.5">
            <h1 className="hero-badge">What would you like to do?</h1>
          </div>
          <UseCaseSections useCases={listUseCases("supplier")} variant="supplier" />
          <OtherOrganizations me={me} current={org} />
        </div>
      )}
    </PortalGate>
  );
}
