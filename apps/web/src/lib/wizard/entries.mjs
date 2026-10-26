// @ts-check
/**
 * Points d'entrée du mode Simple : les MÊMES composants (`components/wizard-simple`), seule une configuration
 * change — titre, texte d'introduction, cibles affichées. Logique pure (node --test, simple.test.mjs). Les
 * composants ne nomment aucun système : un point d'entrée propre à un système (ex. un parcours pour les cabinets)
 * le déclare ici, par son code du Registry.
 */
import { SIMPLE_LABELS as L } from "./simple-labels.mjs";

/** @typedef {import("./simple.mjs").TargetChoice} TargetChoice */
/**
 * @typedef {{
 *   title: string,
 *   intro: string,
 *   targetHeading?: string,
 *   systems?: string[],
 *   groups?: TargetChoice["group"][],
 *   detect?: boolean,
 * }} WizardEntry
 * `targetHeading` : intitulé du choix de la cible (absent : celui du Wizard, « 2. Where should your data go? ») ;
 * `systems` : codes des systèmes du Registry affichés (tous si absent) ; `groups` : groupes de cibles affichés
 * (tous si absent) ; `detect` : dès le dépôt du fichier, le format est choisi d'après le type reconnu.
 */

/** @type {{ general: WizardEntry, datev: WizardEntry }} */
export const WIZARD_ENTRIES = {
  // Wizard général : tous les systèmes
  general: { title: L.title, intro: L.intro },
  // parcours des cabinets (Steuerberater) : les seuls formats DATEV du Registry (Bookings, Customers and
  // suppliers) ; ni vos systèmes connectés, ni les systèmes dont la structure serait proposée par l'IA
  datev: {
    title: "Prepare data for DATEV",
    intro:
      "Add your customer or supplier list, or your sales or purchase invoices, check what DATEV should receive, and download the file prepared for DATEV. Nothing is written to your systems.",
    targetHeading: "2. What do you want to prepare?",
    systems: ["datev"],
    groups: ["registry"],
    detect: true,
  },
};

/** Cibles affichées par un point d'entrée, dans l'ordre de l'API : groupe et système de la configuration.
 * Une cible sans système du Registry (votre système connecté, un système connu) ne passe un filtre de systèmes
 * que si elle en porte un.
 * @param {TargetChoice[]} targets
 * @param {WizardEntry} entry
 * @returns {TargetChoice[]} */
export function entryTargets(targets, entry) {
  return targets.filter(
    (t) =>
      (!entry.groups || entry.groups.includes(t.group)) &&
      (!entry.systems || (t.system != null && entry.systems.includes(t.system))),
  );
}
