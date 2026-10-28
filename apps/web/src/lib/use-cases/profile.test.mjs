// Profil d'affichage (node --test) : `full` ne change rien ; `demo` ne garde que les use cases validés.
import assert from "node:assert/strict";
import { test } from "node:test";

import { capabilityRows } from "./capabilities.mjs";
import { listUseCases, USE_CASES } from "./catalog.mjs";
import { DEMO_VALIDATED, linkVisible, uiProfile, visibleRows, visibleUseCases } from "./profile.mjs";

test("sans configuration, le profil est full et rien n'est masqué", () => {
  assert.equal(process.env.NEXT_PUBLIC_UIE_PROFILE === "demo" ? "demo" : "full", uiProfile());
  assert.equal(visibleUseCases(USE_CASES, "full"), USE_CASES);
  assert.ok(linkVisible("/portal/wizard", USE_CASES, "full"));
  const rows = capabilityRows(USE_CASES, ["odoo", "lexware", "datev"]);
  assert.equal(visibleRows(rows, "full"), rows);
});

test("démo : seuls les use cases disponibles ou validés restent, affichés disponibles", () => {
  const shown = visibleUseCases(USE_CASES, "demo");
  assert.ok(shown.length > 0);
  for (const u of shown) {
    assert.equal(u.status, "available");
    const original = USE_CASES.find((x) => x.id === u.id);
    assert.ok(original?.status === "available" || DEMO_VALIDATED.includes(u.id));
  }
  const ids = shown.map((u) => u.id);
  assert.ok(ids.includes("migration.wizard.datev")); // Prepare data for DATEV : validé
  assert.ok(!ids.includes("migration.wizard")); // Smart export : expérimental
  assert.ok(!ids.some((id) => USE_CASES.find((u) => u.id === id)?.status === "coming_soon"));
  assert.ok(!listUseCases("supplier", shown).some((u) => u.id === "accounting.export.datev"));
  for (const id of DEMO_VALIDATED) assert.ok(USE_CASES.some((u) => u.id === id), `${id} : use case inconnu`);
});

test("démo : un lien du header qui ne mène qu'à un use case masqué disparaît", () => {
  assert.ok(!linkVisible("/portal/wizard", USE_CASES, "demo")); // Smart export
  assert.ok(linkVisible("/portal/prepare-datev", USE_CASES, "demo"));
  assert.ok(linkVisible("/portal", USE_CASES, "demo")); // page sans use case : gardée
  assert.ok(linkVisible("/portal/capabilities", USE_CASES, "demo"));
});

test("démo : Capabilities ne dit jamais « Not implemented » d'une direction masquée", () => {
  const rows = capabilityRows(listUseCases("supplier", visibleUseCases(USE_CASES, "demo")), ["odoo", "lexware", "datev"]);
  assert.ok(visibleRows(rows, "demo").every((r) => r.useCase));
});
