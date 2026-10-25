// Tests de la logique du parcours de l'AI Migration Wizard (node --test).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  answerFromFill,
  autoFills,
  readinessBanner,
  readinessLabel,
  mergeFills,
  earlyShown,
  answersOf,
  back,
  buildAnswer,
  canContinue,
  rememberChoices,
  dataTypeOf,
  decisionList,
  emptyMissingAnswer,
  fieldName,
  fillOf,
  filterChoices,
  FIRST_PAGE,
  initialDecisions,
  initialMissingAnswer,
  isSuggested,
  needsConfirmation,
  OTHER,
  pendingConfirmations,
  preparedBanner,
  progress,
  sectionChoices,
  takenTargets,
  verificationBanner,
  visibleChoices,
} from "./flow.mjs";

/** @type {any} */
const Q = {
  id: "target_system",
  topic: "target_system",
  title: "Which target system?",
  help: null,
  allow_other: true,
  choices: [
    {
      value: "registry:odoo",
      label: "Odoo",
      description: null,
      source: "registry",
    },
    {
      value: "sap_s4hana",
      label: "SAP S/4HANA",
      description: null,
      source: "ai",
    },
  ],
};

test("an answer keeps the choice and its source (Registry or AI)", () => {
  assert.deepEqual(buildAnswer(Q, "registry:odoo", ""), {
    question_id: "target_system",
    topic: "target_system",
    question: "Which target system?",
    value: "registry:odoo",
    label: "Odoo",
    source: "registry",
  });
  assert.equal(buildAnswer(Q, "sap_s4hana", "")?.source, "ai");
  assert.equal(buildAnswer(Q, "unknown", ""), null);
});

test("« Other » uses the typed text, never an empty answer", () => {
  assert.equal(buildAnswer(Q, OTHER, "  "), null);
  const a = buildAnswer(Q, OTHER, " Infor M3 ");
  assert.equal(a?.value, "Infor M3");
  assert.equal(a?.source, "user");
  assert.equal(buildAnswer({ ...Q, allow_other: false }, OTHER, "x"), null);
});

test("back restores the last question with its previous selection", () => {
  const a1 = /** @type {any} */ (buildAnswer(Q, "sap_s4hana", ""));
  const free = /** @type {any} */ (buildAnswer(Q, OTHER, "Infor M3"));
  assert.equal(back([]), null);
  const b = back([{ question: Q, answer: a1 }]);
  assert.deepEqual(
    b && { n: b.history.length, id: b.current.id, sel: b.selected },
    {
      n: 0,
      id: "target_system",
      sel: "sap_s4hana",
    },
  );
  const c = back([{ question: Q, answer: free }]);
  assert.deepEqual(c && [c.selected, c.otherText], [OTHER, "Infor M3"]);
  assert.deepEqual(answersOf([{ question: Q, answer: a1 }]), [a1]);
});

test("progress grows with the answers and reaches 100 % only when done", () => {
  assert.equal(progress(0, 5, false), 0);
  assert.equal(progress(3, 2, false), 50);
  assert.ok(progress(10, 0, false) < 100);
  assert.equal(progress(7, 0, true), 100);
});

/** @param {string} label @param {string} source @param {string | null} [group] */
const choice = (label, source, group = null) =>
  /** @type {any} */ ({
    value: label.toLowerCase(),
    label,
    description: null,
    group,
    source,
  });

test("verification banner: an AI structure is never called a verified import schema", () => {
  assert.match(verificationBanner("ai").title, /not a verified import schema/);
  assert.equal(verificationBanner("verified").tone, "success");
});

test("sections: verified schema first, then known systems, then AI proposals", () => {
  const list = [
    choice("Weclapp", "known"),
    choice("Odoo", "registry"),
    choice("X", "ai"),
  ];
  assert.deepEqual(
    sectionChoices(list).map((s) => [s.title, s.choices.map((c) => c.label)]),
    [
      ["Verified schema", ["Odoo"]],
      ["Known systems", ["Weclapp"]],
      ["AI proposals", ["X"]],
    ],
  );
  assert.equal(sectionChoices([choice("A", "ai")])[0].title, null); // une seule section : pas de titre
});

test("search filters on name, description and category, without case or accents", () => {
  const list = [
    choice("sevdesk", "known", "Accounting"),
    choice("Lexware Warenwirtschaft", "known", "ERP"),
  ];
  assert.deepEqual(
    filterChoices(list, "WAREN").map((c) => c.label),
    ["Lexware Warenwirtschaft"],
  );
  assert.deepEqual(
    filterChoices(list, "accounting").map((c) => c.label),
    ["sevdesk"],
  );
  assert.equal(filterChoices(list, "  ").length, 2);
});

test("progressive display: first page only, all when searching or asked, selection always visible", () => {
  const list = Array.from({ length: 19 }, (_, i) => choice(`S${i}`, "known"));
  const first = visibleChoices(list, {
    query: "",
    showAll: false,
    selected: "",
  });
  assert.equal(first.visible.length, FIRST_PAGE);
  assert.equal(first.hidden, 19 - FIRST_PAGE);
  assert.equal(
    visibleChoices(list, { query: "", showAll: true, selected: "" }).hidden,
    0,
  );
  assert.equal(
    visibleChoices(list, { query: "s1", showAll: false, selected: "" }).hidden,
    0,
  );
  const chosen = visibleChoices(list, {
    query: "",
    showAll: false,
    selected: "s18",
  });
  assert.ok(chosen.visible.some((c) => c.value === "s18"));
});

/** @param {number} position @param {string} status @param {string | null} target */
const column = (position, status, target, confidence = null) =>
  /** @type {any} */ ({
    position,
    header: `C${position}`,
    filled: 1,
    examples: [],
    status,
    target,
    confidence,
    probably_not_needed: false,
  });

test("mapping: proposed targets first, uncertain ones to confirm, one column per target field", () => {
  const cols = [
    column(0, "matched", "SKU"),
    column(1, "to_confirm", "NAME", 70),
    column(2, "empty", null),
  ];
  const decisions = initialDecisions(cols);
  assert.deepEqual(decisions, { 0: "SKU", 1: "NAME", 2: null });
  assert.deepEqual(
    pendingConfirmations(cols, new Set()).map((c) => c.position),
    [1],
  );
  assert.deepEqual(pendingConfirmations(cols, new Set([1])), []);
  assert.deepEqual([...takenTargets(decisions, 1)], ["SKU"]); // un champ déjà pris ailleurs
  assert.deepEqual(decisionList({ 0: "SKU", 1: null }), [
    { position: 0, target: "SKU" },
    { position: 1, target: null },
  ]);
});

test("mapping: choices are remembered by header (« Not imported » only when chosen), empty columns left out", () => {
  const cols = [
    column(0, "ai_match", "list_price", 96),
    column(1, "unmatched", null),
    column(2, "empty", null),
  ];
  // « Not imported » par défaut d'une colonne non reconnue : jamais retenu (sinon plus jamais analysée)
  assert.deepEqual(
    rememberChoices(cols, { 0: "list_price", 1: null, 2: null }),
    [{ header: "C0", target: "list_price" }],
  );
  // « Not imported » CHOISI par l'utilisateur : retenu
  assert.deepEqual(
    rememberChoices(cols, { 0: "list_price", 1: null, 2: null }, new Set([1])),
    [
      { header: "C0", target: "list_price" },
      { header: "C1", target: null },
    ],
  );
  const again = column(0, "previous", "list_price");
  assert.deepEqual(initialDecisions([again]), { 0: "list_price" }); // réappliqué, rien à confirmer
  assert.deepEqual(pendingConfirmations([again], new Set()), []);
});

test("an AI failure never poisons the next analysis: 54 unrecognized columns are not remembered", () => {
  const cols = Array.from({ length: 55 }, (_, i) =>
    column(i, i === 54 ? "empty" : "unmatched", null),
  );
  const decisions = Object.fromEntries(cols.map((c) => [c.position, null]));
  assert.deepEqual(rememberChoices(cols, decisions, new Set()), []);
});

test("simple mode never shows technical names; a prepared file is never ready to import", () => {
  const f = { name: "categ_id", label: "Product category" };
  assert.equal(fieldName(f, false), "Product category");
  assert.equal(fieldName(f, true), "Product category (categ_id)");
  for (const level of /** @type {const} */ (["verified", "partial", "ai"])) {
    assert.doesNotMatch(preparedBanner(level).title, /ready to import/i);
  }
  assert.match(preparedBanner("ai").title, /not officially verified/);
});

test("type of data comes from the answers, else from the structure title", () => {
  /** @type {any[]} */
  const answers = [
    { topic: "target_system", label: "Big Suite" },
    { topic: "data_scope", label: "General" },
    { topic: "business_object", label: "Products" },
  ];
  assert.equal(dataTypeOf(answers, "Fallback"), "Products");
  // un sous-type n'est jamais le type de données ; le sujet « data_type » passe en premier
  assert.equal(
    dataTypeOf([{ topic: "object_subtype", label: "FERT" }], "Title"),
    "Title",
  );
  assert.equal(
    dataTypeOf(
      /** @type {any} */ ([
        ...answers,
        { topic: "data_type", label: "Customers" },
      ]),
      "Title",
    ),
    "Customers",
  );
  assert.equal(dataTypeOf([], "Fallback"), "Fallback");
});

test("missing information: a Registry value needs no confirmation, anything else must be confirmed", () => {
  /** @type {any} */
  const q = {
    field: "type",
    label: "Product Type",
    kind: "choice",
    title: "?",
    help: null,
    free_text: true,
    warning: null,
    choices: [
      { value: "consu", label: "Goods", description: null, verified: true },
    ],
  };
  const pick = {
    ...emptyMissingAnswer(),
    how: /** @type {const} */ ("choice"),
    value: "consu",
  };
  assert.equal(needsConfirmation(q, pick), false);
  assert.ok(canContinue(q, pick));
  assert.deepEqual(fillOf(q, pick), {
    field: "type",
    common: "consu",
    per_row: {},
    confirmed: false,
  });

  const typed = {
    ...emptyMissingAnswer(),
    how: /** @type {const} */ ("other"),
    other: " UNIT-1 ",
  };
  assert.equal(needsConfirmation(q, typed), true);
  assert.equal(canContinue(q, typed), false); // non vérifiable : confirmation exigée
  assert.deepEqual(fillOf(q, { ...typed, confirmed: true }), {
    field: "type",
    common: "UNIT-1",
    per_row: {},
    confirmed: true,
  });

  const rows = {
    ...emptyMissingAnswer(),
    how: /** @type {const} */ ("per_row"),
    perRow: { 0: "consu", 1: " " },
  };
  assert.deepEqual(fillOf(q, rows), {
    field: "type",
    common: null,
    per_row: { 0: "consu" },
    confirmed: false,
  });
  assert.equal(fillOf(q, emptyMissingAnswer()), null);
});

test("guided resolution: a deduced answer is pre-selected; a business answer or I don't know writes nothing", () => {
  /** @type {any} */
  const q = {
    field: "type",
    label: "Product Type",
    kind: "choice",
    title: "What kind of products are they?",
    help: null,
    free_text: false,
    warning: null,
    choices: [
      { value: "consu", label: "Goods", description: null, verified: true },
    ],
    suggested: "consu",
    explanation: "You track stock, so they are goods.",
    hints: [],
  };
  const start = initialMissingAnswer(q);
  assert.deepEqual([start.how, start.value], ["choice", "consu"]);
  assert.ok(isSuggested(q, start) && canContinue(q, start));
  assert.equal(isSuggested(q, { ...start, value: "service" }), false);

  /** @type {any} */
  const unit = {
    ...q,
    field: "uom_id/id",
    kind: "reference",
    choices: [],
    suggested: null,
    free_text: true,
  };
  const hint = {
    ...emptyMissingAnswer(),
    how: /** @type {const} */ ("hint"),
    value: "Piece",
  };
  assert.ok(canContinue(unit, hint) && !needsConfirmation(unit, hint));
  assert.deepEqual(fillOf(unit, hint), {
    field: "uom_id/id",
    common: null,
    per_row: {},
    confirmed: false,
    hint: "Piece",
  });
  const unknown = {
    ...emptyMissingAnswer(),
    how: /** @type {const} */ ("unknown"),
  };
  assert.deepEqual(fillOf(unit, unknown), {
    field: "uom_id/id",
    common: null,
    per_row: {},
    confirmed: false,
    unknown: true,
  });
});

test("unresolved fields: the previous answer comes back, and the file is never called ready", () => {
  /** @type {any} */
  const q = {
    field: "unit",
    label: "Unit",
    kind: "reference",
    title: "How do you sell your products?",
    help: null,
    free_text: true,
    warning: null,
    choices: [],
    suggested: null,
    previous_hint: "Box",
  };
  const kept = answerFromFill(q, {
    common: null,
    per_row: {},
    confirmed: false,
    hint: "Box",
  });
  assert.deepEqual([kept.how, kept.value], ["hint", "Box"]); // jamais redemandée à vide
  // valeur trouvée dans le système connecté pour la réponse métier : elle passe avant
  const found = answerFromFill(
    {
      ...q,
      suggested: "u.box",
      choices: [
        { value: "u.box", label: "Boxes", description: null, verified: true },
      ],
    },
    { common: null, per_row: {}, confirmed: false, hint: "Box" },
  );
  assert.deepEqual([found.how, found.value], ["choice", "u.box"]);
  const typed = answerFromFill(q, {
    common: "U-1",
    per_row: {},
    confirmed: true,
  });
  assert.deepEqual(
    [typed.how, typed.other, typed.confirmed],
    ["other", "U-1", true],
  );
  assert.match(
    preparedBanner("verified", 2).title,
    /Not ready for import: 2 required fields/,
  );
  assert.doesNotMatch(
    preparedBanner("verified", 0).title,
    /ready (to|for) import/i,
  );
});

test("prepared is not ready: only the server's checks can say ready; verified deductions are applied", () => {
  assert.match(readinessBanner("ready", 0).title, /^Ready for import/);
  assert.match(
    readinessBanner("prepared", 0).title,
    /^Prepared, not yet ready for import/,
  );
  assert.match(
    readinessBanner("to_complete", 2).title,
    /Not ready for import: 2 required fields/,
  );
  // trois états jamais confondus : « Prepared » ≠ « Ready for import »
  assert.deepEqual(readinessLabel("ready", 0), {
    tone: "success",
    label: "Ready for import",
  });
  assert.deepEqual(readinessLabel("prepared", 0), {
    tone: "warning",
    label: "Prepared",
  });
  assert.deepEqual(readinessLabel("to_complete", 3), {
    tone: "warning",
    label: "To complete",
  });
  assert.equal(readinessLabel("prepared", 1).label, "To complete");
  /** @type {any[]} */
  const questions = [
    {
      field: "type",
      label: "Product Type",
      kind: "choice",
      suggested: "consu",
      explanation: "You sell goods only.",
      choices: [{ value: "consu", label: "Goods", verified: true }],
    },
    // nom proposé pour une référence : jamais appliqué d'office (non vérifiable)
    {
      field: "uom_id",
      label: "Unit",
      kind: "reference",
      suggested: "Units",
      explanation: null,
      choices: [],
    },
  ];
  const auto = autoFills(questions);
  // marquée « auto » : jamais notée « complétée par vous » dans le fichier
  assert.deepEqual(auto.fills, [
    {
      field: "type",
      common: "consu",
      per_row: {},
      confirmed: false,
      auto: true,
    },
  ]);
  assert.deepEqual(auto.applied, [
    {
      field: "type",
      label: "Product Type",
      value: "Goods",
      explanation: "You sell goods only.",
    },
  ]);
});

test("the page hard-codes no system, question or choice", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const page = readFileSync(
    join(here, "..", "..", "app", "portal", "wizard", "page.tsx"),
    "utf8",
  );
  assert.doesNotMatch(page, /SAP|S\/4HANA|Odoo|DATEV|Lexware|FERT|2608/);
  assert.doesNotMatch(page, /OPENAI|api[_-]?key/i);
  assert.match(page, /useWizardNext\(/);
  assert.match(page, /Search system/);
  assert.match(page, /Download prepared file/);
  assert.match(page, /Complete missing information/);
  assert.match(page, /I don&apos;t know/);
  assert.match(page, /To complete/);
  assert.match(page, /Connect my system \(optional\)/); // une option, jamais une obligation
  assert.match(page, /Enter exact value/);
  assert.doesNotMatch(page, /External ID|categ_id|uom_id/); // aucun nom technique dans la page
  assert.doesNotMatch(page, /Ready to import/i); // jamais promis
  assert.match(page, /"simple", "expert"/); // deux modes, mêmes réponses
  assert.doesNotMatch(page, /"(Verified schema|Known system|AI proposal)"/); // libellés : flow.mjs seulement
  assert.doesNotMatch(page, /overflow-x-auto|min-w-\[/); // jamais de défilement horizontal
});

test("answers given before the file and after it: one per field, the latest wins", () => {
  const before = [
    { field: "type", common: "consu" },
    { field: "uom_id", common: "Units" },
  ];
  const after = [
    { field: "uom_id", common: "Box" },
    { field: "name", common: "x" },
  ];
  assert.deepEqual(mergeFills(before, after), [
    { field: "type", common: "consu" },
    { field: "uom_id", common: "Box" },
    { field: "name", common: "x" },
  ]);
  assert.deepEqual(mergeFills(before, []), before);
});

test("answers given before the file are shown in plain words", () => {
  const questions = [
    {
      field: "type",
      label: "Product Type",
      choices: [{ value: "consu", label: "Goods" }],
    },
    { field: "uom_id", label: "Unit of Measure", choices: [] },
    { field: "categ_id", label: "Product Category", choices: [] },
  ];
  const fills = [
    { field: "type", common: "consu" },
    { field: "uom_id", common: null, hint: "Piece" },
    { field: "categ_id", common: null, unknown: true },
    { field: "other", common: "x" }, // champ hors des questions : ignoré
  ];
  assert.deepEqual(earlyShown(questions, fills), [
    { field: "type", label: "Product Type", value: "Goods" },
    { field: "uom_id", label: "Unit of Measure", value: "Piece" },
    { field: "categ_id", label: "Product Category", value: "Not known yet" },
  ]);
});
