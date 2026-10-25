// @ts-check
/**
 * AI Migration Wizard : logique PURE du parcours (historique, réponse choisie, progression, retour arrière,
 * filtrage et sections des choix, statuts). Aucune question ni aucun choix en dur : tout vient de l'API
 * (Registry d'abord, IA ensuite). Exécutable tel quel par `node --test` (flow.test.mjs).
 *
 * Statuts, trois niveaux jamais confondus : Verified schema (schéma publié du Registry), Known system (système
 * identifié par le Registry, SANS schéma d'import vérifié), AI proposal (proposé par le LLM).
 */

/** Réponse libre (« Other ») : sa valeur est le texte saisi. */
export const OTHER = "__other__";
export const STORAGE_KEY = "nexa45.migration-wizard.v2";
/** Au-delà, la liste des choix devient filtrable et s'affiche progressivement. */
export const LONG_LIST = 8;
/** Nombre de choix montrés avant « Show all » (sans recherche). */
export const FIRST_PAGE = 9;

/** @typedef {"registry" | "known" | "ai" | "user"} Source */
/** @typedef {"success" | "info" | "warning" | "neutral"} Tone */
/** @typedef {{ value: string, label: string, description: string | null, group: string | null, source: Source }} Choice */
/** @typedef {{ id: string, topic: string, title: string, help: string | null, choices: Choice[], allow_other: boolean, other_label: string | null }} Question */
/** Réponse : aussi « file » (déduite du fichier par des règles, démarrage « fichier d'abord » de l'API).
 * @typedef {{ question_id: string, topic: string, question: string, value: string, label: string, source: Source | "file" }} Answer */
/** @typedef {{ question: Question, answer: Answer }} Step */

/** Bandeau de la structure cible d'après son niveau de vérification.
 * @param {"verified" | "partial" | "ai"} level
 * @returns {{ tone: Tone, title: string }} */
export function verificationBanner(level) {
  switch (level) {
    case "verified":
      return {
        tone: "success",
        title: "Verified schema: every field was found in the Nexa45 Registry",
      };
    case "partial":
      return {
        tone: "warning",
        title:
          "Partly verified schema: fields marked “AI proposal” are not verified",
      };
    default:
      return {
        tone: "warning",
        title:
          "AI proposal: this is not a verified import schema of the target system",
      };
  }
}

/** Réponse à une question, d'après le choix sélectionné (ou le texte libre). `null` : rien d'utilisable.
 * @param {Question} question
 * @param {string} selected  valeur du choix, ou OTHER
 * @param {string} otherText
 * @returns {Answer | null} */
export function buildAnswer(question, selected, otherText) {
  const base = {
    question_id: question.id,
    topic: question.topic,
    question: question.title,
  };
  if (selected === OTHER) {
    const text = otherText.trim().slice(0, 300);
    return text && question.allow_other
      ? { ...base, value: text, label: text, source: "user" }
      : null;
  }
  const choice = question.choices.find((c) => c.value === selected);
  return choice
    ? {
        ...base,
        value: choice.value,
        label: choice.label,
        source: choice.source,
      }
    : null;
}

/** Réponses dans l'ordre, telles que l'API les attend.
 * @param {Step[]} history
 * @returns {Answer[]} */
export function answersOf(history) {
  return history.map((s) => s.answer);
}

/** Sélection à restaurer quand on revient sur une question déjà répondue.
 * @param {Step} step
 * @returns {{ selected: string, otherText: string }} */
export function selectionOf(step) {
  const known = step.question.choices.some(
    (c) => c.value === step.answer.value,
  );
  return known
    ? { selected: step.answer.value, otherText: "" }
    : { selected: OTHER, otherText: step.answer.label };
}

/** Retour arrière : la dernière question répondue redevient la question courante, sa réponse pré-sélectionnée.
 * @param {Step[]} history
 * @returns {{ history: Step[], current: Question, selected: string, otherText: string } | null} */
export function back(history) {
  const last = history.at(-1);
  if (!last) return null;
  return {
    history: history.slice(0, -1),
    current: last.question,
    ...selectionOf(last),
  };
}

/** Avancement en pourcentage : questions répondues sur le total estimé (répondues + courante + restantes).
 * @param {number} answered
 * @param {number} remaining  estimation après la question courante
 * @param {boolean} done
 * @returns {number} */
export function progress(answered, remaining, done) {
  if (done) return 100;
  const total = answered + 1 + Math.max(0, remaining);
  return Math.min(95, Math.round((answered / total) * 100));
}

/** Choix dont le libellé, la description ou la catégorie contient le texte cherché (sans casse ni accents).
 * @param {Choice[]} choices
 * @param {string} query
 * @returns {Choice[]} */
export function filterChoices(choices, query) {
  const fold = (/** @type {string} */ t) =>
    t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const q = fold(query.trim());
  if (!q) return choices;
  return choices.filter((c) =>
    fold(`${c.label} ${c.description ?? ""} ${c.group ?? ""}`).includes(q),
  );
}

const SECTION_ORDER = /** @type {const} */ ([
  "registry",
  "known",
  "ai",
  "user",
]);
/** @type {Readonly<Record<Source, string>>} */
const SECTION_TITLE = {
  registry: "Verified schema",
  known: "Known systems",
  ai: "AI proposals",
  user: "Your answers",
};

/** Sections dans l'ordre : schéma vérifié du Registry, systèmes connus, propositions de l'IA. Ordre des choix
 * conservé dans chaque section ; titre seulement quand il y a plusieurs sections.
 * @param {Choice[]} choices
 * @returns {{ source: Source, title: string | null, choices: Choice[] }[]} */
export function sectionChoices(choices) {
  const sections = SECTION_ORDER.map((source) => ({
    source,
    title: SECTION_TITLE[source],
    choices: choices.filter((c) => c.source === source),
  })).filter((s) => s.choices.length > 0);
  return sections.map((s) => ({
    ...s,
    title: sections.length > 1 ? s.title : null,
  }));
}

/** Affichage progressif : les premiers choix seulement, sauf recherche en cours, « Show all », ou choix
 * sélectionné plus loin (toujours visible).
 * @param {Choice[]} choices  déjà filtrés
 * @param {{ query: string, showAll: boolean, selected: string }} state
 * @returns {{ visible: Choice[], hidden: number }} */
export function visibleChoices(choices, { query, showAll, selected }) {
  if (showAll || query.trim() || choices.length <= FIRST_PAGE)
    return { visible: choices, hidden: 0 };
  const first = choices.slice(0, FIRST_PAGE);
  const chosen = choices.find((c) => c.value === selected);
  const visible =
    chosen && !first.includes(chosen) ? [...first, chosen] : first;
  return { visible, hidden: choices.length - visible.length };
}

// --------------------------------------------------------------------------- //
// Fichier du client : correspondances colonne → champ cible, confirmations, mode d'affichage
// --------------------------------------------------------------------------- //
/** @typedef {"matched" | "ai_match" | "to_confirm" | "unmatched" | "empty" | "previous"} ColumnStatus */
/** @typedef {{ position: number, header: string, filled: number, examples: string[], status: ColumnStatus, target: string | null, confidence: number | null, probably_not_needed: boolean }} ColumnMapping */
/** @typedef {{ name: string, label: string }} FieldRef */

/** Décisions de départ : la cible proposée (nom exact, IA sûre ou incertaine), sinon « non importée ».
 * @param {ColumnMapping[]} columns
 * @returns {Record<number, string | null>} */
export function initialDecisions(columns) {
  return Object.fromEntries(
    columns.map((c) => [c.position, c.status === "empty" ? null : c.target]),
  );
}

/** Choix retenus pour chaque colonne, renvoyés à l'analyse suivante du même fichier pour qu'une structure
 * régénérée ne fasse perdre aucune correspondance : toute colonne qui a un champ, et « Not imported » SEULEMENT
 * s'il a été choisi par l'utilisateur. Le « Not imported » par défaut d'une colonne non reconnue (IA indisponible,
 * aucune proposition) n'est jamais retenu : sinon la colonne ne serait plus jamais analysée.
 * @param {ColumnMapping[]} columns
 * @param {Record<number, string | null>} decisions
 * @param {ReadonlySet<number>} [explicit]  colonnes décidées par l'utilisateur (choisies ou confirmées)
 * @returns {{ header: string, target: string | null }[]} */
export function rememberChoices(columns, decisions, explicit = new Set()) {
  return columns
    .filter((c) => c.status !== "empty" && c.position in decisions)
    .filter((c) => decisions[c.position] || explicit.has(c.position))
    .map((c) => ({ header: c.header, target: decisions[c.position] ?? null }));
}

/** Colonnes encore à confirmer : proposition incertaine que l'utilisateur n'a ni confirmée ni changée.
 * @param {ColumnMapping[]} columns
 * @param {ReadonlySet<number>} confirmed
 * @returns {ColumnMapping[]} */
export function pendingConfirmations(columns, confirmed) {
  return columns.filter(
    (c) => c.status === "to_confirm" && !confirmed.has(c.position),
  );
}

/** Champs déjà choisis par une AUTRE colonne (un champ cible ne reçoit qu'une colonne).
 * @param {Record<number, string | null>} decisions
 * @param {number} position
 * @returns {Set<string>} */
export function takenTargets(decisions, position) {
  return new Set(
    Object.entries(decisions)
      .filter(([pos, t]) => Number(pos) !== position && t)
      .map(([, t]) => /** @type {string} */ (t)),
  );
}

/** Décisions au format de l'API.
 * @param {Record<number, string | null>} decisions
 * @returns {{ position: number, target: string | null }[]} */
export function decisionList(decisions) {
  return Object.entries(decisions).map(([pos, target]) => ({
    position: Number(pos),
    target,
  }));
}

/** Nom affiché d'un champ cible : libellé seul en mode simple (jamais le nom technique), les deux en expert.
 * @param {FieldRef} field
 * @param {boolean} expert */
export function fieldName(field, expert) {
  return expert && field.label !== field.name
    ? `${field.label} (${field.name})`
    : field.label;
}

/** Statut du fichier préparé : jamais « prêt à importer » ; tant qu'un champ obligatoire reste à compléter, le
 * fichier est dit NON prêt ; seul un schéma vérifié est dit vérifié.
 * @param {"verified" | "partial" | "ai"} level
 * @param {number} [toComplete]  champs obligatoires encore sans valeur
 * @returns {{ tone: Tone, title: string }} */
export function preparedBanner(level, toComplete = 0) {
  if (toComplete > 0) {
    return {
      tone: "warning",
      title: `Not ready for import: ${toComplete} required field${toComplete > 1 ? "s" : ""} still to complete.`,
    };
  }
  return level === "verified"
    ? {
        tone: "success",
        title:
          "Prepared with a verified schema. Check the values before any import.",
      }
    : {
        tone: "warning",
        title:
          "Prepared for review: the target format is not officially verified. Check it before any import.",
      };
}

/** « Type de données » d'après les réponses (sujet de l'objet métier, sinon de la donnée), à défaut le titre de
 * la structure. Aucune valeur en dur : seulement des sujets de question, comme les renvoie l'API.
 * @param {Answer[]} answers
 * @param {string} fallback
 * @returns {string} */
export function dataTypeOf(answers, fallback) {
  const exact = answers.find((a) => a.topic === "data_type");
  if (exact) return exact.label;
  const excluded = ["subtype", "sub_type", "scope", "category"];
  for (const key of [
    "business_object",
    "object",
    "domain",
    "entity",
    "data_kind",
    "kind_of_data",
  ]) {
    const hit = answers.find(
      (a) =>
        a.topic.includes(key) && !excluded.some((x) => a.topic.includes(x)),
    );
    if (hit) return hit.label;
  }
  return fallback;
}

// --------------------------------------------------------------------------- //
// « Complete missing information » : une question par champ obligatoire sans valeur
// --------------------------------------------------------------------------- //
/** @typedef {{ value: string, label: string, description: string | null, verified: boolean }} MissingChoice */
/** @typedef {{ field: string, label: string, kind: string, title: string, help: string | null, choices: MissingChoice[], free_text: boolean, warning: string | null }} MissingQuestion */
/** @typedef {{ how: "choice" | "other" | "per_row" | "hint" | "unknown" | "", value: string, other: string, perRow: Record<number, string>, confirmed: boolean }} MissingAnswer */

/** @returns {MissingAnswer} */
export function emptyMissingAnswer() {
  return { how: "", value: "", other: "", perRow: {}, confirmed: false };
}

/** Réponse de départ : la valeur DÉDUITE des réponses précédentes, pré-sélectionnée (à confirmer).
 * @param {MissingQuestion & { suggested?: string | null }} q
 * @returns {MissingAnswer} */
export function initialMissingAnswer(q) {
  if (!q.suggested) return emptyMissingAnswer();
  return q.choices.some((c) => c.value === q.suggested)
    ? { ...emptyMissingAnswer(), how: "choice", value: q.suggested }
    : { ...emptyMissingAnswer(), how: "other", other: q.suggested };
}

/** La réponse affichée est-elle la valeur déduite des réponses précédentes ?
 * @param {MissingQuestion & { suggested?: string | null }} q
 * @param {MissingAnswer} a */
export function isSuggested(q, a) {
  if (!q.suggested) return false;
  return (
    (a.how === "choice" && a.value === q.suggested) ||
    (a.how === "other" && a.other.trim() === q.suggested)
  );
}

/** Valeurs saisies pour la question (commune ou par ligne), sans les vides.
 * @param {MissingAnswer} a
 * @returns {string[]} */
function usedValues(a) {
  if (a.how === "choice") return a.value ? [a.value] : [];
  if (a.how === "other") return a.other.trim() ? [a.other.trim()] : [];
  if (a.how === "per_row")
    return Object.values(a.perRow)
      .map((v) => v.trim())
      .filter(Boolean);
  return [];
}

/** Une valeur non vérifiable (saisie libre, suggestion de l'IA, référence) exige une confirmation explicite ;
 * seule une valeur admise du Registry ou lue dans le système n'en demande pas. Une réponse métier ou « je ne
 * sais pas » n'écrit aucune valeur : rien à confirmer (le champ reste signalé).
 * @param {MissingQuestion} q
 * @param {MissingAnswer} a */
export function needsConfirmation(q, a) {
  const verified = new Set(
    q.choices.filter((c) => c.verified).map((c) => c.value),
  );
  return usedValues(a).some((v) => !verified.has(v));
}

/** On peut continuer : une valeur (confirmée si nécessaire), une réponse métier, ou « je ne sais pas ».
 * @param {MissingQuestion} q
 * @param {MissingAnswer} a */
export function canContinue(q, a) {
  if (a.how === "unknown") return true;
  if (a.how === "hint") return Boolean(a.value);
  return usedValues(a).length > 0 && (!needsConfirmation(q, a) || a.confirmed);
}

/** Réponse au format de l'API (valeur commune, valeurs par ligne, réponse métier ou « je ne sais pas »).
 * @param {MissingQuestion} q
 * @param {MissingAnswer} a
 * @returns {{ field: string, common: string | null, per_row: Record<number, string>, confirmed: boolean, hint?: string, unknown?: boolean } | null} */
export function fillOf(q, a) {
  if (a.how === "unknown")
    return {
      field: q.field,
      common: null,
      per_row: {},
      confirmed: false,
      unknown: true,
    };
  if (a.how === "hint") {
    return a.value
      ? {
          field: q.field,
          common: null,
          per_row: {},
          confirmed: false,
          hint: a.value,
        }
      : null;
  }
  if (!usedValues(a).length) return null;
  const confirmed = needsConfirmation(q, a) && a.confirmed;
  if (a.how === "per_row") {
    const perRow = Object.fromEntries(
      Object.entries(a.perRow).filter(([, v]) => v.trim()),
    );
    return { field: q.field, common: null, per_row: perRow, confirmed };
  }
  return {
    field: q.field,
    common: a.how === "choice" ? a.value : a.other.trim(),
    per_row: {},
    confirmed,
  };
}

/** Réponse déjà donnée → réponse affichée (la question revient pré-remplie, jamais vide).
 * @param {MissingQuestion & { suggested?: string | null, previous_hint?: string | null }} q
 * @param {{ common: string | null, per_row: Record<number, string>, confirmed: boolean, hint?: string, unknown?: boolean } | undefined} fill
 * @returns {MissingAnswer} */
export function answerFromFill(q, fill) {
  // une valeur trouvée dans le système connecté pour la réponse métier passe avant la réponse métier
  if (q.suggested && q.previous_hint) return initialMissingAnswer(q);
  if (!fill) return initialMissingAnswer(q);
  if (fill.unknown) return { ...emptyMissingAnswer(), how: "unknown" };
  if (fill.hint)
    return { ...emptyMissingAnswer(), how: "hint", value: fill.hint };
  if (Object.keys(fill.per_row).length) {
    return {
      ...emptyMissingAnswer(),
      how: "per_row",
      perRow: { ...fill.per_row },
      confirmed: fill.confirmed,
    };
  }
  const value = fill.common ?? "";
  return q.choices.some((c) => c.value === value)
    ? {
        ...emptyMissingAnswer(),
        how: "choice",
        value,
        confirmed: fill.confirmed,
      }
    : {
        ...emptyMissingAnswer(),
        how: "other",
        other: value,
        confirmed: fill.confirmed,
      };
}

/** Bandeau du fichier : « Ready for import » seulement si le SERVEUR a constaté que tous les contrôles sont passés ;
 * sinon « Prepared » (fichier préparé, à revoir avant l'import) ou « à compléter ».
 * @param {"to_complete" | "prepared" | "ready"} status
 * @param {number} toComplete
 * @returns {{ tone: Tone, title: string }} */
export function readinessBanner(status, toComplete) {
  if (status === "ready")
    return {
      tone: "success",
      title: "Ready for import: every check below passed.",
    };
  if (status === "to_complete" || toComplete > 0)
    return preparedBanner("ai", Math.max(toComplete, 1));
  return {
    tone: "warning",
    title:
      "Prepared, not yet ready for import: fix the problems below before importing this file.",
  };
}

/** Pastille du fichier : trois états jamais confondus (« Prepared » n'est pas « Ready for import »).
 * @param {"to_complete" | "prepared" | "ready"} status
 * @param {number} toComplete
 * @returns {{ tone: Tone, label: string }} */
export function readinessLabel(status, toComplete) {
  if (status === "ready") return { tone: "success", label: "Ready for import" };
  if (status === "to_complete" || toComplete > 0)
    return { tone: "warning", label: "To complete" };
  return { tone: "warning", label: "Prepared" };
}

/** Valeurs DÉDUITES des réponses précédentes et VÉRIFIÉES (valeur admise du Registry) : appliquées d'office,
 * montrées avec leur explication et modifiables. Une valeur non vérifiable n'est jamais appliquée d'office.
 * @param {(MissingQuestion & { suggested?: string | null, explanation?: string | null })[]} questions
 * @returns {{ fills: { field: string, common: string, per_row: Record<number, string>, confirmed: boolean, auto: boolean }[], applied: { field: string, label: string, value: string, explanation: string | null }[] }} */
export function autoFills(questions) {
  /** @type {{ field: string, common: string, per_row: Record<number, string>, confirmed: boolean, auto: boolean }[]} */
  const fills = [];
  /** @type {{ field: string, label: string, value: string, explanation: string | null }[]} */
  const applied = [];
  for (const q of questions) {
    if (!q.suggested || (q.kind !== "choice" && q.kind !== "boolean")) continue;
    const choice = q.choices.find((c) => c.value === q.suggested && c.verified);
    if (!choice) continue;
    fills.push({
      field: q.field,
      common: choice.value,
      per_row: {},
      confirmed: false,
      auto: true,
    });
    applied.push({
      field: q.field,
      label: q.label,
      value: choice.label,
      explanation: q.explanation ?? null,
    });
  }
  return { fills, applied };
}

/** Réponses données AVANT le fichier puis APRÈS (« Complete missing information ») : une seule par champ, la plus
 * récente gagne ; jamais de doublon envoyé au serveur.
 * @template {{ field: string }} F
 * @param {F[]} before
 * @param {F[]} after
 * @returns {F[]} */
export function mergeFills(before, after) {
  const later = new Set(after.map((f) => f.field));
  return [...before.filter((f) => !later.has(f.field)), ...after];
}

/** Réponses données avant le fichier, en clair pour « Your answers » : libellé du champ et valeur affichée (libellé
 * du choix, réponse métier, « Not known yet »). Rien n'est affiché pour une question passée.
 * @param {{ field: string, label: string, choices: { value: string, label: string }[] }[]} questions
 * @param {{ field: string, common?: string | null, hint?: string, unknown?: boolean }[]} fills
 * @returns {{ field: string, label: string, value: string }[]} */
export function earlyShown(questions, fills) {
  /** @type {{ field: string, label: string, value: string }[]} */
  const out = [];
  for (const f of fills) {
    const q = questions.find((x) => x.field === f.field);
    if (!q) continue;
    const value = f.unknown
      ? "Not known yet"
      : f.hint ||
        (q.choices.find((c) => c.value === f.common)?.label ?? f.common ?? "");
    if (value) out.push({ field: f.field, label: q.label, value });
  }
  return out;
}
