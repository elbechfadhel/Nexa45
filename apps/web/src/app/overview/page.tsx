"use client";

/** Overview admin : « Que peut-on faire avec GO45 Integration Engine, et comment le montrer ? ». Générée depuis
 *  le catalogue des use cases (audience admin) ; les use cases du portail fournisseur sont listés à part, issus
 *  du même catalogue, sans démarrage depuis la console (ils exigent un compte fournisseur). */
import { UseCaseSections } from "@/components/use-case-overview";
import { listOtherUseCases, listUseCases } from "@/lib/use-cases/catalog.mjs";

export default function AdminOverview() {
  const adminUseCases = listUseCases("admin");
  return (
    <div className="page-stack">
      <div className="space-y-1.5">
        <h1 className="hero-badge">Overview</h1>
        <p className="page-description">What can you do with GO45 Integration Engine, and how do you show it?</p>
      </div>
      <UseCaseSections useCases={adminUseCases} variant="admin" />
      <div className="space-y-1.5 border-t border-border pt-6">
        <h2 className="page-title text-2xl">In the supplier portal</h2>
        <p className="page-description">Demonstrate these with a supplier account (they run in the portal).</p>
      </div>
      <UseCaseSections
        useCases={listOtherUseCases("admin")}
        variant="admin"
        startable={false}
        note="Sign in as a supplier to demonstrate"
        // numérotation poursuivie après les cartes admin : 1 → 20 sur la page
        firstNumber={adminUseCases.length + 1}
      />
    </div>
  );
}
