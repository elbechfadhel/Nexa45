// Tests du mode Simple « fichier d'abord » de l'AI Migration Wizard (node --test) : logique des trois écrans et
// garde-fous des composants (aucun système, champ ni texte en dur hors du fichier de libellés).
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  alreadyExportedText,
  answerText,
  appliedThemes,
  rowTaxKey,
  settingsSummary,
  currencyNoticeText,
  automaticColumns,
  choicesOf,
  columnsToConfirm,
  decisionsOf,
  defaultOrigin,
  defaultTarget,
  excludedGroups,
  fieldLabel,
  fillsOf,
  formatChoices,
  afterDetect,
  detectCandidates,
  openTile,
  soleSystem,
  systemTileDetail,
  ignoredColumns,
  importSteps,
  initialEdits,
  questionKey,
  requestOf,
  resultTitle,
  takenFields,
  targetGroups,
  targetTiles,
  targetSubtitle,
  targetTitle,
  unanswered,
} from "./simple.mjs";
import { entryTargets, WIZARD_ENTRIES } from "./entries.mjs";
import { SIMPLE_LABELS } from "./simple-labels.mjs";

const here = dirname(fileURLToPath(import.meta.url));

const TARGETS = [
  { value: "instance:1", label: "Shop 18", group: "instance", detail: "Main shop", connected: true },
  { value: "registry:shop:shop:17", label: "Shop 17", group: "registry" },
  { value: "registry:shop:shop:18", label: "Shop 18", group: "registry" },
  { value: "known:acme", label: "Acme ERP", group: "known" },
];

/** Réponse de /quick-start (forme de l'API, valeurs inventées). */
function start() {
  return {
    data_type: "Products",
    target_label: "Shop 18",
    choices: { currency: "EUR", "records:unit": "default" },
    questions: [
      {
        question: {
          field: "category",
          label: "Category",
          kind: "choice",
          title: "Your file has its own category values: what should Shop 18 do with them?",
          choices: [
            { value: "create", label: "Create the missing category values" },
            { value: "default", label: "Use the default category of Shop 18 for every row" },
          ],
          free_text: false,
        },
        default: "create",
        default_from: "usual_default",
        decision: "records:category",
      },
      {
        question: { field: "brand", label: "Brand", kind: "text", title: "Which brand?", choices: [], free_text: true },
        default: null,
        decision: null,
      },
    ],
    applied: [
      {
        question: {
          field: "kind",
          label: "Product Type",
          kind: "choice",
          title: "Product type",
          choices: [
            { value: "goods_code", label: "Goods" },
            { value: "service_code", label: "Service" },
          ],
          free_text: false,
        },
        default: "goods_code",
        default_from: "registry_first_value",
        decision: null,
      },
      {
        question: {
          field: "unit",
          label: "Unit of Measure",
          kind: "reference",
          title: "Unit",
          choices: [{ value: "default", label: "The default unit of measure of Shop 18" }],
          free_text: true,
        },
        default: "default",
        default_from: "usual_default",
        decision: "records:unit",
      },
    ],
    fills: [{ field: "kind", common: "goods_code", per_row: {}, confirmed: true }],
    decisions: [
      { position: 0, target: "name" },
      { position: 1, target: "price" },
    ],
    mapping: {
      file_name: "list.csv",
      rows: 26,
      columns: [
        { position: 0, header: "Name", status: "matched", target: "name", examples: ["Lamp"], filled: 26 },
        { position: 1, header: "Price", status: "ai_match", target: "price", examples: ["9"], filled: 26 },
        { position: 2, header: "Text", status: "to_confirm", target: "description", examples: ["x"], filled: 20 },
        { position: 3, header: "Ref", status: "to_confirm", target: "code", examples: ["R1"], filled: 26 },
        { position: 4, header: "Remark", status: "unmatched", target: null, examples: ["y"], filled: 3 },
        { position: 5, header: "Spare", status: "empty", target: null, examples: [], filled: 0 },
      ],
    },
    proposal: {
      fields: [
        { name: "name", label: "Name" },
        { name: "price", label: "Sales Price" },
        { name: "description", label: "Sales Description" },
        { name: "code", label: "Internal Reference" },
      ],
    },
  };
}

test("targets: your systems first, then the Registry, then known systems; search keeps the order", () => {
  assert.deepEqual(
    targetGroups(TARGETS).map((g) => [g.group, g.items.length]),
    [
      ["instance", 1],
      ["registry", 2],
      ["known", 1],
    ],
  );
  assert.deepEqual(
    targetGroups(TARGETS, "main").map((g) => g.items.map((t) => t.value)),
    [["instance:1"]],
  );
  assert.deepEqual(targetGroups(TARGETS, "nothing"), []);
  // votre système connecté : son nom en titre, le système et la version en sous-titre
  assert.equal(targetTitle(TARGETS[0]), "Main shop");
  assert.equal(targetSubtitle(TARGETS[0]), "Shop 18");
  assert.equal(targetTitle(TARGETS[1]), "Shop 17");
  assert.equal(targetSubtitle(TARGETS[1]), "");
  assert.equal(defaultTarget(TARGETS), "instance:1");
  assert.equal(defaultTarget(TARGETS.slice(1)), ""); // aucun système connecté : l'utilisateur choisit
});

test("every question and applied decision starts with its preselected answer, shown in plain words", () => {
  const s = start();
  const edits = initialEdits(s);
  assert.deepEqual(edits.answers, {
    "records:category": "create",
    "field:kind": "goods_code",
    "records:unit": "default",
  });
  assert.equal(questionKey(s.questions[1]), "field:brand");
  assert.equal(answerText(s.applied[0], "goods_code"), "Goods"); // jamais la valeur technique
  assert.equal(answerText(s.questions[1], "Acme"), "Acme"); // valeur saisie
  assert.equal(defaultOrigin(s.applied[0]), "usual value");
  assert.deepEqual(
    unanswered(s, edits).map((q) => q.question.field),
    ["brand"],
  );
});

test("the request sends choices, fills and decisions; confirmed columns only with the download", () => {
  const s = start();
  const edits = initialEdits(s);
  edits.answers["field:kind"] = "service_code"; // décision appliquée changée d'un clic
  edits.answers["field:brand"] = "Acme";
  edits.answers["records:category"] = "default";
  edits.confirmed = [3, 2];
  edits.targets = { 2: null }; // « Text » : Not imported, confirmé
  assert.deepEqual(choicesOf(s, edits), {
    currency: "EUR",
    "records:unit": "default",
    "records:category": "default",
  });
  assert.deepEqual(fillsOf(s, edits), [
    { field: "kind", common: "service_code", per_row: {}, confirmed: true },
    { field: "brand", common: "Acme", per_row: {}, confirmed: true },
  ]);
  assert.deepEqual(decisionsOf(s, edits), [
    { position: 0, target: "name" },
    { position: 1, target: "price" },
    { position: 2, target: null }, // gardée sans champ : la mémoire apprend « pas utile »
    { position: 3, target: "code" },
  ]);
  assert.deepEqual(requestOf(s, edits).confirmed, []); // l'aperçu n'apprend rien
  assert.deepEqual(requestOf(s, edits, true).confirmed, [2, 3]);
});

test("columns: automatic, to confirm and not imported are listed apart, each field taken once", () => {
  const s = start();
  const edits = initialEdits(s);
  assert.deepEqual(automaticColumns(s).map((c) => c.header), ["Name", "Price"]);
  assert.deepEqual(columnsToConfirm(s).map((c) => c.header), ["Text", "Ref"]);
  // une colonne à confirmer n'est pas « non importée » tant qu'on ne l'a pas décidé
  assert.deepEqual(ignoredColumns(s, edits).map((c) => c.header), ["Remark"]);
  edits.confirmed = [2];
  edits.targets = { 2: null };
  assert.deepEqual(ignoredColumns(s, edits).map((c) => c.header), ["Text", "Remark"]);
  assert.equal(fieldLabel(s, "price"), "Sales Price");
  assert.equal(fieldLabel(s, null), "Not imported");
  edits.confirmed = [3];
  assert.deepEqual([...takenFields(s, edits, 2)].sort(), ["code", "name", "price"]);
  assert.equal(takenFields(s, edits, 3).has("code"), false); // son propre champ reste proposé
});

test("summary: ready, prepared or to complete; excluded rows grouped by reason; import order explained", () => {
  assert.equal(resultTitle({ rows: 25, status: "ready" }, "Products"), "25 products ready");
  assert.equal(resultTitle({ rows: 25, status: "prepared" }, "Products"), "25 products prepared");
  assert.match(resultTitle({ rows: 3, status: "to_complete" }, "Customers"), /^3 customers prepared, information/);
  assert.equal(resultTitle({ rows: 2, status: "prepared" }, "Unknown"), "2 records prepared");
  assert.deepEqual(
    excludedGroups([
      { row: 12, reason: "Same data as row 11", kind: "duplicate" },
      { row: 3, reason: "Amounts in INR", kind: "currency" },
      { row: 5, reason: "Amounts in GBP", kind: "currency" },
      { row: 9, reason: "Something else" },
    ]).map((g) => [g.kind, g.rows]),
    [
      ["currency", [3, 5]],
      ["duplicate", [12]],
      ["other", [9]],
    ],
  );
  assert.deepEqual(
    importSteps(
      { created_records: [{ sheet: "Product Category (import first)", count: 6, label: "Product Category" }] },
      "Products",
    ),
    {
      steps: [
        "Import the sheet “Product Category (import first)” first: 6 product categories to create.",
        "Then import the sheet “Data”: your products.",
      ],
      notes: [],
    },
  );
  assert.deepEqual(importSteps({ created_records: [] }, "Products"), {
    steps: ["Import the sheet “Data”: your products."],
    notes: [],
  });
});

test("the new screens hard-code no system, field or sentence: texts live in the labels file", () => {
  const folder = join(here, "..", "..", "components", "wizard-simple");
  const files = readdirSync(folder).filter((f) => f.endsWith(".tsx"));
  assert.ok(files.length >= 4, "components in their own folder");
  const page = readFileSync(join(here, "..", "..", "app", "portal", "wizard", "page.tsx"), "utf8");
  assert.match(page, /<SimpleWizard/); // Simple = démarrage par le fichier, par défaut
  assert.match(page, /useState<WizardMode>\(\(\) => storedMode\(\)\)/);
  for (const file of files) {
    const code = readFileSync(join(folder, file), "utf8");
    assert.doesNotMatch(code, /Odoo|DATEV|Lexware|SAP|categ_id|uom_id|consu|default_code/, file);
    assert.doesNotMatch(code, /overflow-x-auto|min-w-\[/, file); // jamais de défilement horizontal
    assert.doesNotMatch(code, /Ready to import/i, file);
    // aucune phrase en dur dans le JSX : un texte entre balises vient de SIMPLE_LABELS (L.…)
    const sentences = [...code.matchAll(/>\s*([A-Z][a-z]+(?: [a-z]+){2,}[^<{]*)</g)].map((m) => m[1]);
    assert.deepEqual(sentences, [], file);
  }
  const labels = Object.keys(SIMPLE_LABELS);
  for (const key of ["start", "download", "confirmAll", "appliedHeading", "importOrderHeading", "workingSteps"]) {
    assert.ok(labels.includes(key), key);
  }
});

test("the wizard says nothing is written to your systems, never that nothing is sent anywhere", () => {
  // le fichier est lu, l'IA en reçoit des en-têtes et des exemples masqués : « rien n'est envoyé » serait faux
  assert.match(SIMPLE_LABELS.intro, /Nothing is written to your systems\./);
  const page = readFileSync(join(here, "..", "..", "app", "portal", "wizard", "page.tsx"), "utf8");
  const catalog = readFileSync(join(here, "..", "use-cases", "catalog.mjs"), "utf8");
  const folder = join(here, "..", "..", "components", "wizard-simple");
  const texts = [
    JSON.stringify(SIMPLE_LABELS),
    page,
    catalog,
    ...readdirSync(folder).map((f) => readFileSync(join(folder, f), "utf8")),
  ];
  for (const text of texts) {
    assert.doesNotMatch(text, /nothing is sent to any system|sent to no system|nothing leaves/i);
  }
  assert.match(page, /nothing is written to your systems\./i);
});

test("English texts use true singulars and plurals and English quotes", () => {
  assert.equal(resultTitle({ rows: 1, status: "prepared" }, "Products"), "1 product prepared");
  assert.equal(resultTitle({ rows: 1643, status: "ready" }, "Products"), "1,643 products ready");
  assert.equal(SIMPLE_LABELS.resultMeta(1, "a.csv", "Odoo 17"), "1 row read in a.csv · target Odoo 17");
  assert.equal(SIMPLE_LABELS.excludedCount(33), "33 rows");
  const all = readFileSync(join(here, "simple-labels.mjs"), "utf8")
    .split("\n")
    .filter((l) => !/^\s*(\/\/|\*|\/\*\*)/.test(l))
    .join("\n");
  assert.doesNotMatch(all, /[«»]|\w\(s\)/);
});

test("the formats or versions of one system are one tile; the format or the version is chosen next", () => {
  /** @type {any[]} */
  const targets = [
    { value: "registry:odoo:odoo:17", label: "Odoo 17", group: "registry", system: "odoo", system_name: "Odoo", format: null },
    { value: "registry:odoo:odoo:18", label: "Odoo 18", group: "registry", system: "odoo", system_name: "Odoo", format: null },
    { value: "registry:x:a:12", label: "X A 12", group: "registry", system: "x", system_name: "X", format: "A 12" },
    { value: "registry:x:a:13", label: "X A 13", group: "registry", system: "x", system_name: "X", format: "A 13" },
    { value: "registry:x:b:5", label: "X B 5", group: "registry", system: "x", system_name: "X", format: "B 5" },
    { value: "registry:y:api:v1", label: "Y", group: "registry", system: "y", system_name: "Y", format: null },
    { value: "instance:1", label: "Odoo 17", group: "instance", detail: "accounting17" },
  ];
  const tiles = targetTiles(targets);
  assert.deepEqual(
    tiles.map((t) =>
      t.kind === "system" ? `${t.label} (${t.choose}): ${t.formats.map((f) => f.value).join(", ")}` : t.target.label,
    ),
    [
      "Odoo (version): registry:odoo:odoo:17, registry:odoo:odoo:18",
      "X (format): registry:x:a:12, registry:x:a:13, registry:x:b:5",
      "Y", // une seule cible : une tuile simple
      "Odoo 17", // votre système connecté reste sa propre tuile
    ],
  );
  // versions : chacune est un choix visible, aucune dans « Other versions »
  const odoo = /** @type {any} */ (tiles[0]);
  assert.deepEqual(formatChoices(odoo.formats), {
    main: [
      { value: "registry:odoo:odoo:17", label: "Odoo 17" },
      { value: "registry:odoo:odoo:18", label: "Odoo 18" },
    ],
    others: [],
  });
  assert.equal(openTile(tiles, "", "registry:odoo:odoo:18")?.system, "odoo");
  assert.equal(SIMPLE_LABELS.formatCount(3), "3 formats");
  assert.equal(SIMPLE_LABELS.versionCount(3), "3 versions");
  assert.equal(SIMPLE_LABELS.versionHeading("Odoo"), "Which version of Odoo do you use?");
});

test("more than half of the rows without their amounts: one visible sentence on top of the summary", () => {
  const notice = { target: "EUR", file: ["USD", "INR"], rows: 997, fields: ["Sales Price"], from_system: true };
  assert.equal(
    currencyNoticeText({ currency_notice: notice }, "Products"),
    "Your system uses EUR, your file uses USD, INR: 997 products prepared without a sales price. No amount is converted.",
  );
  assert.equal(
    currencyNoticeText({ currency_notice: { ...notice, from_system: false, rows: 1, file: ["USD"] } }, "Products"),
    "You chose EUR, your file uses USD: 1 product prepared without a sales price. No amount is converted.",
  );
  assert.equal(currencyNoticeText({ currency_notice: null }, "Products"), "");
});

test("the notice ends with what the target import writes instead of the empty prices", () => {
  const notice = {
    target: "EUR",
    file: ["USD"],
    rows: 997,
    fields: ["Sales Price"],
    from_system: true,
    note: "Odoo 17 will use its default Sales Price, 1.00, for these products.",
  };
  assert.equal(
    currencyNoticeText({ currency_notice: notice }, "Products"),
    "Your system uses EUR, your file uses USD: 997 products prepared without a sales price. No amount is converted. " +
      "Odoo 17 will use its default Sales Price, 1.00, for these products.",
  );
});

test("a question about a field offers the file's columns: the chosen column feeds the field", () => {
  /** @type {any} */
  const start = {
    choices: {},
    fills: [],
    decisions: [{ position: 0, target: "name" }],
    mapping: { columns: [] },
    questions: [
      {
        question: { field: "default_code", title: "Which column of your file contains Reference?", choices: [], file_columns: [{ position: 2, header: "Ref", examples: ["A-1"] }] },
        default: null,
        decision: null,
      },
    ],
    applied: [],
  };
  const edits = { answers: { "field:default_code": "column:2" }, confirmed: [], targets: {} };
  assert.deepEqual(decisionsOf(start, edits), [
    { position: 0, target: "name" },
    { position: 2, target: "default_code" },
  ]);
  assert.deepEqual(fillsOf(start, edits), []); // une colonne, jamais une valeur commune
  assert.deepEqual(requestOf(start, edits, true).confirmed, [2]); // la mémoire apprend ce choix explicite
  assert.equal(SIMPLE_LABELS.columnOption("Ref", ["A-1", "B-2"]), "“Ref” (for example A-1, B-2)");
});

test("DATEV: two plain choices named by what they receive, latest version; other versions apart", () => {
  /** @type {any[]} */
  const formats = [
    { value: "b12", label: "DATEV Buchungsstapel 12", format: "Buchungsstapel 12", product_name: "Buchungsstapel", version: "12", latest: false, receives: [] },
    { value: "b13", label: "DATEV Buchungsstapel 13", format: "Buchungsstapel 13", product_name: "Buchungsstapel", version: "13", latest: true, receives: ["Invoices"] },
    { value: "d5", label: "DATEV Debitoren/Kreditoren 5", format: "Debitoren/Kreditoren 5", product_name: "Debitoren/Kreditoren", version: "5", latest: true, receives: ["Customers & suppliers", "Customers", "Suppliers"] },
  ];
  const { main, others } = formatChoices(formats);
  assert.deepEqual(main.map((c) => c.label), ["Bookings (Buchungsstapel)", "Customers and suppliers (Debitoren/Kreditoren)"]);
  assert.deepEqual(others, [{ value: "b12", label: "Buchungsstapel · Version 12" }]);
});

test("the checking step says what is checked for this target", () => {
  const [, , check] = SIMPLE_LABELS.workingSteps("DATEV Buchungsstapel 13", ["Invoices"]);
  assert.equal(check.title, "Checking against DATEV Buchungsstapel 13");
  assert.doesNotMatch(check.help, /barcode|price/i);
  assert.match(SIMPLE_LABELS.workingSteps("Odoo 17", ["Products"])[2].help, /barcodes/);
  assert.match(SIMPLE_LABELS.workingSteps("DATEV", ["Customers & suppliers", "Customers", "Suppliers"])[2].help, /VAT IDs/);
});

test("a file in the target's own format is imported as one file, with what was prepared", () => {
  const preview = { delivery: "format", notices: ["39 invoices in the file (39 bookings, period 07/2026 to 09/2026)."] };
  // une seule étape numérotée ; le contenu du fichier est une information, pas une étape
  assert.deepEqual(importSteps(preview, "Invoices", "DATEV Buchungsstapel"), {
    steps: [
      "Import this file in DATEV Buchungsstapel: your invoices, in the format of DATEV Buchungsstapel (nothing is sent automatically).",
    ],
    notes: ["39 invoices in the file (39 bookings, period 07/2026 to 09/2026)."],
  });
});

test("rows not written for other reasons are listed one by one with their reason", () => {
  const groups = excludedGroups([
    { row: 35, reason: "Invoice RE-35: 16 % is not a valid VAT rate.", kind: "rate" },
    { row: 38, reason: "Invoice RE-4 entered twice.", kind: "duplicate" },
  ]);
  assert.deepEqual(groups.find((g) => g.kind === "other")?.reasons, [
    { row: 35, reason: "Invoice RE-35: 16 % is not a valid VAT rate." },
  ]);
  // doublon PROBABLE (même USt-IdNr) : son propre groupe, avec son motif, jamais « Duplicate rows »
  const probable = excludedGroups([
    { row: 14, reason: "Probable duplicate (same VAT ID as row 1): not exported.", kind: "probable_duplicate" },
    { row: 38, reason: "Invoice RE-4 entered twice.", kind: "duplicate" },
  ]);
  assert.deepEqual(
    probable.map((g) => [g.kind, g.title]),
    [
      ["duplicate", "Duplicate rows (imported once)"],
      ["probable_duplicate", "Probable duplicates (not exported)"],
    ],
  );
  assert.deepEqual(probable[1].reasons, [
    { row: 14, reason: "Probable duplicate (same VAT ID as row 1): not exported." },
  ]);
  assert.equal(SIMPLE_LABELS.questionsHelp(2, 0), "2 decisions to answer.");
  assert.equal(SIMPLE_LABELS.questionsHelp(3, 1), "3 decisions, 1 already preselected.");
  assert.equal(SIMPLE_LABELS.questionsHelp(2), "2 decisions, each already preselected.");
});

test("an entry point reuses the Simple screens: only its title, introduction and targets change", () => {
  // Wizard général : toutes les cibles, dans l'ordre de l'API
  assert.deepEqual(entryTargets(TARGETS, WIZARD_ENTRIES.general), TARGETS);
  assert.equal(WIZARD_ENTRIES.general.title, SIMPLE_LABELS.title);
  for (const entry of Object.values(WIZARD_ENTRIES)) {
    assert.match(entry.intro, /Nothing is written to your systems\./);
  }
  assert.equal(WIZARD_ENTRIES.datev.title, "Prepare data for DATEV");
  // les transformations décidées (numéro : espace → tiret, zéro d'une PLZ) sont listées dans la synthèse
  const result = readFileSync(join(here, "..", "..", "components", "wizard-simple", "result-screen.tsx"), "utf8");
  assert.match(result, /preview\.value_transforms\.map/);
  assert.equal(SIMPLE_LABELS.transformCount(1), "1 value");
  // les deux sens : clients et fournisseurs, factures de vente et d'achat
  assert.match(WIZARD_ENTRIES.datev.intro, /customer or supplier list, or your sales or purchase invoices/);
  // la question de l'étape 2 : propre au parcours ; le Wizard garde la sienne
  assert.equal(WIZARD_ENTRIES.datev.targetHeading, "2. What do you want to prepare?");
  assert.equal(WIZARD_ENTRIES.general.targetHeading, undefined);
  assert.equal(SIMPLE_LABELS.targetHeading, "2. Where should your data go?");
  // parcours DATEV : les formats DATEV du Registry seulement, une tuile ouverte d'office sur ses deux choix
  /** @type {any[]} */
  const all = [
    { value: "instance:1", label: "Odoo 17", group: "instance", detail: "accounting17", connected: true },
    { value: "registry:datev:buchungsstapel:12", label: "DATEV Buchungsstapel 12", group: "registry", system: "datev", system_name: "DATEV", format: "Buchungsstapel 12", product_name: "Buchungsstapel", version: "12" },
    { value: "registry:datev:buchungsstapel:13", label: "DATEV Buchungsstapel 13", group: "registry", system: "datev", system_name: "DATEV", format: "Buchungsstapel 13", product_name: "Buchungsstapel", version: "13", latest: true, receives: ["Invoices"] },
    { value: "registry:datev:debitoren-kreditoren:5", label: "DATEV Debitoren/Kreditoren 5", group: "registry", system: "datev", system_name: "DATEV", format: "Debitoren/Kreditoren 5", product_name: "Debitoren/Kreditoren", version: "5", latest: true, receives: ["Customers & suppliers"] },
    { value: "registry:lexware:public-api:v1", label: "Lexware Office", group: "registry", system: "lexware", system_name: "Lexware Office" },
    { value: "registry:odoo:odoo:17", label: "Odoo 17", group: "registry", system: "odoo", system_name: "Odoo" },
    { value: "known:sevdesk", label: "sevdesk", group: "known" },
  ];
  all.forEach((t) => (t.product = t.value.split(":")[2]));
  const datev = entryTargets(all, WIZARD_ENTRIES.datev);
  assert.deepEqual(datev.map((t) => t.value), all.slice(1, 4).map((t) => t.value));
  assert.deepEqual(targetGroups(datev).map((g) => g.group), ["registry"]); // ni « proposés par l'IA », ni vos systèmes
  const tiles = targetTiles(datev);
  const open = openTile(tiles, "", "");
  assert.equal(open?.system, "datev");
  // un seul système à l'écran : ses choix directement, sans titre de groupe ni tuile
  assert.equal(soleSystem(tiles), open);
  // dès le dépôt du fichier : le serveur choisit parmi les deux choix visibles (règles, sans LLM)
  assert.equal(WIZARD_ENTRIES.datev.detect, true);
  assert.equal(WIZARD_ENTRIES.general.detect, undefined); // Wizard général inchangé
  assert.deepEqual(open && detectCandidates(open), [
    "registry:datev:buchungsstapel:13",
    "registry:datev:debitoren-kreditoren:5",
  ]);
  const partners = "registry:datev:debitoren-kreditoren:5";
  assert.deepEqual(afterDetect("", false, { target: partners }), { target: partners, auto: true });
  // type pas sûr : un choix fait d'après le fichier précédent est retiré, celui de l'utilisateur est gardé
  assert.deepEqual(afterDetect(partners, true, { target: null }), { target: "", auto: false });
  assert.deepEqual(afterDetect(partners, false, { target: null }), { target: partners, auto: false });
  assert.deepEqual(afterDetect("", false, {}), { target: "", auto: false });
  // deux formats (Buchungsstapel en deux versions, Debitoren/Kreditoren) : « 2 formats », pas « 3 formats »
  assert.equal(open && systemTileDetail(open), "2 formats");
  assert.deepEqual(formatChoices(open?.formats ?? []).main.map((c) => c.label), [
    "Bookings (Buchungsstapel)",
    "Customers and suppliers (Debitoren/Kreditoren)",
  ]);
  assert.equal(defaultTarget(datev), ""); // l'utilisateur choisit ce que DATEV reçoit
  // Wizard général inchangé : tous les groupes et tous les systèmes ; aucune tuile ouverte d'office
  const general = entryTargets(all, WIZARD_ENTRIES.general);
  assert.deepEqual(general, all);
  assert.deepEqual(targetGroups(general).map((g) => g.group), ["instance", "registry", "known"]);
  assert.equal(openTile(targetGroups(general).flatMap((g) => targetTiles(g.items)), "", ""), undefined);
  assert.equal(soleSystem(targetGroups(general).flatMap((g) => targetTiles(g.items))), undefined);
  // écran 1 : le système seul n'a ni groupe ni tuile, ses choix sont rendus directement
  const screen = readFileSync(join(here, "..", "..", "components", "wizard-simple", "start-screen.tsx"), "utf8");
  assert.match(screen, /\{sole \? \([^]*?<Choices tile=\{sole\}/);
  // choix seuls : ni second titre ni « The most recent version is preselected » (rien n'est présélectionné)
  assert.match(screen, /<Choices tile=\{sole\} [^>]*label=\{heading\}/);
  assert.match(screen, /\{label === undefined && \([^)]*?<h4[^]*?L\.formatHelp[^]*?<\/div>\s*\)\}/);
  assert.equal(SIMPLE_LABELS.formatHelp, "The most recent version is preselected."); // Wizard général inchangé
  // fichier choisi : icône tableur sur fond bleu léger, le format dans la ligne méta (« XLSX · 8 KB »)
  assert.match(screen, /<FileSpreadsheet /);
  assert.doesNotMatch(screen, /FileBadge/);
  // la page du parcours ne fait que passer sa configuration aux composants du mode Simple : aucune copie
  const app = join(here, "..", "..", "app", "portal");
  const page = readFileSync(join(app, "prepare-datev", "page.tsx"), "utf8");
  assert.match(page, /<SimpleWizard entry=\{WIZARD_ENTRIES\.datev\} \/>/);
  assert.doesNotMatch(page, /StartScreen|ResultScreen|useWizard|toggle=|ModeToggle/);
  // le Wizard général garde le sélecteur Simple / Expert
  assert.match(readFileSync(join(app, "wizard", "page.tsx"), "utf8"), /<SimpleWizard toggle=/);
  const header = readFileSync(join(here, "..", "..", "components", "app-header.tsx"), "utf8");
  assert.match(header, /href: "\/portal\/prepare-datev", label: "Prepare data for DATEV"/);
});

test("every single choice of the console uses the shared radio group", () => {
  // un seul style de choix unique (components/ui/radio-list.tsx) : aucun radio dessiné ailleurs
  const src = join(here, "..", "..");
  /** @param {string} dir @returns {string[]} */
  const files = (dir) =>
    readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? files(join(dir, e.name)) : e.name.endsWith(".tsx") ? [join(dir, e.name)] : [],
    );
  const offenders = files(src).filter(
    (f) => !f.endsWith("radio-list.tsx") && /type="radio"|role="radio"/.test(readFileSync(f, "utf8")),
  );
  assert.deepEqual(offenders, []);
});

test("decided for you is grouped by kind of decision: settings, accounts by category, file decisions", () => {
  /** @param {string} decision @param {string} label @param {string} value @param {any} [mapping] @returns {any} */
  const q = (decision, label, value, mapping) => ({
    decision,
    default: value,
    question: { field: decision, label, kind: "text", title: label, choices: [], free_text: true },
    ...(mapping ? { mapping } : {}),
  });
  const auto = ["3300", "3400"];
  const applied = [
    q("setting:advisor_number", "Advisor number (Beraternummer)", "1234567"),
    q("setting:chart_of_accounts", "Chart of accounts (Sachkontenrahmen)", "03"),
    q("map:CATEGORY:purchase:wareneinkauf:19", "G/L account for Wareneinkauf at 19 % VAT", "3400", {
      category: "Wareneinkauf",
      rate: "19",
      tax_decision: "map:TAX:rate:PURCHASE:19",
      automatic_accounts: auto,
    }),
    q("map:CATEGORY:purchase:werbung:19", "G/L account for Werbung at 19 % VAT", "4600", {
      category: "Werbung",
      rate: "19",
      tax_decision: "map:TAX:rate:PURCHASE:19",
      automatic_accounts: auto,
    }),
    q("map:TAX:rate:PURCHASE:19", "Tax key for purchases at 19 % VAT", "0009"),
    q("accounting:number_spaces", "1 invoice number contains spaces", "hyphen"),
  ];
  const themes = appliedThemes(applied);
  assert.deepEqual(themes.settings.map((x) => x.decision), ["setting:advisor_number", "setting:chart_of_accounts"]);
  assert.deepEqual(themes.rows.map((x) => x.mapping?.category), ["Wareneinkauf", "Werbung"]);
  assert.deepEqual(themes.taxes.map((x) => x.decision), ["map:TAX:rate:PURCHASE:19"]); // jamais une ligne à part
  assert.deepEqual(themes.others.map((x) => x.decision), ["accounting:number_spaces"]);
  assert.equal(settingsSummary(themes.settings, {}), "Beraternummer 1234567 · Sachkontenrahmen 03");
  // compte automatique : aucune clé de taxe écrite, quelle que soit la clé du taux
  assert.equal(rowTaxKey(themes.rows[0], themes.taxes[0], {}), "none (automatic account)");
  assert.equal(rowTaxKey(themes.rows[1], themes.taxes[0], {}), "0009");
});

test("invoices already exported are one summary line by date, the detail only in a collapsed block", () => {
  const rows = [
    { row: 2, reason: "Invoice A: 16 % is not a valid VAT rate: not exported.", kind: "rate" },
    { row: 1, reason: "Invoice B: Already exported on 2026-10-26: not exported again.", kind: "already_exported" },
  ];
  const groups = excludedGroups(rows);
  assert.deepEqual(groups.map((g) => g.kind), ["other", "already_exported"]);
  assert.equal(groups[1].reasons.length, 1); // le détail reste disponible (bloc dépliable)
  const one = { already_exported: [{ date: "2026-10-26", count: 27 }] };
  assert.equal(
    alreadyExportedText(one, "Purchase invoices"),
    "27 purchase invoices already exported on 2026-10-26: not exported again",
  );
  const two = { already_exported: [{ date: "2026-10-25", count: 12 }, { date: "2026-10-26", count: 15 }] };
  assert.equal(
    alreadyExportedText(two, "Invoices"),
    "27 invoices already exported (12 on 2026-10-25, 15 on 2026-10-26): not exported again",
  );
  assert.equal(alreadyExportedText({}, "Invoices"), "");
  assert.equal(
    SIMPLE_LABELS.nothingNewHelp({ one: "invoice", many: "invoices" }),
    "Nothing new to export: every invoice of this file was already exported.",
  );
  // rien de neuf : jamais « Your file is prepared », aucun bouton de téléchargement
  const folder = join(here, "..", "..", "components", "wizard-simple");
  assert.match(readFileSync(join(folder, "result-screen.tsx"), "utf8"), /preview\.nothing_new\s*\?\s*L\.nothingNewHelp/);
  assert.match(readFileSync(join(folder, "download-card.tsx"), "utf8"), /\{!preview\.nothing_new && \(\s*<Button/);
});
