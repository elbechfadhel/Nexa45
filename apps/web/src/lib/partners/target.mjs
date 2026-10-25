// @ts-check
/**
 * Aperçu « clients / fournisseurs → système cible » : présentation PURE de ce que l'API renvoie.
 * Aucun nom de système (ni source, ni cible) : les noms viennent de la réponse. Aucun calcul métier : le
 * statut, l'état d'envoi et les problèmes sont décidés par le backend.
 *
 * Deux axes distincts, jamais confondus :
 *   - préparation : Ready / Warning / Blocked (la requête peut-elle être envoyée telle quelle ?) ;
 *   - historique  : New / Already sent / Changed since sent (a-t-elle déjà été envoyée ?).
 */

/** @typedef {"success" | "warning" | "danger" | "neutral" | "info"} Tone */

/** @param {string} status
 * @returns {{ label: string, tone: Tone }} */
export function preparationStatus(status) {
  switch (status) {
    case "ready":
      return { label: "Ready", tone: "success" };
    case "warning":
      return { label: "Warning", tone: "warning" };
    default:
      return { label: "Blocked", tone: "danger" };
  }
}

/** @param {string} state
 * @returns {{ label: string, tone: Tone }} */
export function sendState(state) {
  switch (state) {
    case "already_sent":
      return { label: "Already sent", tone: "neutral" };
    case "changed_since_sent":
      return { label: "Changed since sent", tone: "warning" };
    default:
      return { label: "New", tone: "info" };
  }
}

/** @param {string} kind
 * @returns {string} */
export function kindLabel(kind) {
  return kind === "company" ? "Company" : kind === "person" ? "Person" : "Unknown";
}

/** @param {string[]} roles
 * @returns {string} */
export function rolesLabel(roles) {
  const names = roles.map((r) => (r === "customer" ? "Customer" : "Supplier"));
  return names.length ? names.join(" & ") : "No role";
}

/** Phrase des compteurs de préparation.
 * @param {{ partners: number, ready: number, warning: number, blocked: number }} c
 * @returns {string} */
export function countsSentence(c) {
  return `${c.partners} read · ${c.ready} ready · ${c.warning} with warnings · ${c.blocked} blocked`;
}

/** Ce qui pourrait être envoyé (prêts + avertissements) : information, jamais une promesse d'envoi.
 * @param {{ ready: number, warning: number }} c
 * @returns {number} */
export function sendable(c) {
  return c.ready + c.warning;
}

/** Filtre d'affichage par statut de préparation.
 * @template {{ status: string }} T
 * @param {T[]} partners
 * @param {string} filter  "all" | "ready" | "warning" | "blocked"
 * @returns {T[]} */
export function filterByStatus(partners, filter) {
  return filter === "all" ? partners : partners.filter((p) => p.status === filter);
}

// --------------------------------------------------------------------------- //
// Envoi (phase 1b) : présentation pure des décisions du backend
// --------------------------------------------------------------------------- //

/** Ce qu'un envoi ferait pour ce partenaire (décidé par le backend : `action`).
 * @param {string} action  "create" | "update" | "none"
 * @returns {{ label: string, tone: Tone } | null} */
export function actionLabel(action) {
  if (action === "create") return { label: "Will create", tone: "info" };
  if (action === "update") return { label: "Will update", tone: "warning" };
  return null;
}

/** Références sélectionnables : seulement ce que le backend déclare envoyable (jamais un bloqué).
 * @template {{ source_reference: string, sendable: boolean }} T
 * @param {T[]} partners
 * @returns {string[]} */
export function selectableRefs(partners) {
  return partners.filter((p) => p.sendable).map((p) => p.source_reference);
}

/** Sélection nettoyée : ce qui n'est plus envoyable en sort (après un envoi, un rechargement…).
 * @param {Iterable<string>} selected
 * @param {string[]} allowed
 * @returns {string[]} */
export function keepSelectable(selected, allowed) {
  const ok = new Set(allowed);
  return [...selected].filter((r) => ok.has(r));
}

/** Résumé de la confirmation : combien de créations et de mises à jour.
 * @template {{ source_reference: string, action: string }} T
 * @param {T[]} partners
 * @param {string[]} selected
 * @returns {{ total: number, create: number, update: number }} */
export function confirmationCounts(partners, selected) {
  const chosen = new Set(selected);
  const rows = partners.filter((p) => chosen.has(p.source_reference));
  return {
    total: rows.length,
    create: rows.filter((p) => p.action === "create").length,
    update: rows.filter((p) => p.action === "update").length,
  };
}

/** @param {string} status
 * @returns {{ label: string, tone: Tone }} */
export function sendItemStatus(status) {
  switch (status) {
    case "sent":
      return { label: "Sent", tone: "success" };
    case "failed":
      return { label: "Not sent", tone: "danger" };
    case "unknown":
      return { label: "Unknown outcome", tone: "warning" };
    case "needs_decision":
      return { label: "Your decision", tone: "warning" };
    case "pending":
      return { label: "Pending", tone: "info" };
    default:
      return { label: "Skipped", tone: "neutral" };
  }
}

/** @param {string} status
 * @returns {{ label: string, tone: Tone }} */
export function sendRunStatus(status) {
  switch (status) {
    case "completed":
      return { label: "Completed", tone: "success" };
    case "partial":
      return { label: "Partly sent", tone: "warning" };
    case "running":
      return { label: "Interrupted or running", tone: "info" };
    default:
      return { label: "Nothing sent", tone: "danger" };
  }
}
