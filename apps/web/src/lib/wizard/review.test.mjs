// Tests de « Check the mapping » de l'AI Migration Wizard (node --test) : logique de revue et garde-fous de la page.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { initialDecisions } from "./flow.mjs";
import {
  answerLabel,
  clip,
  currencyCode,
  currentDecision,
  currentQuestion,
  EXPERT_EXAMPLE_MAX,
  expertExamples,
  looksStructured,
  mappingSummary,
  notImportedGroups,
  openQuestions,
  problemGroups,
  reviewGroups,
  rowsText,
  simpleExamples,
  withChoice,
} from "./review.mjs";

/** @param {Partial<import("./flow.mjs").ColumnMapping> & { position: number }} c */
const col = (c) => ({
  header: `Column ${c.position}`,
  filled: 1000,
  examples: [],
  status: "unmatched",
  target: null,
  confidence: null,
  probably_not_needed: false,
  ...c,
});

/** Un catalogue de 1 000 lignes et 55 colonnes : 40 reconnues, 3 incertaines, 8 inutiles, 2 inconnues, 2 vides. */
function bigFile() {
  const columns = [];
  for (let i = 0; i < 40; i++)
    columns.push(col({ position: i, status: i % 2 ? "matched" : "ai_match", target: `f${i}` }));
  for (let i = 40; i < 43; i++)
    columns.push(col({ position: i, status: "to_confirm", target: `f${i}`, confidence: 75 }));
  for (let i = 43; i < 51; i++) columns.push(col({ position: i, probably_not_needed: true }));
  for (let i = 51; i < 53; i++) columns.push(col({ position: i }));
  for (let i = 53; i < 55; i++) columns.push(col({ position: i, status: "empty", filled: 0 }));
  return columns;
}

test("summary of a 55-column file: only the uncertain columns need a decision", () => {
  const columns = bigFile();
  const decisions = initialDecisions(columns);
  const groups = reviewGroups(columns, decisions, new Set());
  assert.deepEqual(mappingSummary(1000, groups, decisions), {
    rows: 1000,
    automatic: 40,
    chosen: 0,
    toConfirm: 3,
    notImported: 10,
  });
  // chaque colonne est dans un groupe et un seul : rien ne disparaît
  const all = [...groups.pending, ...groups.mapped, ...groups.notImported, ...groups.empty].map((c) => c.position);
  assert.deepEqual(
    all.sort((a, b) => a - b),
    columns.map((c) => c.position),
  );
});

test("decisions are asked one by one and an ambiguous mapping is never confirmed by itself", () => {
  const columns = bigFile();
  const decisions = initialDecisions(columns);
  const confirmed = new Set();
  let d = currentDecision(columns, confirmed);
  assert.equal(d.column?.position, 40);
  assert.deepEqual([d.index, d.total], [1, 3]);
  // une incertaine reste à décider tant que l'utilisateur n'a rien fait, même proposée avec une cible
  assert.equal(reviewGroups(columns, decisions, confirmed).pending.length, 3);
  confirmed.add(40);
  d = currentDecision(columns, confirmed);
  assert.deepEqual([d.column?.position, d.index, d.total], [41, 2, 3]);
  confirmed.add(41).add(42);
  assert.equal(currentDecision(columns, confirmed).column, null);
});

test("value decisions come from the server: one at a time, by column and kind, never answered by themselves", () => {
  /** @param {string} key @param {string | null} [answer] @param {string | null} [suggested] */
  const q = (key, answer = null, suggested = null) => ({
    key,
    kind: key.split(":")[0],
    title: `Question ${key}`,
    help: "",
    count: 10,
    options: [
      { value: "a", label: "Option A", examples: [["1,234", "1234"]] },
      { value: "b", label: "Option B" },
    ],
    suggested,
    answer,
  });
  // une suggestion (règle ou IA) ne vaut jamais réponse
  const questions = [q("list:3", "a"), q("unit:4", null, "a"), q("currency")];
  assert.deepEqual(
    openQuestions(questions).map((x) => x.key),
    ["unit:4", "currency"],
  );
  const step = currentQuestion(questions);
  assert.deepEqual([step.question?.key, step.index, step.total], ["unit:4", 2, 3]);
  assert.equal(currentQuestion([q("x", "a")]).question, null);
  // réponse, puis « Change » : la question est reposée
  let choices = withChoice({}, "unit:4", "b");
  assert.deepEqual(choices, { "unit:4": "b" });
  choices = withChoice(choices, "unit:4", null);
  assert.deepEqual(choices, {});
  assert.equal(answerLabel(q("list:3", "a")), "Option A");
  assert.equal(answerLabel({ ...q("currency"), answer: "CHF" }), "CHF"); // autre code saisi
  assert.equal(currencyCode(" eur "), "EUR");
  assert.equal(currencyCode("€"), null);
});

test("correcting an automatic match moves it out of the automatic count, never out of sight", () => {
  const columns = bigFile();
  const decisions = { ...initialDecisions(columns), 0: null, 1: "f40" };
  const groups = reviewGroups(columns, decisions, new Set([0, 1]));
  const summary = mappingSummary(1000, groups, decisions);
  assert.equal(summary.automatic, 38);
  assert.equal(summary.chosen, 1);
  assert.equal(summary.notImported, 11);
  const yours = notImportedGroups(groups.notImported).find((g) => g.reason === "your_choice");
  assert.deepEqual(
    yours?.columns.map((c) => c.position),
    [0],
  );
});

test("not imported columns are grouped and named, unrecognized ones first", () => {
  const columns = bigFile();
  const decisions = { ...initialDecisions(columns), 40: null };
  const groups = reviewGroups(columns, decisions, new Set([40]));
  const byReason = notImportedGroups(groups.notImported);
  assert.deepEqual(
    byReason.map((g) => [g.title, g.columns.length]),
    [
      ["Not recognized", 2],
      ["Not imported by your choice", 1],
      ["Probably not needed", 8],
    ],
  );
});

test("simple mode hides long structured values and clips long text", () => {
  const json = JSON.stringify({ attributes: Array.from({ length: 20 }, (_, i) => ({ name: `a${i}`, value: i })) });
  const long = "x".repeat(200);
  const shown = simpleExamples([json, "", "Red", long, "Blue", "Green"]);
  assert.deepEqual(shown.length, 3);
  assert.equal(shown[0], "Red");
  assert.ok(shown[1].endsWith("…") && shown[1].length === 60);
  assert.ok(!shown.some((e) => looksStructured(e)));
  assert.deepEqual(simpleExamples(['{"a":1}']), ['{"a":1}']); // une valeur courte reste lisible
});

test("expert mode clips examples at 150 characters with « Show more »", () => {
  const long = "y".repeat(400);
  const closed = expertExamples(["short", long], false);
  assert.equal(closed.clipped, true);
  assert.equal(closed.examples[1].length, EXPERT_EXAMPLE_MAX);
  assert.deepEqual(expertExamples(["short", long], true).examples, ["short", long]);
  assert.equal(expertExamples(["short"], false).clipped, false);
  assert.deepEqual(clip("abc", 3), { text: "abc", clipped: false });
});

test("the mapping screen: decisions one by one in Simple, every column in Expert, choices kept across modes", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const page = readFileSync(join(here, "..", "..", "app", "portal", "wizard", "page.tsx"), "utf8");
  assert.match(page, /currentDecision\(/);
  assert.match(page, /Decision \{decision\.index\} of \{decision\.total\}/);
  assert.match(page, /Show details/);
  assert.match(page, /Check automatic matches/);
  assert.match(page, /Show more/);
  assert.match(page, /simpleExamples\(/);
  assert.match(page, /expertExamples\(/);
  // le changement de mode ne remonte pas la section du fichier : décisions et confirmations gardées
  assert.match(page, /<FileSection\s+key=\{proposal\.title\}/);
  assert.doesNotMatch(page, /key=\{[^}]*mode[^}]*\}/);
  // aucune confirmation automatique : « Prepare » attend la fin des décisions
  assert.match(page, /disabled=\{!input \|\| pending\.length > 0/);
  assert.doesNotMatch(page, /Amazon|Odoo/);
  // décisions sur les valeurs : posées par le serveur, une à la fois en Simple ; jamais de règle ni de devise
  // en dur dans la page ; une suggestion n'est jamais appliquée seule ; avant → après montré en Expert
  assert.match(page, /Decisions about your values/);
  assert.match(page, /currentQuestion\(/);
  assert.match(page, /Not applied until you/);
  assert.match(page, /Before → after/);
  assert.match(page, /Values not written/);
  assert.match(page, /expert \|\| openValues\.length === 0/); // Simple : fichier préparé après les décisions
  assert.doesNotMatch(page, /"(USD|EUR|INR|GBP|pounds|kg)"/);
  assert.doesNotMatch(page, /"(last|path|dmy|mdy|point|comma)"/);
});

test("problems: rows in plain words, in two groups, nothing hidden", () => {
  assert.equal(rowsText([4], 1), "Row 4");
  assert.equal(rowsText([2, 5, 9], 3), "Rows 2, 5, 9");
  assert.equal(rowsText([1, 2, 3, 4], 40, 2), "Rows 1, 2 and 38 more");
  assert.equal(rowsText([], 3), ""); // problème du fichier entier (aucune colonne de prix)
  const groups = problemGroups([
    { kind: "barcode_invalid", severity: "not_written", rows: [3], count: 1, message: "m", fix: "f" },
    { kind: "missing_price", severity: "to_check", rows: [2], count: 1, message: "m", fix: "f" },
    { kind: "invalid", rows: [1], count: 1, message: "m" }, // ancienne forme : non écrite
    { kind: "left_out", severity: "not_written", rows: [5], count: 1, message: "m" },
  ]);
  assert.deepEqual(
    [groups.notWritten.map((i) => i.kind), groups.toCheck.map((i) => i.kind)],
    [["barcode_invalid", "invalid", "left_out"], ["missing_price"]],
  );
});
