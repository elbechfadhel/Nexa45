import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  accountsToReview,
  documentTypeLabel,
  groupOptions,
  optionKind,
  rateLabel,
  selectionSummary,
} from "./api-target.mjs";

test("options are pre-filtered by the expected kind and grouped", () => {
  const options = [
    { id: "1", label: "Warenverkäufe", group: "Einnahmen", kind: "income" },
    { id: "2", label: "Dienstleistung", group: "Einnahmen", kind: "income" },
    { id: "3", label: "Bürobedarf", group: "Sonstige Ausgaben", kind: "outgo" },
  ];
  assert.deepEqual(
    groupOptions(options, optionKind("customer")).map((g) => [g.group, g.options.map((o) => o.label)]),
    [["Einnahmen", ["Dienstleistung", "Warenverkäufe"]]],
  );
  assert.equal(groupOptions(options, optionKind("supplier"))[0].options[0].label, "Bürobedarf");
  assert.equal(groupOptions(options, null).length, 2);
});

test("labels are presentation only", () => {
  assert.equal(documentTypeLabel("VENDOR_BILL"), "Vendor bill");
  assert.equal(rateLabel("19.0"), "19 %");
  assert.equal(rateLabel(null), "—");
  assert.deepEqual(accountsToReview([{ choice_id: null }, { choice_id: "x", problem: "gone" }, { choice_id: "y" }]), {
    missing: 1,
    problems: 1,
  });
});

test("the accounting API target page names no system and sends only after an explicit confirmation", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const dir = join(here, "..", "..", "app", "portal", "systems", "[systemId]", "accounting", "[targetId]");
  const view = readFileSync(join(dir, "api-target-view.tsx"), "utf8");
  assert.doesNotMatch(view, /odoo|lexware|datev|voucher/i);
  assert.doesNotMatch(view, /api\.(POST|PUT|DELETE)|method:\s*"(POST|PUT|DELETE)"/); // jamais d'appel direct
  assert.match(view, /Confirm and send/);
  assert.match(view, /randomUUID\(\)/); // clé d'envoi générée à l'ouverture de la confirmation
  assert.match(view, /selectableRefs\(data\.documents\)/); // la page ne décide pas de ce qui est envoyable
  assert.match(view, /disabled=\{!d\.sendable\}/); // non envoyable = non sélectionnable
  assert.match(view, /if \(!confirmKey \|\| send\.isPending\) return;/); // anti-double-clic
  const hooks = readFileSync(join(here, "..", "api", "accounting-api-target-hooks.ts"), "utf8");
  assert.match(hooks, /useSendAccountingDocuments[\s\S]{0,200}retry: false/); // jamais de nouvel essai automatique
});

test("the confirmation lists numbers, partners and amounts, and totals them per currency", () => {
  const docs = [
    { source_reference: "a", number: "RE/1", partner: "Alpha", total_amount: "404.60", currency: "EUR", sendable: true },
    { source_reference: "b", number: "RE/2", partner: "Beta", total_amount: "101.15", currency: "EUR", sendable: true },
    { source_reference: "c", number: "RE/3", partner: "Gamma", total_amount: "9.99", currency: "EUR", sendable: false },
  ];
  const s = selectionSummary(docs, ["a", "b"]);
  assert.deepEqual(s.rows.map((r) => [r.number, r.partner, r.amount]), [["RE/1", "Alpha", "404.60"], ["RE/2", "Beta", "101.15"]]);
  assert.deepEqual(s.totals, [{ currency: "EUR", amount: "505.75" }]);
});

test("the list of connected target systems never disappears silently when it cannot be read", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const dir = join(here, "..", "..", "app", "portal", "systems", "[systemId]");
  for (const file of ["accounting-analysis.tsx", "partner-analysis.tsx"]) {
    const src = readFileSync(join(dir, file), "utf8");
    // régression : API pas à jour (404) → la section « Send to a connected system » était masquée
    assert.match(src, /if \(targets\.error\)[\s\S]{0,200}<ErrorBox error=\{targets\.error\}/, file);
    assert.doesNotMatch(src, /Prepare for (Lexware|Odoo)/i, file); // aucun bouton codé en dur : la liste vient de l'API
  }
});

test("declared accounting reuses the read / prepare flow, chosen by the API, never by a system name", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const dir = join(here, "..", "..", "app", "portal", "systems", "[systemId]");
  const view = readFileSync(join(dir, "system-view.tsx"), "utf8");
  // la carte dépend de `declared` (API), jamais du code d'un système
  assert.match(view, /s\.declared\.includes\("accounting"\) \? <DeclaredAccounting/);
  assert.doesNotMatch(view, /system_code\s*===|"lexware"|"odoo"/);
  const acc = readFileSync(join(dir, "accounting-analysis.tsx"), "utf8");
  const declared = acc.slice(acc.indexOf("export function DeclaredAccounting"), acc.indexOf("function AccountingReview"));
  assert.match(declared, /<AccountingReview systemId=\{system\.id\} \/>/); // même parcours, aucune analyse
  assert.doesNotMatch(declared, /useAnalyzeCatalog|Analyze accounting|lexware|odoo/i);
});
