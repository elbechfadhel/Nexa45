// Tests des modèles de questionnaire de l'AI Migration Wizard (node --test).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { cleanName, defaultTemplateName, restoredState, templateMeta, templateSteps } from "./templates.mjs";

/** @param {string} id @param {string} label */
const step = (id, label) => ({
  question: { id, topic: id, title: `Q ${id}`, help: null, choices: [], allow_other: true, other_label: null },
  answer: { question_id: id, topic: id, question: `Q ${id}`, value: label.toLowerCase(), label, source: "ai" },
});

test("a template keeps the questions and answers only, never anything else", () => {
  const history = [{ ...step("target_system", "Odoo"), file: "x.csv", mapping: { title: "name" } }];
  assert.deepEqual(templateSteps(/** @type {any} */ (history)), [step("target_system", "Odoo")]);
});

test("use restores the answers with no question to ask again", () => {
  const steps = [step("target_system", "Odoo"), step("version", "18")];
  assert.deepEqual(restoredState(steps, "simple"), {
    history: steps,
    current: null, // aucune question reposée : le questionnaire n'est pas refait
    remaining: 0,
    proposal: null,
    mode: "simple",
  });
  assert.equal(restoredState(steps, "expert").mode, "expert"); // le mode courant est gardé
});

test("default name from the first answers, unique among the user's templates", () => {
  const history = [step("target_system", "Odoo"), step("version", "18"), step("kind", "Goods")];
  assert.equal(defaultTemplateName(history, []), "Odoo · 18");
  assert.equal(defaultTemplateName(history, ["odoo · 18", "Odoo · 18 (2)"]), "Odoo · 18 (3)");
  assert.equal(defaultTemplateName([], []), "My migration");
  assert.equal(cleanName("  Shop   catalog "), "Shop catalog");
  assert.equal(cleanName("   "), null);
  assert.equal(templateMeta({ answered: 1, updated_at: "2026-10-21T10:00:00Z" }), "1 answer · 21 Oct 2026");
});

test("the page offers the templates sidebar without touching the questionnaire flow", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const page = readFileSync(join(here, "..", "..", "app", "portal", "wizard", "page.tsx"), "utf8");
  const bar = readFileSync(join(here, "..", "..", "components", "wizard-templates.tsx"), "utf8");
  assert.match(page, /<TemplateSidebar/);
  assert.match(page, /restoredState\(/);
  // « Use » : la structure est demandée, jamais la question suivante (pas de useWizardNext pour un modèle)
  assert.match(page, /function onUseTemplate[\s\S]*?propose\.mutate\(/);
  assert.doesNotMatch(page.match(/function onUseTemplate[\s\S]*?\n {2}}/)?.[0] ?? "", /ask\(|next\.mutate/);
  for (const label of ["New migration", "Save current answers", "Rename", "Delete", "Templates"]) {
    assert.match(bar, new RegExp(label));
  }
  // un modèle n'envoie que les étapes du questionnaire
  assert.match(bar, /templateSteps\(history\)/);
  assert.doesNotMatch(bar, /decisions|fills|mapping|File\b/);
});
