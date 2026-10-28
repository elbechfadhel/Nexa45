// @ts-check
/**
 * Profil d'affichage de la console : `full` (défaut, développement) ou `demo` (démonstration).
 *
 * Le profil est une CONFIGURATION de construction : `NEXT_PUBLIC_UIE_PROFILE=demo` au moment de `next build`
 * (posé par `scripts/demo-start.ps1`, jamais par `next dev`). Sans cette variable, rien ne change.
 *
 * En démo, seuls restent visibles les use cases VALIDÉS : ceux qui sont `available`, plus ceux que l'utilisateur
 * a validés par son test manuel (`DEMO_VALIDATED`), affichés comme disponibles. Les use cases `experimental`,
 * `coming_soon` et `deprecated` sont masqués, ainsi que les liens du header qui ne mènent qu'à eux (Smart export).
 * Présentation seulement : aucune page n'est retirée de l'application ni protégée par ce profil.
 */

/** @typedef {import("./catalog.mjs").UseCaseDefinition} UseCaseDefinition */
/** @typedef {"full" | "demo"} UiProfile */

/** Use cases encore expérimentaux, validés par le test manuel de l'utilisateur pour la démo (2026-10-28 : domaines
 * 1 à 4 de « Prepare data for DATEV »). */
export const DEMO_VALIDATED = ["migration.wizard.datev"];

/** Profil de cette construction (la référence littérale à la variable est remplacée par Next au build).
 * @returns {UiProfile} */
export function uiProfile() {
  return process.env.NEXT_PUBLIC_UIE_PROFILE === "demo" ? "demo" : "full";
}

/** Use cases visibles dans un profil : tous en `full` ; en `demo`, les disponibles et les validés (affichés
 * disponibles).
 * @param {UseCaseDefinition[]} catalog
 * @param {UiProfile} [profile]
 * @returns {UseCaseDefinition[]} */
export function visibleUseCases(catalog, profile = uiProfile()) {
  if (profile !== "demo") return catalog;
  return catalog
    .filter((u) => u.status === "available" || DEMO_VALIDATED.includes(u.id))
    .map((u) => (u.status === "available" ? u : { ...u, status: "available" }));
}

/** Lignes « System integrations » d'un profil : en `demo`, seulement les directions portées par un use case visible
 * (une direction dont le use case est masqué n'est pas « Not implemented »).
 * @template {{ useCase: unknown }} R
 * @param {R[]} rows
 * @param {UiProfile} [profile]
 * @returns {R[]} */
export function visibleRows(rows, profile = uiProfile()) {
  return profile === "demo" ? rows.filter((r) => r.useCase) : rows;
}

/** Un lien du header reste-t-il visible ? En `demo`, un lien caché est celui dont la page n'appartient qu'à des use
 * cases masqués ; une page qui n'est citée par aucun use case (Overview, Capabilities…) reste visible.
 * @param {string} href
 * @param {UseCaseDefinition[]} catalog
 * @param {UiProfile} [profile] */
export function linkVisible(href, catalog, profile = uiProfile()) {
  if (profile !== "demo") return true;
  const owns = (/** @type {UseCaseDefinition} */ u) => [...u.routes, ...(u.start ? [u.start.route] : [])].includes(href);
  const owners = catalog.filter(owns);
  if (owners.length === 0) return true;
  const shown = new Set(visibleUseCases(catalog, profile).map((u) => u.id));
  return owners.some((u) => shown.has(u.id));
}
