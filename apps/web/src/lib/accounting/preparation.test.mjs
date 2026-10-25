// Présentation de la préparation DATEV (phase 4) : statuts, suggestion ≠ décision, cellules telles qu'écrites,
// sens et clé de taxe. Données au format de l'API (BookingOut, MappingOut) : écritures de RE/2026/00002 et d'un
// avoir fournisseur des pièces de démonstration (inventées) ; clés de taxe = valeurs de TEST.
import assert from "node:assert/strict";
import test from "node:test";

import {
  cell,
  cellText,
  countsSentence,
  mappingStatus,
  mappingTarget,
  partnerSide,
  taxDecision,
  targetStatus,
} from "./preparation.mjs";

const B1 = {
  tax: "19%",
  tax_key: null,
  tax_no_key: true,
  cells: [
    { field: "001", value: "654,50" },
    { field: "002", value: '"S"' },
    { field: "008", value: "8400" },
    { field: "011", value: '"RE/2026/00002"' },
  ],
};

test("target status is separate from source eligibility, unprepared documents are neutral", () => {
  assert.deepEqual(targetStatus("ready"), { label: "Ready", tone: "success" });
  assert.deepEqual(targetStatus("blocked"), { label: "Blocked", tone: "danger" });
  assert.deepEqual(targetStatus(null), { label: "Not prepared", tone: "neutral" });
});

test("a suggestion is shown as not confirmed and has no target value yet", () => {
  const suggested = { kind: "account", status: "suggested", suggestion: "8400", target_value: null };
  assert.equal(mappingStatus("suggested").label, "Suggested — not confirmed");
  assert.equal(mappingTarget(suggested), "—");
  assert.equal(mappingTarget({ kind: "account", status: "confirmed", target_value: "8400" }), "8400");
  assert.equal(mappingTarget({ kind: "tax", status: "confirmed", no_value: true }), "No tax key");
  assert.equal(mappingStatus("missing").tone, "warning");
});

test("cells are displayed as written by the format, without its quotes", () => {
  assert.equal(cell(B1, "001"), "654,50");
  assert.equal(cell(B1, "002"), "S");
  assert.equal(cell(B1, "011"), "RE/2026/00002");
  assert.equal(cell(B1, "009"), "—");
  assert.equal(cellText('"Say ""hi"""'), 'Say "hi"');
});

test("direction and tax decision of customer invoices and vendor credit notes", () => {
  assert.equal(partnerSide("debit", "customer"), "Debit on the customer account");
  assert.equal(partnerSide("debit", "supplier"), "Debit on the supplier account"); // avoir fournisseur
  assert.equal(partnerSide("credit", "supplier"), "Credit on the supplier account"); // facture fournisseur
  assert.equal(taxDecision(B1), "19% → No tax key");
  assert.equal(taxDecision({ tax: "7%", tax_key: "1234" }), "7% → 1234");
  assert.equal(taxDecision({ tax: "7%" }), "7% → not decided");
  assert.equal(taxDecision({ tax: null }), "No tax on this line");
});

test("counts keep eligible and ready apart", () => {
  const sentence = countsSentence({ eligible: 10, ready: 0, warnings: 0, blocked: 11, not_prepared: 2 });
  assert.equal(sentence, "10 eligible in your system · 0 ready · 0 with warnings · 11 blocked · 2 not prepared");
});

// --- phase 5 : fichier ---------------------------------------------------------------------------------
import { EXPORT_POLICY, fileSentence, fileSize, rowSummary } from "./preparation.mjs";

test("a file row is summarised from its written cells (golden RE/2026/00002, row 1)", () => {
  const row = {
    cells: [
      { field: "001", value: "654,50" },
      { field: "002", value: '"S"' },
      { field: "003", value: '"EUR"' },
      { field: "007", value: "10003" },
      { field: "008", value: "8400" },
      { field: "010", value: "1501" },
      { field: "011", value: '"RE/2026/00002"' },
      { field: "014", value: '"Lena Hoffmann, RE/2026/00002"' },
    ],
  };
  assert.deepEqual(rowSummary(row), {
    amount: "654,50",
    side: "S",
    account: "10003",
    counter: "8400",
    taxKey: "—",
    date: "1501",
    reference: "RE/2026/00002",
    text: "Lena Hoffmann, RE/2026/00002",
  });
});

test("the export sentence tells the truth about what is written", () => {
  const counts = { exported_documents: 9, excluded_documents: 4, rows: 10, warnings: 1 };
  assert.equal(fileSentence(counts), "9 documents (1 with warnings) · 10 booking rows · 4 not exported");
  assert.equal(fileSentence({ exported_documents: 1, excluded_documents: 0, rows: 1, warnings: 0 }), "1 document · 1 booking row · 0 not exported");
  assert.match(EXPORT_POLICY, /Blocked, draft and cancelled documents are never written/);
  assert.equal(fileSize(900), "900 B");
  assert.equal(fileSize(2048), "2.0 KB");
});

// --- D11 : historique d'export par pièce -----------------------------------------------------------------
import { changeLines, exportState, historySentence, nothingToExport } from "./preparation.mjs";

test("export history is a separate axis from the target status", () => {
  assert.deepEqual(exportState("new"), { label: "New", tone: "success" });
  assert.deepEqual(exportState("already_exported"), { label: "Already exported", tone: "neutral" });
  assert.deepEqual(exportState("changed_since_export"), { label: "Changed since export", tone: "danger" });
  assert.notEqual(exportState("new").label, targetStatus("ready").label);
});

test("first export: everything is new; second preview: nothing new, and the reason is said", () => {
  const first = { new: 9, already_exported: 0, changed_since_export: 0, will_be_exported: 9 };
  const second = { new: 0, already_exported: 9, changed_since_export: 0, will_be_exported: 0 };
  assert.equal(historySentence(first), "9 new · 0 already exported · 0 changed since export · 9 will be exported");
  assert.equal(nothingToExport(first), null);
  assert.match(nothingToExport(second), /No new documents to export: every exportable document .* was already exported/);
  assert.match(nothingToExport({ new: 0, already_exported: 0, changed_since_export: 1, will_be_exported: 0 }), /need your decision/);
  assert.equal(nothingToExport({ new: 0, already_exported: 0, changed_since_export: 0, will_be_exported: 0 }), "No document can be exported for this period.");
});

test("a changed document says which booking fields changed, never a hash", () => {
  const lines = changeLines([
    { booking: 2, label: "Gegenkonto (ohne BU-Schlüssel)", change: "changed" },
    { booking: 2, label: "BU-Schlüssel", change: "added" },
    { booking: 3, label: "Umsatz (ohne Soll/Haben-Kz)", change: "removed" },
  ]);
  assert.deepEqual(lines, [
    "Booking 2: Gegenkonto (ohne BU-Schlüssel) changed, BU-Schlüssel added",
    "Booking 3: Umsatz (ohne Soll/Haben-Kz) removed",
  ]);
});
