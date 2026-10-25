// @ts-check
/** Présentation de la préparation d'un format cible (ACCOUNTING, phase 4) : VUES des données renvoyées par
 *  l'API (statuts, provenance, valeurs des cellules telles qu'elles seraient écrites), jamais un calcul
 *  comptable ni une règle du format. Fonctions pures testées par `node --test` (`preparation.test.mjs`). */

/** @typedef {"success" | "warning" | "danger" | "info" | "neutral"} Tone */

/** Statut cible d'une pièce ou d'une écriture (null : non préparée). Distinct de l'éligibilité source.
 * @param {string | null | undefined} status
 * @returns {{ label: string, tone: Tone }} */
export function targetStatus(status) {
  switch (status) {
    case "ready":
      return { label: "Ready", tone: "success" };
    case "warning":
      return { label: "Warning", tone: "warning" };
    case "blocked":
      return { label: "Blocked", tone: "danger" };
    default:
      return { label: "Not prepared", tone: "neutral" };
  }
}

/** Statut d'une correspondance : une suggestion n'est PAS une décision.
 * @param {string} status
 * @returns {{ label: string, tone: Tone }} */
export function mappingStatus(status) {
  switch (status) {
    case "confirmed":
      return { label: "Confirmed", tone: "success" };
    case "suggested":
      return { label: "Suggested — not confirmed", tone: "info" };
    case "invalid":
      return { label: "Invalid", tone: "danger" };
    default:
      return { label: "Missing", tone: "warning" };
  }
}

/** Valeur cible affichée d'une correspondance.
 * @param {{ status: string, target_value?: string | null, no_value?: boolean, kind: string }} m
 * @returns {string} */
export function mappingTarget(m) {
  if (m.status !== "confirmed" && m.status !== "invalid") return "—";
  if (m.no_value) return m.kind === "tax" ? "No tax key" : "No value";
  return m.target_value ?? "—";
}

/** Valeur d'une cellule telle qu'elle serait écrite, sans les guillemets du format (affichage).
 * @param {string | null | undefined} value
 * @returns {string} */
export function cellText(value) {
  if (value === null || value === undefined || value === "") return "—";
  return value.length >= 2 && value.startsWith('"') && value.endsWith('"') ? value.slice(1, -1).replaceAll('""', '"') : value;
}

/** Cellule d'une écriture par sa position du format (ex. « 002 »), lue dans les cellules renvoyées.
 * @param {{ cells: { field: string, value: string }[] }} booking
 * @param {string} field
 * @returns {string} */
export function cell(booking, field) {
  return cellText(booking.cells.find((c) => c.field === field)?.value);
}

/** Libellé du sens du compte du tiers (vue du sens renvoyé par l'API).
 * @param {"debit" | "credit"} side
 * @param {"customer" | "supplier" | null | undefined} role
 * @returns {string} */
export function partnerSide(side, role) {
  const who = role === "supplier" ? "supplier" : role === "customer" ? "customer" : "partner";
  return `${side === "debit" ? "Debit" : "Credit"} on the ${who} account`;
}

/** Clé de taxe affichée d'une écriture.
 * @param {{ tax?: string | null, tax_key?: string | null, tax_no_key?: boolean }} booking
 * @returns {string} */
export function taxDecision(booking) {
  if (!booking.tax) return "No tax on this line";
  if (booking.tax_no_key) return `${booking.tax} → No tax key`;
  return booking.tax_key ? `${booking.tax} → ${booking.tax_key}` : `${booking.tax} → not decided`;
}

/** Résumé des compteurs : éligibles (source) et prêtes (cible) sont deux notions distinctes.
 * @param {{ eligible: number, ready: number, warnings: number, blocked: number, not_prepared: number }} c
 * @returns {string} */
export function countsSentence(c) {
  return `${c.eligible} eligible in your system · ${c.ready} ready · ${c.warnings} with warnings · ${c.blocked} blocked · ${c.not_prepared} not prepared`;
}

// --------------------------------------------------------------------------- //
// Fichier (phase 5) : mêmes données que le fichier téléchargé, lues telles quelles
// --------------------------------------------------------------------------- //

/** Règle d'export affichée : seules les pièces prêtes ou avec avertissement sont écrites. */
export const EXPORT_POLICY =
  "Ready documents and documents with warnings are written. Blocked, draft and cancelled documents are never written.";

/** Ligne du fichier résumée par ses champs principaux (positions du format lues dans les cellules).
 * @param {{ cells: { field: string, value: string }[] }} row
 * @returns {{ amount: string, side: string, account: string, counter: string, taxKey: string, date: string, reference: string, text: string }} */
export function rowSummary(row) {
  return {
    amount: cell(row, "001"),
    side: cell(row, "002"),
    account: cell(row, "007"),
    counter: cell(row, "008"),
    taxKey: cell(row, "009"),
    date: cell(row, "010"),
    reference: cell(row, "011"),
    text: cell(row, "014"),
  };
}

/** Phrase de ce que le téléchargement contiendra : compteurs renvoyés par l'API, jamais recalculés.
 * @param {{ exported_documents: number, excluded_documents: number, rows: number, warnings: number }} c
 * @returns {string} */
export function fileSentence(c) {
  const docs = `${c.exported_documents} document${c.exported_documents === 1 ? "" : "s"}`;
  const rows = `${c.rows} booking row${c.rows === 1 ? "" : "s"}`;
  const warn = c.warnings ? ` (${c.warnings} with warnings)` : "";
  return `${docs}${warn} · ${rows} · ${c.excluded_documents} not exported`;
}

/** Taille lisible d'un fichier.
 * @param {number} bytes
 * @returns {string} */
export function fileSize(bytes) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

// --------------------------------------------------------------------------- //
// Historique d'export par pièce (D11) : axe DISTINCT du statut cible
// --------------------------------------------------------------------------- //

/** État d'historique d'export d'une pièce (jamais confondu avec Ready / Warning / Blocked).
 * @param {string} state
 * @returns {{ label: string, tone: Tone }} */
export function exportState(state) {
  switch (state) {
    case "already_exported":
      return { label: "Already exported", tone: "neutral" };
    case "changed_since_export":
      return { label: "Changed since export", tone: "danger" };
    default:
      return { label: "New", tone: "success" };
  }
}

/** Phrase des compteurs d'historique : ce qui est nouveau, déjà parti, modifié, et ce que le fichier contiendra.
 * @param {{ new: number, already_exported: number, changed_since_export: number, will_be_exported: number }} h
 * @returns {string} */
export function historySentence(h) {
  return `${h.new} new · ${h.already_exported} already exported · ${h.changed_since_export} changed since export · ${h.will_be_exported} will be exported`;
}

/** Message quand le fichier ne contiendrait aucune pièce.
 * @param {{ new: number, already_exported: number, changed_since_export: number, will_be_exported: number }} h
 * @returns {string | null} */
export function nothingToExport(h) {
  if (h.will_be_exported > 0) return null;
  if (h.changed_since_export > 0 && h.already_exported === 0)
    return "No document to export: the documents of this period changed since their export and need your decision.";
  if (h.already_exported > 0 || h.changed_since_export > 0)
    return "No new documents to export: every exportable document of this period was already exported.";
  return "No document can be exported for this period.";
}

/** Changements d'une pièce modifiée, par écriture (libellés des champs, jamais d'empreinte).
 * @param {{ booking: number, label: string, change: string }[]} changes
 * @returns {string[]} */
export function changeLines(changes) {
  /** @type {Map<number, string[]>} */
  const byBooking = new Map();
  for (const c of changes) {
    const verb = c.change === "added" ? "added" : c.change === "removed" ? "removed" : "changed";
    byBooking.set(c.booking, [...(byBooking.get(c.booking) ?? []), `${c.label} ${verb}`]);
  }
  return [...byBooking.entries()].map(([booking, items]) => `Booking ${booking}: ${items.join(", ")}`);
}
