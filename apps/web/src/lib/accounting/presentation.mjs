// @ts-check
/** Présentation des pièces comptables lues (ACCOUNTING, phase 3.1) : VUES des données renvoyées par l'API, jamais
 *  un second calcul comptable. Convention du CDM conservée : montant signé, débit > 0, crédit < 0 ; la colonne
 *  Débit / Crédit n'est qu'une façon de l'afficher. Le solde vient du backend (somme des lignes du CDM).
 *  Fonctions pures (JavaScript + JSDoc) testées par `node --test` (`presentation.test.mjs`). */

/** Libellé du compteur de pièces éligibles : comptabilisées et sans construction non prise en charge, donc
 *  prêtes pour l'étape suivante (mapping). Ce n'est PAS une validation DATEV (phases suivantes). */
export const ELIGIBLE_LABEL = "Eligible";
export const ELIGIBLE_HINT = "Posted and fully supported: ready for mapping (no DATEV validation yet).";

/**
 * Montant décimal (chaîne ou nombre de l'API) → texte à deux décimales, séparateur de milliers, SANS signe.
 * Lecture de la chaîne décimale (pas de calcul en virgule flottante) ; plus de deux décimales : arrondi
 * d'affichage seulement.
 * @param {string | number} value
 * @returns {string}
 */
export function formatAbsolute(value) {
  const text = String(value).trim().replace(/^[-+]/, "");
  let [int, frac = ""] = text.split(".");
  if (frac.length > 2) {
    return Math.abs(Number(value)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  int = (int || "0").replace(/^0+(?=\d)/, "");
  return `${int.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${frac.padEnd(2, "0")}`;
}

/**
 * Signe d'un montant décimal de l'API : 1, -1 ou 0 (zéro, « -0.00 » compris).
 * @param {string | number} value
 * @returns {1 | -1 | 0}
 */
export function signOf(value) {
  const text = String(value).trim();
  if (!/[1-9]/.test(text)) return 0;
  return text.startsWith("-") ? -1 : 1;
}

/**
 * Montant signé (convention du CDM) → colonnes Débit / Crédit : positif = débit, négatif = crédit en valeur
 * absolue, zéro = aucune des deux.
 * @param {string | number} amount
 * @returns {{ debit: string | null, credit: string | null }}
 */
export function debitCredit(amount) {
  const sign = signOf(amount);
  if (sign > 0) return { debit: formatAbsolute(amount), credit: null };
  if (sign < 0) return { debit: null, credit: formatAbsolute(amount) };
  return { debit: null, credit: null };
}

/**
 * Montant signé pour un total (« 739.89 EUR », « -12.00 EUR ») ; absent → « — ».
 * @param {string | number | null | undefined} value
 * @param {string | null | undefined} [currency]
 * @returns {string}
 */
export function formatAmount(value, currency) {
  if (value === null || value === undefined) return "—";
  const text = `${signOf(value) < 0 ? "-" : ""}${formatAbsolute(value)}`;
  return currency ? `${text} ${currency}` : text;
}

/**
 * Solde d'une pièce tel que renvoyé par le backend : équilibrée seulement s'il vaut exactement zéro.
 * @param {string | number | null | undefined} balance
 * @param {string | null | undefined} currency
 * @returns {{ text: string, balanced: boolean }}
 */
export function balanceView(balance, currency) {
  if (balance === null || balance === undefined) return { text: "—", balanced: false };
  return { text: formatAmount(balance, currency), balanced: signOf(balance) === 0 };
}

/**
 * Titre d'une ligne : sa description quand le CDM en a une (article, taxe, référence), la sorte de ligne en
 * sous-titre ; sinon la sorte seule. Rien n'est inventé.
 * @param {{ kind: string, description?: string | null }} line
 * @returns {{ title: string, subtitle: string | null }}
 */
export function lineTitle(line) {
  const description = line.description?.trim();
  return description ? { title: description, subtitle: line.kind } : { title: line.kind, subtitle: null };
}
