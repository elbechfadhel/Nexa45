// Tests de la vue « Capabilities » (node --test) : une ligne par use case et par direction, statuts lus dans le
// catalogue, les deux directions de chaque paire, filtres, aucune donnée en dur dans la page.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  capabilityRows,
  capabilitySystems,
  directions,
  filterRows,
  filterSystemRows,
  flowSystems,
  pairsOf,
  roleLabel,
  stateLabel,
  systemCapabilityRows,
} from "./capabilities.mjs";
import { listUseCases, SYSTEM_NAMES, USE_CASES } from "./catalog.mjs";

/** Use case minimal pour les tests (seuls les champs lus comptent). */
const uc = (id, status, source, target, domain = "partner") =>
  /** @type {any} */ ({ id, status, title: id, domain, systems: { source, target } });

test("one row per use case and direction; a direction without use case gets one 'Not implemented' row", () => {
  const list = [
    uc("p.datev", "available", ["odoo", "lexware"], ["datev"]),
    uc("p.lex", "experimental", ["odoo"], ["lexware"]),
    uc("a.planned", "coming_soon", ["odoo"], ["datev"]),
    uc("import", "available", ["file"], ["odoo"]), // un fichier n'est pas un système de la vue
  ];
  const rows = capabilityRows(list, ["odoo", "lexware", "datev"]);
  const text = rows.map((r) => `${r.source}>${r.target}:${r.useCase?.id ?? "-"}:${r.state}`);
  assert.deepEqual(text, [
    "odoo>lexware:p.lex:experimental",
    "lexware>odoo:-:not_implemented",
    "odoo>datev:p.datev:available",
    "odoo>datev:a.planned:not_implemented",
    "datev>odoo:-:not_implemented",
    "lexware>datev:p.datev:available",
    "datev>lexware:-:not_implemented",
  ]);
  assert.deepEqual(pairsOf(list[3]), []);
});

test("both directions of every pair of systems", () => {
  assert.deepEqual(directions(["odoo", "lexware"]), [
    { source: "odoo", target: "lexware" },
    { source: "lexware", target: "odoo" },
  ]);
});

test("filters by source and target", () => {
  const rows = capabilityRows([uc("x", "available", ["odoo"], ["datev"])], ["odoo", "datev"]);
  assert.equal(filterRows(rows, {}).length, 2);
  assert.deepEqual(
    filterRows(rows, { source: "odoo" }).map((r) => r.target),
    ["datev"],
  );
  assert.deepEqual(
    filterRows(rows, { source: "datev", target: "odoo" }).map((r) => r.state),
    ["not_implemented"],
  );
  assert.equal(filterRows(rows, { source: "odoo", target: "odoo" }).length, 0);
});

test("real catalog: Odoo, Lexware Office, DATEV, six directions, statuses from the catalog", () => {
  const order = Object.keys(SYSTEM_NAMES);
  const useCases = listUseCases("supplier");
  assert.deepEqual(flowSystems(useCases, order), ["odoo", "lexware", "datev"]);
  const rows = capabilityRows(useCases, order);
  assert.equal(new Set(rows.map((r) => `${r.source}>${r.target}`)).size, 6);
  const byId = Object.fromEntries(USE_CASES.map((u) => [u.id, u.status]));
  for (const r of rows.filter((x) => x.useCase)) {
    const id = r.useCase?.id ?? "";
    const expected = { available: "available", experimental: "experimental" }[byId[id]] ?? "not_implemented";
    assert.equal(r.state, expected, id);
  }
});

test("system capabilities: use cases without a flow, one row per system, ordered by system then catalog", () => {
  const list = [
    uc("connect", "available", ["odoo", "lexware"], []),
    uc("flow", "available", ["odoo"], ["datev"]), // un flux : jamais dans ce tableau
    uc("import", "experimental", ["file"], ["odoo"]), // fichier → système : propre au système cible
    uc("read", "coming_soon", ["lexware"], []),
    uc("old", "deprecated", ["odoo"], []),
    uc("internal", "available", ["go45"], []), // plateforme : pas un système
  ];
  const rows = systemCapabilityRows(list, ["odoo", "lexware", "datev"]);
  assert.deepEqual(
    rows.map((r) => `${r.system}:${r.useCase.id}:${r.state}`),
    ["odoo:connect:available", "odoo:import:experimental", "lexware:connect:available", "lexware:read:not_implemented"],
  );
  assert.deepEqual(capabilitySystems(rows), ["odoo", "lexware"]);
  assert.deepEqual(
    filterSystemRows(rows, { system: "lexware" }).map((r) => r.useCase.id),
    ["connect", "read"],
  );
  assert.equal(filterSystemRows(rows, {}).length, 4);
});

test("real catalog: every system capability is a supplier use case without a flow, status from the catalog", () => {
  const useCases = listUseCases("supplier");
  const rows = systemCapabilityRows(useCases, Object.keys(SYSTEM_NAMES));
  assert.ok(rows.length > 0);
  const byId = Object.fromEntries(USE_CASES.map((u) => [u.id, u]));
  for (const r of rows) {
    const u = byId[r.useCase.id];
    assert.equal(pairsOf(u).length, 0, r.useCase.id);
    assert.ok([...u.systems.source, ...u.systems.target].includes(r.system), r.useCase.id);
    const expected = { available: "available", experimental: "experimental" }[u.status] ?? "not_implemented";
    assert.equal(r.state, expected, r.useCase.id);
  }
  // aucun flux du premier tableau ne réapparaît ici
  const flows = new Set(capabilityRows(useCases, Object.keys(SYSTEM_NAMES)).map((r) => r.useCase?.id));
  assert.ok(rows.every((r) => !flows.has(r.useCase.id)));
});

test("system capabilities: source and target roles", () => {
  const rows = systemCapabilityRows(
    [uc("connect", "available", ["odoo"], []), uc("import", "available", ["file"], ["odoo"])],
    ["odoo"],
  );
  assert.deepEqual(
    rows.map((r) => `${r.useCase.id}:${roleLabel(r.role)}`),
    ["connect:Source", "import:Target"],
  );
  assert.deepEqual(
    filterSystemRows(rows, { role: "target" }).map((r) => r.useCase.id),
    ["import"],
  );
});

test("a target served by Registry formats gets the use cases aimed at it, once each, with its formats", () => {
  const list = [
    uc("p.file", "available", ["odoo", "lexware"], ["datev"], "partner"),
    uc("a.file", "experimental", ["odoo", "lexware"], ["datev"], "accounting"),
    uc("a.submit", "coming_soon", ["odoo"], ["datev"], "accounting"),
    uc("p.api", "experimental", ["odoo"], ["lexware"], "partner"), // cible par API : déjà dans « System integrations »
  ];
  const formats = [
    { system: "datev", domain: "partner", name: "Debitoren/Kreditoren", version: "5" },
    { system: "datev", domain: "accounting", name: "Buchungsstapel", version: "13" },
  ];
  const rows = systemCapabilityRows(list, ["odoo", "lexware", "datev"], formats);
  assert.deepEqual(
    rows.map((r) => `${r.system}:${r.role}:${r.useCase.id}:${r.state}:${r.formats.join("|")}`),
    [
      "datev:target:p.file:available:Debitoren/Kreditoren 5",
      "datev:target:a.file:experimental:Buchungsstapel 13",
      "datev:target:a.submit:not_implemented:Buchungsstapel 13",
    ],
  );
  // sans format du Registry, rien n'est ajouté (aucune capacité inventée)
  assert.equal(systemCapabilityRows(list, ["datev"], []).length, 0);
});

test("real catalog: DATEV target capabilities come from the catalog, one row per use case", () => {
  const formats = [
    { system: "datev", domain: "partner", name: "Debitoren/Kreditoren", version: "5" },
    { system: "datev", domain: "accounting", name: "Buchungsstapel", version: "13" },
  ];
  const useCases = listUseCases("supplier");
  const rows = systemCapabilityRows(useCases, Object.keys(SYSTEM_NAMES), formats).filter((r) => r.system === "datev");
  const expected = useCases.filter((u) => u.systems.target.includes("datev")).map((u) => u.id);
  assert.deepEqual(
    rows.map((r) => r.useCase.id),
    expected,
  );
  assert.ok(rows.every((r) => r.role === "target"));
  assert.ok(expected.includes("partner.export.datev") && expected.includes("accounting.export.datev"));
  assert.ok(expected.includes("accounting.datev.submit"));
});

test("labels: Available, Experimental (in test), Not implemented", () => {
  assert.deepEqual(stateLabel("available"), { symbol: "✓", label: "Available", tone: "success" });
  assert.deepEqual(stateLabel("experimental"), { symbol: "✓", label: "Experimental (in test)", tone: "warning" });
  assert.deepEqual(stateLabel("not_implemented"), { symbol: "—", label: "Not implemented", tone: "neutral" });
});

test("the page hard-codes no status, no system and calls no write API", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const page = readFileSync(join(here, "..", "..", "app", "portal", "capabilities", "page.tsx"), "utf8");
  assert.doesNotMatch(page, /"(odoo|lexware|datev)"/i); // les systèmes viennent du catalogue
  assert.doesNotMatch(page, /"(available|experimental|coming_soon|not_implemented)"/); // les statuts aussi
  assert.doesNotMatch(page, /api\.(POST|PUT|DELETE)|useMutation/); // lecture seule
  assert.doesNotMatch(page, /n\/a/); // plus de « n/a »
  assert.match(page, /capabilityRows\(/);
  assert.match(page, /systemCapabilityRows\(/);
  assert.match(page, /System integrations/);
  assert.match(page, /System capabilities/);
});
