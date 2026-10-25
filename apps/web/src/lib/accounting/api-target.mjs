// @ts-check
/**
 * Pièces comptables → système cible relié par son API : présentation PURE de l'aperçu renvoyé par l'API.
 * Aucun nom de système, aucun calcul comptable : statuts, montants, contrôles et sortes attendues viennent
 * du backend. Les statuts de préparation et l'historique d'envoi réutilisent `lib/partners/target.mjs`.
 */

/** Sorte d'option attendue pour un compte (d'après le rôle du partenaire de ses pièces).
 * @param {string} expected  "customer" | "supplier"
 * @returns {string} */
export function expectedKindLabel(expected) {
  return expected === "customer" ? "Income (sales documents)" : "Expense (purchase documents)";
}

/** Sorte d'option pour PRÉ-FILTRER la liste (« customer » → income, « supplier » → outgo). Le backend
 *  contrôle de toute façon chaque choix contre la cible.
 * @param {string} expected
 * @returns {string} */
export function optionKind(expected) {
  return expected === "customer" ? "income" : "outgo";
}

/** Options proposées pour un compte : celles de la sorte attendue, groupées, triées.
 * @template {{ id: string, label: string, group?: string | null, kind?: string | null }} T
 * @param {T[]} options
 * @param {string | null | undefined} kind  sorte attendue, null : toutes
 * @returns {{ group: string, options: T[] }[]} */
export function groupOptions(options, kind) {
  const kept = kind ? options.filter((o) => !o.kind || o.kind === kind) : options;
  /** @type {Map<string, T[]>} */
  const groups = new Map();
  for (const o of kept) {
    const g = o.group || "Other";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)?.push(o);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([group, list]) => ({ group, options: [...list].sort((x, y) => x.label.localeCompare(y.label)) }));
}

/** Libellé d'un type de pièce du CDM.
 * @param {string | null | undefined} type
 * @returns {string} */
export function documentTypeLabel(type) {
  switch (type) {
    case "CUSTOMER_INVOICE":
      return "Customer invoice";
    case "CUSTOMER_CREDIT_NOTE":
      return "Customer credit note";
    case "VENDOR_BILL":
      return "Vendor bill";
    case "VENDOR_CREDIT_NOTE":
      return "Vendor credit note";
    default:
      return "Document";
  }
}

/** Taux de taxe affiché (« 19 % », « 0 % », « — »).
 * @param {string | number | null | undefined} rate
 * @returns {string} */
export function rateLabel(rate) {
  if (rate === null || rate === undefined) return "—";
  const n = Number(rate);
  return `${Number.isInteger(n) ? n : n.toFixed(2)} %`;
}

/** Résumé de la confirmation : les pièces choisies (numéro, partenaire, montant) et leur total par devise.
 *  Aucun calcul comptable : somme d'affichage des totaux TTC renvoyés par le backend.
 * @param {{ source_reference: string, number?: string | null, partner?: string | null,
 *           total_amount?: string | null, currency?: string | null }[]} documents
 * @param {string[]} selected
 * @returns {{ rows: { reference: string, number: string, partner: string, amount: string | null,
 *             currency: string | null }[], totals: { currency: string, amount: string }[] }} */
export function selectionSummary(documents, selected) {
  const chosen = new Set(selected);
  const rows = documents
    .filter((d) => chosen.has(d.source_reference))
    .map((d) => ({
      reference: d.source_reference,
      number: d.number ?? "No number",
      partner: d.partner ?? "—",
      amount: d.total_amount ?? null,
      currency: d.currency ?? null,
    }));
  /** @type {Map<string, number>} */
  const cents = new Map();
  for (const r of rows) {
    if (r.amount === null) continue;
    const key = r.currency ?? "";
    cents.set(key, (cents.get(key) ?? 0) + Math.round(Number(r.amount) * 100));
  }
  const totals = [...cents.entries()].map(([currency, c]) => ({ currency, amount: (c / 100).toFixed(2) }));
  return { rows, totals };
}

/** Comptes encore sans valeur choisie, ou dont le choix pose problème.
 * @param {{ choice_id?: string | null, problem?: string | null }[]} accounts
 * @returns {{ missing: number, problems: number }} */
export function accountsToReview(accounts) {
  return {
    missing: accounts.filter((a) => !a.choice_id).length,
    problems: accounts.filter((a) => a.choice_id && a.problem).length,
  };
}
