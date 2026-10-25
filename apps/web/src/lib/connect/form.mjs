// @ts-check
/**
 * Formulaire « Connect a system » : logique PURE, pilotée par les champs que le CONNECTEUR déclare
 * (`GET /api/connectors` → `fields`). Aucun nom de système ici : la page ne sait pas quels champs un système
 * demande, elle affiche ceux qu'elle reçoit. Un champ `secret` est masqué, n'est jamais rappelé dans un
 * récapitulatif et n'entre dans aucune valeur affichée.
 *
 * JavaScript pur (JSDoc + @ts-check), testé par `node --test` (`form.test.mjs`).
 */

/** @typedef {{ name: string, label: string, kind: "text" | "url" | "secret", required: boolean, help?: string | null, placeholder?: string | null }} ConnectorField */
/** @typedef {Record<string, string>} Values */

/** Valeurs vides pour les champs d'un connecteur.
 * @param {ConnectorField[]} fields
 * @returns {Values} */
export function emptyValues(fields) {
  return Object.fromEntries(fields.map((f) => [f.name, ""]));
}

/** Valeurs à envoyer : un secret tel quel, le reste sans espaces autour ; seuls les champs DÉCLARÉS partent.
 * @param {ConnectorField[]} fields
 * @param {Values} values
 * @returns {Values} */
export function submittedValues(fields, values) {
  return Object.fromEntries(
    fields.map((f) => [f.name, f.kind === "secret" ? (values[f.name] ?? "") : (values[f.name] ?? "").trim()]),
  );
}

/** Le formulaire peut être testé : nom du système et tous les champs obligatoires renseignés.
 * @param {string} name
 * @param {ConnectorField[]} fields
 * @param {Values} values
 * @returns {boolean} */
export function isComplete(name, fields, values) {
  const sent = submittedValues(fields, values);
  return name.trim().length > 0 && fields.every((f) => !f.required || sent[f.name].length > 0);
}

/** Empreinte des valeurs testées : un test ne vaut que pour EXACTEMENT ces valeurs (jamais affichée).
 * @param {string} system
 * @param {ConnectorField[]} fields
 * @param {Values} values
 * @returns {string} */
export function testedSignature(system, fields, values) {
  return JSON.stringify([system, fields.map((f) => [f.name, submittedValues(fields, values)[f.name]])]);
}

/** Lignes du récapitulatif (libellé, valeur) : jamais un champ secret, jamais une valeur vide.
 * @param {ConnectorField[]} fields
 * @param {Values} values
 * @returns {{ label: string, value: string, technical: boolean }[]} */
export function summaryRows(fields, values) {
  const sent = submittedValues(fields, values);
  return fields
    .filter((f) => f.kind !== "secret" && sent[f.name])
    .map((f) => ({ label: f.label, value: sent[f.name], technical: f.kind === "url" }));
}

/** Type de l'élément de saisie d'un champ (`shown` : l'utilisateur a demandé à voir le secret).
 * @param {ConnectorField} field
 * @param {boolean} shown
 * @returns {"text" | "password" | "url"} */
export function inputType(field, shown) {
  if (field.kind === "secret") return shown ? "text" : "password";
  return field.kind === "url" ? "url" : "text";
}

/** Valeurs d'aide de développement appliquées aux seuls champs déclarés (les autres sont ignorées).
 * @param {ConnectorField[]} fields
 * @param {Record<string, unknown>} dev
 * @returns {Values} */
export function fillFrom(fields, dev) {
  return Object.fromEntries(fields.map((f) => [f.name, typeof dev[f.name] === "string" ? String(dev[f.name]) : ""]));
}
