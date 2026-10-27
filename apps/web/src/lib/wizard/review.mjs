// @ts-check
/**
 * AI Migration Wizard — « Check the mapping » : logique PURE de la revue des correspondances (node --test,
 * review.test.mjs). Un fichier de centaines de lignes et de dizaines de colonnes se revoit SANS examiner chaque
 * colonne : en Simple, seules les décisions nécessaires (correspondance incertaine) sont posées, une par une ; les
 * correspondances automatiques restent corrigeables, les colonnes non importées sont regroupées et nommées, jamais
 * perdues en silence. Aucun système, aucun champ, aucune valeur en dur.
 */
import { pendingConfirmations } from "./flow.mjs";

/** @typedef {import("./flow.mjs").ColumnMapping} ColumnMapping */
/** @typedef {"not_recognized" | "probably_not_needed" | "your_choice"} NotImportedReason */

/** Longueur d'un exemple en Simple (les longues valeurs structurées sont retirées). */
export const SIMPLE_EXAMPLE_MAX = 60;
/** Longueur d'un exemple en Expert avant « Show more ». */
export const EXPERT_EXAMPLE_MAX = 150;
/** Correspondances trouvées sans intervention : nom exact, IA sûre, choix précédent réappliqué. */
const AUTOMATIC = new Set(["matched", "ai_match", "previous"]);

/** Texte coupé à `max` caractères (points de suspension compris).
 * @param {string} text
 * @param {number} max
 * @returns {{ text: string, clipped: boolean }} */
export function clip(text, max) {
  if (text.length <= max) return { text, clipped: false };
  return { text: `${text.slice(0, max - 1).trimEnd()}…`, clipped: true };
}

/** Valeur structurée (JSON objet ou liste) : illisible pour un débutant.
 * @param {string} value */
export function looksStructured(value) {
  const t = value.trim();
  return (t.startsWith("{") && t.endsWith("}")) || (t.startsWith("[") && t.endsWith("]"));
}

/** Exemples montrés en Simple : sans valeur vide ni longue valeur structurée, coupés, trois au plus.
 * @param {string[]} examples
 * @returns {string[]} */
export function simpleExamples(examples, max = SIMPLE_EXAMPLE_MAX, count = 3) {
  return examples
    .filter((e) => e.trim() && !(looksStructured(e) && e.length > max))
    .slice(0, count)
    .map((e) => clip(e, max).text);
}

/** Exemples montrés en Expert : tous, coupés à `max` sauf « Show more ».
 * @param {string[]} examples
 * @param {boolean} expanded
 * @returns {{ examples: string[], clipped: boolean }} */
export function expertExamples(examples, expanded, max = EXPERT_EXAMPLE_MAX) {
  const clipped = examples.some((e) => e.length > max);
  return { examples: expanded ? examples : examples.map((e) => clip(e, max).text), clipped };
}

/** Répartition des colonnes : à décider (incertaines non confirmées), importées, non importées, vides. Chaque
 * colonne est dans UN groupe : aucune ne disparaît.
 * @param {ColumnMapping[]} columns
 * @param {Record<number, string | null>} decisions
 * @param {ReadonlySet<number>} confirmed */
export function reviewGroups(columns, decisions, confirmed) {
  const withData = columns.filter((c) => c.status !== "empty");
  const pending = pendingConfirmations(withData, confirmed);
  const open = new Set(pending.map((c) => c.position));
  const settled = withData.filter((c) => !open.has(c.position));
  return {
    pending,
    mapped: settled.filter((c) => decisions[c.position]),
    notImported: settled.filter((c) => !decisions[c.position]),
    empty: columns.filter((c) => c.status === "empty"),
  };
}

/** Correspondance trouvée et gardée telle quelle (aucune décision de l'utilisateur).
 * @param {ColumnMapping} column
 * @param {Record<number, string | null>} decisions */
export function isAutomatic(column, decisions) {
  return AUTOMATIC.has(column.status) && Boolean(column.target) && decisions[column.position] === column.target;
}

/** Résumé en tête de l'écran.
 * @param {number} rows
 * @param {ReturnType<typeof reviewGroups>} groups
 * @param {Record<number, string | null>} decisions */
export function mappingSummary(rows, groups, decisions) {
  const automatic = groups.mapped.filter((c) => isAutomatic(c, decisions)).length;
  return {
    rows,
    automatic,
    chosen: groups.mapped.length - automatic,
    toConfirm: groups.pending.length,
    notImported: groups.notImported.length,
  };
}

/** Pourquoi une colonne avec des valeurs n'est pas importée.
 * @param {ColumnMapping} column
 * @returns {NotImportedReason} */
export function notImportedReason(column) {
  if (column.status !== "unmatched") return "your_choice";
  return column.probably_not_needed ? "probably_not_needed" : "not_recognized";
}

const REASON_ORDER = /** @type {const} */ (["not_recognized", "your_choice", "probably_not_needed"]);
/** @type {Readonly<Record<NotImportedReason, string>>} */
const REASON_TITLE = {
  not_recognized: "Not recognized",
  your_choice: "Not imported by your choice",
  probably_not_needed: "Probably not needed",
};

/** Colonnes non importées par raison (non reconnues d'abord : les plus susceptibles de cacher une donnée utile).
 * @param {ColumnMapping[]} notImported
 * @returns {{ reason: NotImportedReason, title: string, columns: ColumnMapping[] }[]} */
export function notImportedGroups(notImported) {
  return REASON_ORDER.map((reason) => ({
    reason,
    title: REASON_TITLE[reason],
    columns: notImported.filter((c) => notImportedReason(c) === reason),
  })).filter((g) => g.columns.length > 0);
}

/** Décision courante en Simple : la première correspondance incertaine encore ouverte, avec son rang
 * (« Decision 2 of 5 ») sur toutes les correspondances incertaines du fichier.
 * @param {ColumnMapping[]} columns
 * @param {ReadonlySet<number>} confirmed */
export function currentDecision(columns, confirmed) {
  const total = columns.filter((c) => c.status === "to_confirm").length;
  const pending = pendingConfirmations(columns, confirmed);
  return { column: pending[0] ?? null, index: total - pending.length + 1, total };
}

// --------------------------------------------------------------------------- //
// Décisions sur les VALEURS : posées par le serveur (moteur de valeurs), une par colonne et par sorte
// --------------------------------------------------------------------------- //
/** @typedef {{ value: string, label: string, examples?: string[][] }} QuestionOption */
/** @typedef {{ key: string, kind: string, title: string, help: string, count: number, options: QuestionOption[], allow_other?: boolean, suggested?: string | null, suggested_by?: string | null, answer?: string | null }} ValueQuestion */

/** Questions encore ouvertes (aucune option choisie), dans l'ordre du serveur.
 * @template {ValueQuestion} Q
 * @param {Q[]} questions
 * @returns {Q[]} */
export function openQuestions(questions) {
  return questions.filter((q) => !q.answer);
}

/** Question courante en Simple et son rang (« Decision 2 of 3 ») sur toutes les questions du fichier.
 * @template {ValueQuestion} Q
 * @param {Q[]} questions
 * @returns {{ question: Q | null, index: number, total: number }} */
export function currentQuestion(questions) {
  const open = openQuestions(questions);
  return { question: open[0] ?? null, index: questions.length - open.length + 1, total: questions.length };
}

/** Choix envoyés au serveur après une réponse ; `null` retire la réponse (la question est reposée).
 * @param {Record<string, string>} choices
 * @param {string} key
 * @param {string | null} value
 * @returns {Record<string, string>} */
export function withChoice(choices, key, value) {
  const next = { ...choices };
  if (value) next[key] = value;
  else delete next[key];
  return next;
}

/** Libellé de la réponse donnée (option connue, sinon la valeur saisie : autre code de devise).
 * @param {ValueQuestion} question */
export function answerLabel(question) {
  return question.options.find((o) => o.value === question.answer)?.label ?? question.answer ?? "";
}

/** Code de devise saisi : trois lettres (ISO 4217), en majuscules ; sinon null.
 * @param {string} text */
export function currencyCode(text) {
  const code = text.trim().toUpperCase();
  return /^[A-Z]{3}$/.test(code) ? code : null;
}

/** Lignes montrées par problème avant « … et N autres » (la liste complète est dans le fichier préparé). */
export const ROWS_SHOWN = 12;

/** Clé d'affichage d'un problème : sorte, colonne ET message (plusieurs avertissements de la cible ont la même
 * sorte et aucune colonne : un message par avertissement).
 * @param {{ kind: string, column: string, message: string }} issue */
export function issueKey(issue) {
  return `${issue.kind}-${issue.column}-${issue.message}`;
}

/** Lignes concernées par un problème, en clair : « Row 4 », « Rows 2, 5, 9 and 37 more » ; `count` = total.
 * @param {number[]} rows
 * @param {number} count
 * @param {number} [max] */
export function rowsText(rows, count, max = ROWS_SHOWN) {
  if (!rows.length) return "";
  const shown = rows.slice(0, max);
  const more = Math.max(count, rows.length) - shown.length;
  const head = `${shown.length + more > 1 ? "Rows" : "Row"} ${shown.join(", ")}`;
  return more > 0 ? `${head} and ${more} more` : head;
}

/** @typedef {{ kind: string, severity?: "not_written" | "to_check" | "serious" | "info", rows?: number[], count: number, fix?: string, message: string }} Problem */

/** Problèmes du fichier préparé par groupe : anomalies GRAVES (en tête de page), valeurs NON écrites (y compris
 * celles écartées par un choix de l'utilisateur : rien n'est caché), valeurs écrites (ou absentes) à vérifier
 * avant l'import, informations.
 * @template {Problem} T
 * @param {T[]} issues
 * @returns {{ serious: T[], notWritten: T[], toCheck: T[], info: T[] }} */
export function problemGroups(issues) {
  return {
    serious: issues.filter((i) => i.severity === "serious"),
    notWritten: issues.filter((i) => (i.severity ?? "not_written") === "not_written"),
    toCheck: issues.filter((i) => i.severity === "to_check"),
    info: issues.filter((i) => i.severity === "info"),
  };
}
