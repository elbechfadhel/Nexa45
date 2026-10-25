// @ts-check
/**
 * AI Migration Wizard — mode Simple « fichier d'abord » : logique PURE des trois écrans (node --test,
 * simple.test.mjs). Cibles groupées, réponses présélectionnées, correspondances à confirmer, corps envoyé au
 * serveur (choices / fills / decisions / confirmed), synthèse et ordre d'import. Aucun système, champ ni valeur en
 * dur : tout vient de l'API ; les textes viennent de `simple-labels.mjs`.
 */
import { SIMPLE_LABELS as L } from "./simple-labels.mjs";

/** @typedef {{ value: string, label: string, group: "instance" | "registry" | "known", detail?: string | null, connected?: boolean, system?: string | null, system_name?: string | null, format?: string | null, product?: string | null, product_name?: string | null, version?: string | null, latest?: boolean, receives?: string[] }} TargetChoice */
/**
 * Tuile de l'écran 1 : une cible, ou un système à plusieurs formats (une tuile, le format se choisit ensuite).
 * @typedef {{ kind: "target", key: string, target: TargetChoice } | { kind: "system", key: string, system: string, label: string, formats: TargetChoice[] }} TargetTile
 */
/** @typedef {{ value: string, label: string, description?: string | null, verified?: boolean }} Choice */
/** @typedef {{ field: string, label: string, kind: string, title: string, help?: string | null, choices: Choice[], free_text: boolean }} Question */
/** @typedef {{ question: Question, default?: string | null, default_from?: string | null, decision?: string | null }} QuickQuestion */
/** @typedef {{ position: number, header: string, status: string, target?: string | null, examples: string[], filled: number, confidence?: number | null, probably_not_needed?: boolean }} Column */
/** @typedef {{ field: string, common?: string | null, per_row?: Record<string, string>, confirmed?: boolean }} Fill */
/** @typedef {{ position: number, target?: string | null }} Decision */
/**
 * @typedef {{
 *   data_type: string,
 *   target_label: string,
 *   choices: Record<string, string>,
 *   questions: QuickQuestion[],
 *   applied: QuickQuestion[],
 *   fills: Fill[],
 *   decisions: Decision[],
 *   mapping: { file_name: string, rows: number, columns: Column[] },
 *   proposal: { fields: { name: string, label: string }[] },
 * }} QuickStart
 */
/**
 * Réponses de l'utilisateur sur l'écran de synthèse : une valeur par question (clé = `questionKey`), les
 * colonnes confirmées et le champ choisi pour chacune (position → champ ou null « Not imported »).
 * @typedef {{ answers: Record<string, string>, confirmed: number[], targets: Record<number, string | null> }} Edits
 */

/** Cibles par groupe, dans l'ordre de l'API (vos systèmes, Registry, systèmes connus), filtrées par la recherche.
 * @param {TargetChoice[]} targets
 * @param {string} search
 * @returns {{ group: TargetChoice["group"], items: TargetChoice[] }[]} */
export function targetGroups(targets, search = "") {
  const q = search.trim().toLowerCase();
  const shown = q
    ? targets.filter((t) => `${t.label} ${t.detail ?? ""}`.toLowerCase().includes(q))
    : targets;
  /** @type {TargetChoice["group"][]} */
  const order = ["instance", "registry", "known"];
  return order
    .map((group) => ({ group, items: shown.filter((t) => t.group === group) }))
    .filter((g) => g.items.length > 0);
}

/** Tuiles d'un groupe, dans l'ordre de l'API : les formats d'un même système (cibles du Registry avec un
 * `format`) sont réunis en UNE tuile du système ; le format se choisit après. Aucun système en dur.
 * @param {TargetChoice[]} items
 * @returns {TargetTile[]} */
export function targetTiles(items) {
  /** @type {TargetTile[]} */
  const tiles = [];
  /** @type {Map<string, TargetChoice[]>} */
  const formats = new Map();
  for (const t of items) {
    if (t.group === "registry" && t.format && t.system) {
      const list = formats.get(t.system);
      if (list) {
        list.push(t);
        continue;
      }
      const created = [t];
      formats.set(t.system, created);
      tiles.push({ kind: "system", key: `system:${t.system}`, system: t.system, label: t.system_name ?? t.system, formats: created });
    } else {
      tiles.push({ kind: "target", key: t.value, target: t });
    }
  }
  return tiles;
}

/** Tuile de système ouverte : celle choisie, ou celle dont un format est la cible actuelle ; sinon undefined.
 * @param {TargetTile[]} tiles @param {string} openSystem @param {string} target
 * @returns {{ kind: "system", key: string, system: string, label: string, formats: TargetChoice[] } | undefined} */
export function openTile(tiles, openSystem, target) {
  for (const t of tiles) {
    if (t.kind === "system" && (t.system === openSystem || t.formats.some((f) => f.value === target))) return t;
  }
  return undefined;
}

/** Choix simples d'un système à plusieurs formats : un par format (sa version la plus récente, nommée par ce
 * qu'elle reçoit : « Bookings (Buchungsstapel) ») ; les autres versions restent dans une option avancée.
 * @param {TargetChoice[]} formats
 * @returns {{ main: { value: string, label: string }[], others: { value: string, label: string }[] }} */
export function formatChoices(formats) {
  const kinds = /** @type {Record<string, string>} */ (L.formatKinds);
  const main = [];
  const others = [];
  for (const f of formats) {
    const product = f.product_name ?? f.format ?? f.label;
    const kind = (f.receives ?? []).map((r) => kinds[r]).find(Boolean);
    if (f.latest) main.push({ value: f.value, label: kind ? L.formatChoice(kind, product) : (f.format ?? f.label) });
    else others.push({ value: f.value, label: `${product} · ${L.versionLabel(f.version ?? "")}` });
  }
  return { main, others };
}

/** Titre d'une tuile : le nom de VOTRE système connecté (« accounting17 »), sinon le libellé de la cible.
 * @param {TargetChoice} t */
export function targetTitle(t) {
  return t.group === "instance" && t.detail ? t.detail : t.label;
}

/** Sous-titre d'une tuile : pour votre système, le système et sa version (« Odoo 17 ») ; sinon le détail.
 * @param {TargetChoice} t */
export function targetSubtitle(t) {
  return t.group === "instance" && t.detail ? t.label : (t.detail ?? "");
}

/** Cible présélectionnée : votre premier système connecté ; sinon aucune (l'utilisateur choisit).
 * @param {TargetChoice[]} targets */
export function defaultTarget(targets) {
  return targets.find((t) => t.group === "instance" && t.connected)?.value ?? "";
}

/** Clé d'une question : sa décision sur les valeurs, sinon son champ.
 * @param {QuickQuestion} q */
export function questionKey(q) {
  return q.decision ?? `field:${q.question.field}`;
}

/** Réponses de départ : la valeur présélectionnée de chaque question posée ET de chaque décision appliquée.
 * @param {QuickStart} start
 * @returns {Edits} */
export function initialEdits(start) {
  /** @type {Record<string, string>} */
  const answers = {};
  for (const q of [...start.questions, ...start.applied]) {
    if (q.default) answers[questionKey(q)] = q.default;
  }
  return { answers, confirmed: [], targets: {} };
}

/** Libellé lisible d'une réponse : celui du choix (jamais la valeur technique), sinon la valeur saisie.
 * @param {QuickQuestion} q
 * @param {string | undefined} value */
export function answerText(q, value) {
  if (!value) return "";
  return q.question.choices.find((c) => c.value === value)?.label ?? value;
}

/** Origine de la présélection en mots simples.
 * @param {QuickQuestion} q */
export function defaultOrigin(q) {
  const from = /** @type {keyof typeof L.defaultFrom | undefined} */ (q.default_from ?? undefined);
  return from ? L.defaultFrom[from] ?? "" : "";
}

/** Colonnes dont la correspondance est incertaine (à confirmer ou changer).
 * @param {QuickStart} start */
export function columnsToConfirm(start) {
  return start.mapping.columns.filter((c) => c.status === "to_confirm" && c.target);
}

/** Colonnes placées sans intervention (nom exact, mémoire, IA sûre).
 * @param {QuickStart} start */
export function automaticColumns(start) {
  const auto = new Set(start.decisions.map((d) => d.position));
  return start.mapping.columns.filter((c) => auto.has(c.position) && c.target);
}

/** Colonnes avec des valeurs qui ne vont dans aucun champ (une colonne encore à confirmer est listée à part).
 * @param {QuickStart} start
 * @param {Edits} edits */
export function ignoredColumns(start, edits) {
  const used = new Set(decisionsOf(start, edits).filter((d) => d.target).map((d) => d.position));
  const pending = new Set(columnsToConfirm(start).map((c) => c.position));
  const confirmed = new Set(edits.confirmed);
  return start.mapping.columns.filter(
    (c) =>
      c.status !== "empty" &&
      !used.has(c.position) &&
      !(pending.has(c.position) && !confirmed.has(c.position)),
  );
}

/** Libellé d'un champ de la structure (jamais son nom technique).
 * @param {QuickStart} start
 * @param {string | null | undefined} name */
export function fieldLabel(start, name) {
  if (!name) return L.notImported;
  return start.proposal.fields.find((f) => f.name === name)?.label ?? name;
}

/** Correspondances envoyées : celles trouvées seules + les colonnes confirmées (avec le champ choisi).
 * @param {QuickStart} start
 * @param {Edits} edits
 * @returns {{ position: number, target: string | null }[]} */
export function decisionsOf(start, edits) {
  const confirmed = new Set(edits.confirmed);
  // réponse « colonne du fichier » à une question sur un champ : cette colonne alimente ce champ
  const chosen = columnAnswers(start, edits);
  const taken = new Set(chosen.map((d) => d.position));
  const fields = new Set(chosen.map((d) => d.target));
  const extra = columnsToConfirm(start)
    .filter((c) => confirmed.has(c.position))
    .map((c) => ({
      position: c.position,
      target: (c.position in edits.targets ? edits.targets[c.position] : c.target) ?? null,
    }));
  // « Not imported » confirmé : envoyé sans champ, pour que la mémoire apprenne que la colonne ne sert pas
  const base = [...start.decisions.map((d) => ({ position: d.position, target: d.target ?? null })), ...extra];
  return [...base.filter((d) => !taken.has(d.position) && !fields.has(d.target ?? "")), ...chosen];
}

/** Préfixe d'une réponse qui désigne une COLONNE du fichier (question « Which column of your file contains… »). */
export const COLUMN_ANSWER = "column:";

/** Colonnes choisies en réponse aux questions sur un champ : position → champ de la question.
 * @param {QuickStart} start
 * @param {Edits} edits
 * @returns {{ position: number, target: string }[]} */
export function columnAnswers(start, edits) {
  const out = [];
  for (const q of [...start.questions, ...start.applied]) {
    const value = edits.answers[questionKey(q)];
    if (value?.startsWith(COLUMN_ANSWER)) {
      out.push({ position: Number(value.slice(COLUMN_ANSWER.length)), target: q.question.field });
    }
  }
  return out;
}

/** Décisions sur les valeurs : celles déjà connues + la réponse à chaque question / décision appliquée qui en est une.
 * @param {QuickStart} start
 * @param {Edits} edits
 * @returns {Record<string, string>} */
export function choicesOf(start, edits) {
  const out = { ...start.choices };
  for (const q of [...start.questions, ...start.applied]) {
    const value = edits.answers[questionKey(q)];
    if (q.decision && value) out[q.decision] = value;
  }
  return out;
}

/** Valeurs de champs : une par champ, la réponse de l'utilisateur l'emporte sur celle appliquée d'office.
 * @param {QuickStart} start
 * @param {Edits} edits
 * @returns {Fill[]} */
export function fillsOf(start, edits) {
  /** @type {Map<string, Fill>} */
  const out = new Map(start.fills.map((f) => [f.field, { per_row: {}, confirmed: true, ...f }]));
  for (const q of [...start.questions, ...start.applied]) {
    if (q.decision) continue;
    const value = edits.answers[questionKey(q)];
    if (value?.startsWith(COLUMN_ANSWER)) continue; // une colonne, jamais une valeur commune
    if (value) out.set(q.question.field, { field: q.question.field, common: value, per_row: {}, confirmed: true });
  }
  return [...out.values()];
}

/** Questions sans réponse (ne bloquent rien : le fichier reste préparé, le champ signalé).
 * @param {QuickStart} start
 * @param {Edits} edits */
export function unanswered(start, edits) {
  return start.questions.filter((q) => !edits.answers[questionKey(q)]);
}

/** Corps commun de l'aperçu et du fichier préparé. `confirmed` n'est envoyé qu'au téléchargement (action finale) :
 * la mémoire de l'organisation apprend alors les choix explicites.
 * @param {QuickStart} start
 * @param {Edits} edits
 * @param {boolean} final */
export function requestOf(start, edits, final = false) {
  return {
    decisions: decisionsOf(start, edits),
    fills: fillsOf(start, edits),
    choices: choicesOf(start, edits),
    confirmed: final
      ? [...new Set([...edits.confirmed, ...columnAnswers(start, edits).map((d) => d.position)])].sort((a, b) => a - b)
      : [],
  };
}

/** Nom du type de données en minuscules, singulier et pluriel (« product », « products ») pour les phrases.
 * @param {string} dataType
 * @returns {{ one: string, many: string }} */
export function nounOf(dataType) {
  const nouns = /** @type {Record<string, { one: string, many: string }>} */ (L.noun);
  return nouns[dataType] ?? L.nounFallback;
}

/** Message bien visible en haut de la synthèse quand plus de la moitié des lignes sont sans leurs montants.
 * @param {{ currency_notice?: { target: string, file: string[], rows: number, fields: string[], from_system: boolean, note?: string } | null }} preview
 * @param {string} dataType */
export function currencyNoticeText(preview, dataType) {
  return preview.currency_notice ? L.currencyNotice(preview.currency_notice, nounOf(dataType)) : "";
}

/** Titre du résultat : prêt (tous les contrôles passés), préparé, ou informations manquantes.
 * @param {{ rows: number, status: string }} preview
 * @param {string} dataType */
export function resultTitle(preview, dataType) {
  const noun = nounOf(dataType);
  if (preview.status === "ready") return L.ready(preview.rows, noun);
  if (preview.status === "to_complete") return L.toComplete(preview.rows, noun);
  return L.prepared(preview.rows, noun);
}

/** @typedef {"currency" | "duplicate" | "probable_duplicate" | "other"} ExcludedKind */
/** Lignes écartées regroupées par motif (devise, lignes identiques, doublon probable, autre), chaque groupe avec
 * ses lignes.
 * @param {{ row: number, reason: string, kind?: string }[]} rows
 * @returns {{ kind: ExcludedKind, title: string, rows: number[], reasons: { row: number, reason: string }[] }[]} */
export function excludedGroups(rows) {
  /** @type {ExcludedKind[]} */
  const order = ["currency", "duplicate", "probable_duplicate", "other"];
  /** @param {string | undefined} kind @returns {ExcludedKind} */
  const groupOf = (kind) => order.find((k) => k === kind) ?? "other";
  return order
    .map((kind) => {
      const found = rows.filter((r) => groupOf(r.kind) === kind);
      // doublon probable, autres raisons : chaque ligne avec SON motif (jamais des numéros sans explication)
      const explained = kind === "other" || kind === "probable_duplicate";
      return {
        kind,
        title: L.excludedKinds[kind],
        rows: found.map((r) => r.row),
        reasons: explained ? found.map((r) => ({ row: r.row, reason: r.reason })) : [],
      };
    })
    .filter((g) => g.rows.length > 0);
}

/** Ordre d'import en étapes simples (numérotées) : feuilles « (import first) » d'abord, puis les données ;
 * `notes` : ce que le fichier contient (comptes, pièces, période), une information, jamais une étape.
 * @param {{ created_records?: { sheet: string, count: number, label: string }[], delivery?: string, notices?: string[] }} preview
 * @param {string} dataType
 * @param {string} [target]
 * @returns {{ steps: string[], notes: string[] }} */
export function importSteps(preview, dataType, target = "") {
  const noun = nounOf(dataType);
  // fichier du FORMAT de la cible (ex. DATEV EXTF) : un seul fichier, importé tel quel par la cible
  if (preview.delivery === "format") return { steps: [L.importFormat(target, noun)], notes: preview.notices ?? [] };
  const first = preview.created_records ?? [];
  if (first.length === 0) return { steps: [L.importOnly(noun)], notes: [] };
  return { steps: [...first.map((c) => L.importFirst(c.sheet, c.count, c.label)), L.importData(noun)], notes: [] };
}

/** Champs déjà pris par une autre colonne (une colonne par champ) : non proposés pour `position`.
 * @param {QuickStart} start
 * @param {Edits} edits
 * @param {number} position */
export function takenFields(start, edits, position) {
  return new Set(
    decisionsOf(start, edits)
      .filter((d) => d.position !== position && d.target)
      .map((d) => /** @type {string} */ (d.target)),
  );
}
