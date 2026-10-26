// @ts-check
/**
 * AI Migration Wizard — mode Simple « fichier d'abord » : TOUS les textes des écrans (dépôt du fichier et choix de
 * la cible, progression, synthèse), en un seul endroit pour la traduction à venir (de, fr). Anglais seulement pour
 * l'instant. Aucun nom de système, de champ ni de valeur technique : ceux-ci viennent de l'API.
 *
 * Un texte avec des valeurs est une fonction ; l'ordre des mots reste ainsi traduisible.
 */

/** Nombre anglais (1,643).
 * @param {number} n */
const num = (n) => n.toLocaleString("en-US");

/** Pluriel anglais du dernier mot (« product category » → « product categories »).
 * @param {string} words */
const plural = (words) =>
  /[^aeiou]y$/i.test(words) ? `${words.slice(0, -1)}ies` : /(s|x|z|ch|sh)$/i.test(words) ? `${words}es` : `${words}s`;

/** « 1 row », « 2 rows » : vrai singulier ou pluriel, jamais « row(s) ».
 * @param {number} n @param {string} one @param {string} [many] */
const count = (n, one, many) => `${num(n)} ${n === 1 ? one : (many ?? plural(one))}`;

/** Ce que vérifie l'étape « Checking against… », selon ce que la cible reçoit. */
const CHECKS = {
  Products: "Prices, barcodes, units, categories and required fields.",
  Customers: "Names, addresses, VAT IDs, bank accounts and account numbers.",
  Suppliers: "Names, addresses, VAT IDs, bank accounts and account numbers.",
  "Customers & suppliers": "Names, addresses, VAT IDs, bank accounts and account numbers.",
  Invoices: "Amounts, VAT rates, dates, accounts and invoices already exported.",
  "Purchase invoices": "Suppliers, amounts, VAT rates, dates, expense accounts and invoices already exported.",
};

/** @param {string[]} receives */
const checksOf = (receives) => {
  const known = /** @type {Record<string, string>} */ (CHECKS);
  const found = [...new Set(receives.map((r) => known[r]).filter(Boolean))];
  return found.length === 1 ? found[0] : "Values, formats and required fields.";
};

export const SIMPLE_LABELS = {
  // ------------------------------------------------------------------ en-tête
  title: "AI Migration Wizard",
  intro: "Add your file, choose where it goes, and download it prepared. Nothing is written to your systems.",
  preparesHeading: "What you can prepare here:",

  // ------------------------------------------------------------------ écran 1 : fichier et cible
  progress: "Progress",
  stepStart: "Your file and target",
  stepWorking: "Analysis",
  stepResult: "Summary",
  fileHeading: "1. Your file",
  fileDrop: "Choose a file or drop it here",
  fileTypes: ".xlsx or .csv, up to 10 MB",
  fileChange: "Change file",
  targetHeading: "2. Where should your data go?",
  targetSearch: "Search system",
  targetNone: "No system matches your search.",
  targetLoading: "Loading the systems…",
  connected: "Connected",
  /** @param {number} n */
  formatCount: (n) => count(n, "format"),
  /** @param {number} n */
  versionCount: (n) => count(n, "version"),
  /** @param {string} system */
  formatHeading: (system) => `What should ${system} receive?`,
  formatHelp: "The most recent version is preselected.",
  /** @param {string} system */
  versionHeading: (system) => `Which version of ${system} do you use?`,
  versionHelp: "Fields, types and allowed values differ between versions.",
  /** Ce que reçoit un format, d'après les types de données qu'il accepte (le nom du format suit entre parenthèses). */
  formatKinds: {
    Invoices: "Bookings",
    "Purchase invoices": "Bookings",
    "Customers & suppliers": "Customers and suppliers",
    Customers: "Customers",
    Suppliers: "Suppliers",
    Products: "Products",
  },
  /** @param {string} kind @param {string} product */
  formatChoice: (kind, product) => `${kind} (${product})`,
  otherVersions: "Other versions",
  /** @param {string} version */
  versionLabel: (version) => `Version ${version}`,
  notConnected: "Not connected",
  groups: {
    instance: "Your connected systems",
    registry: "Systems we know in detail",
    known: "Other systems (structure proposed by AI)",
  },
  groupHelp: {
    instance: "Your values are checked against this system (read only).",
    registry: "Fields, types and allowed values come from the official structure.",
    known: "We know the system, not its fields: the structure is an AI proposal to check.",
  },
  detecting: "Reading the columns of your file…",
  autoChosen: "Chosen from the columns of your file. You can change it.",
  start: "Analyze my file",
  startHint: "Choose a file and a target system to continue.",

  // ------------------------------------------------------------------ écran 2 : progression
  workingHeading: "Analyzing your file",
  /** @param {string} target @param {string[]} [receives] */
  workingSteps: (target, receives = []) => [
    { title: "Reading your file", help: "Header row, empty and total rows, duplicates." },
    { title: "Analyzing the columns", help: "Known names and your previous choices first, AI only for the rest." },
    { title: `Checking against ${target}`, help: checksOf(receives) },
  ],
  /** Type de données inconnu : l'utilisateur le choisit (jamais deviné). */
  dataTypeHeading: "What kind of data does your file hold?",
  dataTypeHelp: "Choose it to continue: your file is analyzed again with this choice.",
  continue: "Continue",
  workingNote: "Large files can take up to a minute. Your file is never stored.",
  /** @param {number} seconds */
  elapsed: (seconds) => `${seconds} s`,
  stepDone: "Done",
  stepRunning: "In progress",
  stepWaiting: "Waiting",
  failed: "The analysis did not finish.",
  retry: "Try again",
  otherFile: "Choose another file",

  // ------------------------------------------------------------------ écran 3 : synthèse
  /** @param {number} n @param {{ one: string, many: string }} noun */
  ready: (n, noun) => `${count(n, noun.one, noun.many)} ready`,
  /** @param {number} n @param {{ one: string, many: string }} noun */
  prepared: (n, noun) => `${count(n, noun.one, noun.many)} prepared`,
  /** @param {number} n @param {{ one: string, many: string }} noun */
  toComplete: (n, noun) => `${count(n, noun.one, noun.many)} prepared, information still missing`,
  /** @param {number} rows @param {string} file @param {string} target */
  resultMeta: (rows, file, target) => `${count(rows, "row")} read in ${file} · target ${target}`,
  /**
   * Plus de la moitié des lignes sans leurs montants (autre devise que la cible).
   * @param {{ target: string, file: string[], rows: number, fields: string[], from_system: boolean, note?: string }} notice
   * @param {{ one: string, many: string }} noun
   */
  currencyNotice: (notice, noun) =>
    `${notice.from_system ? "Your system uses" : "You chose"} ${notice.target}, your file uses ${notice.file.join(", ")}: ` +
    `${count(notice.rows, noun.one, noun.many)} prepared without a ${notice.fields.map((f) => f.toLowerCase()).join(" or ")}. ` +
    "No amount is converted." +
    (notice.note ? ` ${notice.note}` : ""),
  readyHelp: "Every check passed. Import the file as explained below.",
  preparedHelp: "Your file is prepared. Look at the points below before importing it.",
  toCompleteHelp: "Some required information is still missing: answer the questions below.",
  updating: "Updating the summary…",
  noun: {
    Products: { one: "product", many: "products" },
    Customers: { one: "customer", many: "customers" },
    Suppliers: { one: "supplier", many: "suppliers" },
    "Customers & suppliers": { one: "customer or supplier", many: "customers and suppliers" },
    Invoices: { one: "invoice", many: "invoices" },
    "Purchase invoices": { one: "purchase invoice", many: "purchase invoices" },
  },
  nounFallback: { one: "record", many: "records" },

  excludedHeading: "Rows not imported",
  /** @param {number} n */
  excludedCount: (n) => count(n, "row"),
  excludedKinds: {
    currency: "Prices in another currency (never converted)",
    duplicate: "Duplicate rows (imported once)",
    probable_duplicate: "Probable duplicates (not exported)",
    other: "Other reasons",
    already_exported: "Already exported",
  },
  /** Pièces déjà exportées, résumées par date de l'export précédent.
   * @param {{ date: string, count: number }[]} groups @param {{ one: string, many: string }} noun */
  alreadyExported: (groups, noun) => {
    const total = groups.reduce((n, g) => n + g.count, 0);
    const what = `${num(total)} ${total === 1 ? noun.one : noun.many}`;
    const on = (/** @type {string} */ d) => (d ? `on ${d}` : "on an earlier date");
    if (groups.length === 1) return `${what} already exported ${on(groups[0].date)}: not exported again`;
    const dates = groups.map((g) => `${num(g.count)} ${on(g.date)}`).join(", ");
    return `${what} already exported (${dates}): not exported again`;
  },
  alreadyExportedDetails: "Show them",
  /** @param {{ one: string, many: string }} noun */
  nothingNewHelp: (noun) => `Nothing new to export: every ${noun.one} of this file was already exported.`,
  /** @param {number} shown @param {number} total */
  excludedMore: (shown, total) =>
    `The first ${num(shown)} of ${count(total, "row")} are listed here; the prepared file lists them all (sheet “Rows not imported”).`,
  notWrittenHeading: "Values left empty",
  notWrittenHelp: "These values are not written; the rest of each row is imported.",
  toCheckHeading: "To check",
  transformsHeading: "Written differently",
  transformsHelp: "Same value, another spelling, as you decided above.",
  /** @param {number} n */
  transformCount: (n) => count(n, "value"),
  missingHeading: "Still missing",
  /** @param {string} fields */
  missingText: (fields) => `${fields}: required by the target, no value in your file yet.`,
  rowsLabel: "Rows",
  /** @param {number} row */
  rowLabel: (row) => `Row ${row}:`,
  fixLabel: "How to fix",

  appliedHeading: "Decided for you",
  appliedHelp: "Usual choices, already applied. Change any of them with one click.",
  settingsHeading: "Your settings",
  mappingsHeading: "Accounts by category",
  mappingsHelp: "Preselected usual accounts: confirm them with your tax advisor.",
  mappingColumns: { category: "Category", rate: "VAT", account: "Account", taxKey: "Tax key" },
  automaticAccount: "none (automatic account)",
  /** @param {string} rate */
  taxKeyShared: (rate) => `The tax key applies to every category at ${rate} % VAT.`,
  fileDecisionsHeading: "About your file",
  change: "Change",
  done: "Done",
  defaultFrom: {
    your_answers: "from your answers",
    your_system: "from your system",
    your_settings: "from your settings",
    registry_first_value: "usual value",
    your_file: "found in your file",
    usual_default: "usual choice",
  },

  questionsHeading: "Your decisions",
  /** @param {number} n */
  /** @param {number} n @param {number} [preselected] */
  questionsHelp: (n, preselected = n) =>
    preselected === n
      ? n === 1
        ? "One decision, already preselected."
        : `${num(n)} decisions, each already preselected.`
      : preselected === 0
        ? n === 1
          ? "One decision to answer."
          : `${num(n)} decisions to answer.`
        : `${num(n)} decisions, ${num(preselected)} already preselected.`,
  preselected: "Preselected",
  /** @param {string} header @param {string[]} examples */
  columnOption: (header, examples) =>
    examples.length ? `“${header}” (for example ${examples.slice(0, 3).join(", ")})` : `“${header}”`,
  otherValue: "Other value",
  yourAnswer: "Your answer",
  otherPlaceholder: "Type a value",

  confirmHeading: "Columns to confirm",
  confirmHelp: "We are not sure about these columns. Confirm them, or choose another field.",
  confirmAll: "Confirm all",
  confirm: "Confirm",
  confirmed: "Confirmed",
  notImported: "Not imported",
  /** @param {number} n */
  pendingConfirm: (n) =>
    n === 1 ? "1 column is not imported until you confirm it." : `${num(n)} columns are not imported until you confirm them.`,
  automaticHeading: "Matched automatically",
  /** @param {number} n */
  automaticCount: (n) => count(n, "column"),
  ignoredHeading: "Not imported",
  /** @param {number} n */
  ignoredCount: (n) => `${count(n, "column")} of your file`,
  showList: "Show",
  hideList: "Hide",

  downloadHeading: "Download",
  download: "Download prepared file",
  downloading: "Preparing the download…",
  importOrderHeading: "Import order",
  /** @param {string} sheet @param {number} n @param {string} label */
  importFirst: (sheet, n, label) => `Import the sheet “${sheet}” first: ${count(n, label.toLowerCase())} to create.`,
  /** @param {{ one: string, many: string }} noun */
  importData: (noun) => `Then import the sheet “Data”: your ${noun.many}.`,
  /** @param {string} target @param {{ one: string, many: string }} noun */
  importFormat: (target, noun) =>
    `Import this file in ${target}: your ${noun.many}, in the format of ${target} (nothing is sent automatically).`,
  /** @param {{ one: string, many: string }} noun */
  importOnly: (noun) => `Import the sheet “Data”: your ${noun.many}.`,
  /** @param {string} fields */
  targetDefaults: (fields) => `${fields}: left empty, the target uses its own default value.`,
  learnNote: "Your confirmed choices are remembered for your next files.",
  startOver: "Start over",
};
