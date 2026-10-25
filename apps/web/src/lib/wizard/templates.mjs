// @ts-check
/**
 * AI Migration Wizard — modèles de questionnaire (barre latérale) : logique PURE (node --test, templates.test.mjs).
 * Un modèle = les étapes du questionnaire initial (question posée + réponse), RIEN d'autre : ni fichier, ni
 * correspondance, ni valeur complétée. « Use » restaure ces réponses sans reposer aucune question.
 */

/** @typedef {import("./flow.mjs").Step} Step */

export const SIDEBAR_KEY = "nexa45.migration-wizard.sidebar";
export const NAME_MAX = 120;

/** Étapes sauvegardées : question et réponse seulement (toute autre clé est retirée).
 * @param {Step[]} history
 * @returns {Step[]} */
export function templateSteps(history) {
  return history.map(({ question, answer }) => ({ question, answer }));
}

/** Nom proposé : les deux premières réponses (« Odoo · 18 »), rendu unique parmi les modèles existants.
 * @param {Step[]} history
 * @param {string[]} existing
 * @returns {string} */
export function defaultTemplateName(history, existing) {
  const base =
    history
      .slice(0, 2)
      .map((s) => s.answer.label.trim())
      .filter(Boolean)
      .join(" · ")
      .slice(0, NAME_MAX - 5) || "My migration";
  const taken = new Set(existing.map((n) => n.toLowerCase()));
  if (!taken.has(base.toLowerCase())) return base;
  for (let i = 2; ; i++) {
    const name = `${base} (${i})`;
    if (!taken.has(name.toLowerCase())) return name;
  }
}

/** Nom saisi : espaces réduits, borné ; vide → null (rien à enregistrer).
 * @param {string} text
 * @returns {string | null} */
export function cleanName(text) {
  const name = text.split(/\s+/).filter(Boolean).join(" ").slice(0, NAME_MAX);
  return name || null;
}

/** État du Wizard après « Use » : les réponses du modèle, aucune question en cours ; la structure cible est
 * ensuite demandée une fois (jamais le questionnaire), et l'étape « Your file » s'ouvre.
 * @template {string} M
 * @param {Step[]} steps
 * @param {M} mode
 * @returns {{ history: Step[], current: null, remaining: number, proposal: null, mode: M }} */
export function restoredState(steps, mode) {
  return { history: templateSteps(steps), current: null, remaining: 0, proposal: null, mode };
}

/** Résumé d'un modèle dans la liste : « 4 answers · 21 Oct 2026 ».
 * @param {{ answered: number, updated_at: string }} t */
export function templateMeta(t) {
  const day = new Date(t.updated_at);
  const date = Number.isNaN(day.getTime())
    ? ""
    : ` · ${day.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}`;
  return `${t.answered} answer${t.answered > 1 ? "s" : ""}${date}`;
}
