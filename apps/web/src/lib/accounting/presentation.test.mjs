// Présentation des pièces comptables (phase 3.1) : vue Débit / Crédit du montant signé du CDM, solde renvoyé
// par le backend, titres de ligne, libellé « Eligible ». Lignes au format de l'API (`LineOut`), montants des
// pièces de démonstration d'accounting17 (inventées).
import assert from "node:assert/strict";
import test from "node:test";

import {
  balanceView,
  debitCredit,
  ELIGIBLE_LABEL,
  formatAbsolute,
  formatAmount,
  lineTitle,
  signOf,
} from "./presentation.mjs";

/** @param {{ kind: string, description: string | null, amount: string }[]} lines */
const view = (lines) => lines.map((l) => ({ title: lineTitle(l).title, ...debitCredit(l.amount) }));

test("signed amount → debit (positive) or credit (absolute value of a negative)", () => {
  assert.deepEqual(debitCredit("739.89"), { debit: "739.89", credit: null });
  assert.deepEqual(debitCredit("-360.0"), { debit: null, credit: "360.00" });
  assert.deepEqual(debitCredit(-1404), { debit: null, credit: "1,404.00" });
  assert.deepEqual(debitCredit("0.00"), { debit: null, credit: null });
  assert.deepEqual(debitCredit("-0.0"), { debit: null, credit: null });
});

test("amounts are formatted from the decimal text, without floating point drift", () => {
  assert.equal(formatAbsolute("1237.6"), "1,237.60");
  assert.equal(formatAbsolute("-85.39"), "85.39");
  assert.equal(formatAbsolute("1234567"), "1,234,567.00");
  assert.equal(formatAbsolute("0.1"), "0.10");
  assert.equal(formatAmount("-12", "EUR"), "-12.00 EUR");
  assert.equal(formatAmount(null, "EUR"), "—");
  assert.equal(signOf("-0.00"), 0);
});

test("customer invoice: revenue and tax on the credit side, amount due on the debit side", () => {
  const rows = view([
    { kind: "Item", description: "Beratung (Stunde)", amount: "-360.0" },
    { kind: "Tax", description: "19%", amount: "-104.5" },
    { kind: "Amount due", description: "RE/2026/00002", amount: "739.89" },
  ]);
  assert.deepEqual(rows, [
    { title: "Beratung (Stunde)", debit: null, credit: "360.00" },
    { title: "19%", debit: null, credit: "104.50" },
    { title: "RE/2026/00002", debit: "739.89", credit: null },
  ]);
});

test("customer credit note: the CDM signs are reversed, the view follows them", () => {
  const rows = view([
    { kind: "Item", description: "Holzplatte Eiche", amount: "85.0" },
    { kind: "Amount due", description: "RRE/2026/00001", amount: "-101.15" },
  ]);
  assert.deepEqual(rows.map((r) => [r.debit, r.credit]), [["85.00", null], [null, "101.15"]]);
});

test("vendor bill: expense and input tax on the debit side, payable on the credit side", () => {
  const rows = view([
    { kind: "Item", description: "Holzplatte Eiche", amount: "1040.0" },
    { kind: "Tax", description: "19% I", amount: "197.6" },
    { kind: "Amount due", description: null, amount: "-1237.6" },
  ]);
  assert.deepEqual(rows, [
    { title: "Holzplatte Eiche", debit: "1,040.00", credit: null },
    { title: "19% I", debit: "197.60", credit: null },
    { title: "Amount due", debit: null, credit: "1,237.60" },
  ]);
});

test("vendor credit note: expense on the credit side, payable on the debit side", () => {
  const rows = view([
    { kind: "Item", description: "Rücksendung", amount: "-104.0" },
    { kind: "Amount due", description: null, amount: "123.76" },
  ]);
  assert.deepEqual(rows.map((r) => [r.debit, r.credit]), [[null, "104.00"], ["123.76", null]]);
});

test("balance comes from the backend: zero is balanced, anything else is not", () => {
  assert.deepEqual(balanceView("0.00", "EUR"), { text: "0.00 EUR", balanced: true });
  assert.deepEqual(balanceView("0", "EUR"), { text: "0.00 EUR", balanced: true });
  assert.deepEqual(balanceView("-0.01", "EUR"), { text: "-0.01 EUR", balanced: false });
  assert.deepEqual(balanceView("12.5", null), { text: "12.50", balanced: false });
  assert.deepEqual(balanceView(null, "EUR"), { text: "—", balanced: false });
});

test("line titles use the CDM description when present, never an invented one", () => {
  assert.deepEqual(lineTitle({ kind: "Item", description: "Beratung Planung" }), {
    title: "Beratung Planung",
    subtitle: "Item",
  });
  assert.deepEqual(lineTitle({ kind: "Amount due", description: null }), { title: "Amount due", subtitle: null });
  assert.deepEqual(lineTitle({ kind: "Tax", description: "  " }), { title: "Tax", subtitle: null });
});

test("phase 3 wording: eligible for mapping, not ready for export", () => {
  assert.equal(ELIGIBLE_LABEL, "Eligible");
  assert.doesNotMatch(ELIGIBLE_LABEL, /export/i);
});
