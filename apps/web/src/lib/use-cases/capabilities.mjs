// @ts-check
/**
 * Vue « Capabilities » du portail fournisseur : PRÉSENTATION PURE, aucune donnée en dur.
 *
 * Tableau Source | Target | Use case | Status : une ligne = un use case pour UNE direction source → cible. Les
 * use cases, leurs systèmes et leurs statuts viennent du catalogue (`catalog.mjs`, source de vérité unique des
 * Overview). Les deux directions de chaque paire de systèmes sont montrées : une direction sans aucun use case a
 * une seule ligne « Not implemented », sans use case inventé. Second tableau System | Capability | Status : use
 * cases propres à un système, sans couple source → cible.
 *
 * Exécutable tel quel par `node --test` (capabilities.test.mjs).
 */

/** @typedef {import("./catalog.mjs").UseCaseDefinition} UseCaseDefinition */
/** @typedef {"available" | "experimental" | "not_implemented"} RowState */
/** @typedef {{ source: string, target: string }} Pair */
/** @typedef {{ source: string, target: string, useCase: { id: string, title: string } | null,
 *   state: RowState }} CapabilityRow */
/** @typedef {"source" | "target"} Role */
/** @typedef {{ system: string, role: Role, useCase: { id: string, title: string }, state: RowState,
 *   formats: string[] }} SystemCapabilityRow */

/** Systèmes qui ne sont pas des systèmes de la vue : fichier du fournisseur, plateforme elle-même. */
const NOT_A_SYSTEM = new Set(["file", "go45"]);
/** Statut du catalogue → état de la ligne (un use case prévu n'est pas implémenté). */
const FROM_STATUS = /** @type {const} */ ({
  available: "available",
  experimental: "experimental",
  coming_soon: "not_implemented",
  deprecated: "not_implemented",
});
/** États d'une ligne, dans l'ordre de la légende. @type {readonly RowState[]} */
export const ROW_STATES = ["available", "experimental", "not_implemented"];

/** Couples source → cible déclarés par un use case (systèmes externes seulement, jamais un système vers lui-même).
 * @param {UseCaseDefinition} u
 * @returns {Pair[]} */
export function pairsOf(u) {
  const out = [];
  for (const source of u.systems.source)
    for (const target of u.systems.target)
      if (!NOT_A_SYSTEM.has(source) && !NOT_A_SYSTEM.has(target) && source !== target) out.push({ source, target });
  return out;
}

/** Use cases de flux entre systèmes (au moins un couple), dépréciés exclus.
 * @param {UseCaseDefinition[]} useCases
 * @returns {UseCaseDefinition[]} */
export function flowUseCases(useCases) {
  return useCases.filter((u) => u.status !== "deprecated" && pairsOf(u).length > 0);
}

/** Systèmes des flux, dans l'ordre d'affichage donné (les autres suivent, triés).
 * @param {UseCaseDefinition[]} useCases
 * @param {readonly string[]} order
 * @returns {string[]} */
export function flowSystems(useCases, order = []) {
  const systems = new Set(flowUseCases(useCases).flatMap((u) => pairsOf(u).flatMap((p) => [p.source, p.target])));
  const rank = (/** @type {string} */ s) => (order.indexOf(s) < 0 ? order.length : order.indexOf(s));
  return [...systems].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/** Toutes les directions : chaque paire de systèmes dans les deux sens.
 * @param {string[]} systems
 * @returns {Pair[]} */
export function directions(systems) {
  const out = [];
  for (let i = 0; i < systems.length; i++)
    for (let j = i + 1; j < systems.length; j++) {
      out.push({ source: systems[i], target: systems[j] });
      out.push({ source: systems[j], target: systems[i] });
    }
  return out;
}

/** Lignes du tableau : pour chaque direction, ses use cases (ordre du catalogue) ; aucune → une ligne
 * « Not implemented » sans use case.
 * @param {UseCaseDefinition[]} useCases   use cases de l'audience (déjà triés par section puis ordre)
 * @param {readonly string[]} order        ordre d'affichage des systèmes
 * @returns {CapabilityRow[]} */
export function capabilityRows(useCases, order = []) {
  const flows = flowUseCases(useCases);
  /** @type {CapabilityRow[]} */
  const rows = [];
  for (const d of directions(flowSystems(useCases, order))) {
    const matching = flows.filter((u) => pairsOf(u).some((p) => p.source === d.source && p.target === d.target));
    if (matching.length === 0) rows.push({ ...d, useCase: null, state: "not_implemented" });
    for (const u of matching) rows.push({ ...d, useCase: { id: u.id, title: u.title }, state: FROM_STATUS[u.status] });
  }
  return rows;
}

/** Rôles d'un système dans « System capabilities », dans l'ordre d'affichage. @type {readonly Role[]} */
export const ROLES = ["source", "target"];

/** Format d'un système cible connu du Registry (lu par l'API du portail) : système, domaine, nom, version.
 * @typedef {{ system: string, domain: string, name: string, version: string | null }} RegistryFormat */

/** Tableau « System capabilities » : une ligne par système, RÔLE (source / cible) et use case ; dépréciés exclus.
 * - Use cases PROPRES à un système (aucun couple source → cible : connexion, analyse, consultation, import d'un
 *   fichier, lecture) : rôle = côté où le catalogue place le système (source ou cible).
 * - Systèmes cibles servis par des FORMATS du Registry (fichiers d'export) : ils ne se connectent pas et n'ont donc
 *   aucun use case propre ; leurs capacités = use cases du catalogue qui les visent dans le même domaine, UNE ligne
 *   par use case (jamais une par source : c'est le rôle de « System integrations »), avec les formats du Registry.
 * Tri : ordre d'affichage des systèmes, source avant cible, puis ordre du catalogue.
 * @param {UseCaseDefinition[]} useCases        use cases de l'audience (déjà triés par section puis ordre)
 * @param {readonly string[]} order             ordre d'affichage des systèmes
 * @param {readonly RegistryFormat[]} formats   formats cibles du Registry
 * @returns {SystemCapabilityRow[]} */
export function systemCapabilityRows(useCases, order = [], formats = []) {
  /** @type {SystemCapabilityRow[]} */
  const rows = [];
  const fileTargets = new Set(formats.map((f) => f.system));
  for (const u of useCases.filter((x) => x.status !== "deprecated")) {
    const useCase = { id: u.id, title: u.title };
    const state = FROM_STATUS[u.status];
    if (pairsOf(u).length === 0) {
      for (const role of ROLES)
        for (const system of u.systems[role].filter((x) => !NOT_A_SYSTEM.has(x)))
          rows.push({ system, role, useCase, state, formats: [] });
      continue;
    }
    for (const system of new Set(pairsOf(u).map((x) => x.target)))
      if (fileTargets.has(system)) {
        const own = formats.filter((f) => f.system === system && f.domain === u.domain);
        rows.push({ system, role: "target", useCase, state, formats: own.map(formatLabel) });
      }
  }
  const rank = (/** @type {string} */ s) => (order.indexOf(s) < 0 ? order.length : order.indexOf(s));
  // tri stable : l'ordre du catalogue est conservé à l'intérieur d'un système et d'un rôle
  return rows.sort(
    (a, b) =>
      rank(a.system) - rank(b.system) ||
      a.system.localeCompare(b.system) ||
      ROLES.indexOf(a.role) - ROLES.indexOf(b.role),
  );
}

/** « Debitoren/Kreditoren 5 » : nom et version du format, tels que le Registry les donne.
 * @param {RegistryFormat} f */
function formatLabel(f) {
  return f.version ? `${f.name} ${f.version}` : f.name;
}

/** Systèmes du tableau « System capabilities », dans l'ordre des lignes.
 * @param {SystemCapabilityRow[]} rows
 * @returns {string[]} */
export function capabilitySystems(rows) {
  return [...new Set(rows.map((r) => r.system))];
}

/** Filtres System et Role (`""` = tous).
 * @param {SystemCapabilityRow[]} rows
 * @param {{ system?: string, role?: string }} filter
 * @returns {SystemCapabilityRow[]} */
export function filterSystemRows(rows, { system = "", role = "" }) {
  return rows.filter((r) => (!system || r.system === system) && (!role || r.role === role));
}

/** Libellé d'un rôle.
 * @param {Role} role */
export function roleLabel(role) {
  return role === "source" ? "Source" : "Target";
}

/** Filtres Source / Target (`""` = tous).
 * @param {CapabilityRow[]} rows
 * @param {{ source?: string, target?: string }} filter
 * @returns {CapabilityRow[]} */
export function filterRows(rows, { source = "", target = "" }) {
  return rows.filter((r) => (!source || r.source === source) && (!target || r.target === target));
}

/** Symbole, libellé et ton (token sémantique de la charte, jamais une couleur codée en dur).
 * @param {RowState} state
 * @returns {{ symbol: string, label: string, tone: "success" | "warning" | "neutral" }} */
export function stateLabel(state) {
  switch (state) {
    case "available":
      return { symbol: "✓", label: "Available", tone: "success" };
    case "experimental":
      return { symbol: "✓", label: "Experimental (in test)", tone: "warning" };
    default:
      return { symbol: "—", label: "Not implemented", tone: "neutral" };
  }
}
