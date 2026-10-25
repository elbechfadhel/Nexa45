"use client";

/** Prepare data for DATEV (portail fournisseur) : le parcours des cabinets. RÉUTILISE le mode Simple du Wizard
 *  (`components/wizard-simple`) sans copie : seule la configuration `WIZARD_ENTRIES.datev` change (titre,
 *  introduction, cibles affichées). Aucun mode Expert sur cette page. */
import { PortalGate } from "@/components/portal";
import { SimpleWizard } from "@/components/wizard-simple/simple-wizard";
import { WIZARD_ENTRIES } from "@/lib/wizard/entries.mjs";

export default function PrepareDatevPage() {
  return <PortalGate>{() => <SimpleWizard entry={WIZARD_ENTRIES.datev} />}</PortalGate>;
}
