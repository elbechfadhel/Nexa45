// Tests du catalogue des use cases (node --test, sans dépendance). Lancés par `pnpm test` et par pytest
// (tests/test_web_use_cases.py). Garde-fou : une page de l'application sans use case fait échouer la suite.
import assert from "node:assert/strict";
import { readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  canStart,
  groupBySection,
  SECTIONS,
  SYSTEM_NAMES,
  sectionTitle,
  listOtherUseCases,
  USE_CASES,
  listUseCases,
  validateCatalog,
} from "./catalog.mjs";

const APP = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "app");

/** Routes réelles de l'application (dossiers contenant un page.tsx), au format « /instances/[id] ». */
function appPages(dir = APP) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...appPages(path));
    else if (name === "page.tsx") {
      const route = "/" + relative(APP, dir).split(sep).filter(Boolean).join("/");
      out.push(route === "/" ? "/" : route.replace(/\/$/, ""));
    }
  }
  return out.sort();
}

/** Use case minimal valide, pour les cas construits. */
function uc(overrides) {
  return {
    id: "demo.case",
    domain: "product",
    audiences: ["admin"],
    status: "available",
    section: "product",
    order: 1,
    title: "Demo",
    summary: "Demo",
    goal: "Demo",
    flow: ["A", "B"],
    systems: { source: ["odoo"], target: [] },
    steps: ["Step"],
    expected: [],
    prerequisites: [],
    start: { route: "/systems", label: "Open" },
    routes: [],
    ...overrides,
  };
}

test("the real catalog is valid against the real application pages", () => {
  assert.deepEqual(validateCatalog(USE_CASES, appPages()), []);
});

test("every application page is covered by a use case or explicitly untracked", () => {
  const pages = appPages();
  assert.ok(pages.includes("/portal/exports") && pages.includes("/instances/[id]"));
  const errors = validateCatalog([...USE_CASES, uc({ id: "extra.case" })], [...pages, "/portal/new-feature"]);
  assert.ok(errors.some((e) => e.includes("page /portal/new-feature : aucun use case")), errors.join("\n"));
});

test("unique ids, valid audiences, statuses and sections", () => {
  const errors = validateCatalog([
    uc({ id: "a.b" }),
    uc({ id: "a.b" }),
    uc({ id: "c.d", audiences: ["guest"] }),
    uc({ id: "e.f", status: "beta" }),
    uc({ id: "g.h", section: "nowhere" }),
    uc({ id: "i.j", audiences: [] }),
  ]);
  for (const part of ["a.b : identifiant en double", "audience inconnue guest", "statut inconnu beta", "section inconnue", "i.j : aucune audience"]) {
    assert.ok(errors.some((e) => e.includes(part)), part);
  }
});

test("an available use case needs an existing, navigable start route", () => {
  const pages = ["/systems", "/instances/[id]"];
  assert.ok(validateCatalog([uc({ start: undefined })], pages).some((e) => e.includes("sans point de départ")));
  assert.ok(validateCatalog([uc({ start: { route: "/nowhere", label: "x" } })], pages).some((e) => e.includes("route inexistante")));
  assert.ok(validateCatalog([uc({ start: { route: "/instances/[id]", label: "x" } })], pages).some((e) => e.includes("non navigable")));
  const ok = validateCatalog([uc({ start: { route: "/systems?x=1", label: "x" } })], pages);
  assert.ok(!ok.some((e) => e.includes("demo.case")), ok.join(" | ")); // paramètres de requête admis
});

test("coming soon and deprecated use cases cannot be started", () => {
  assert.ok(validateCatalog([uc({ status: "coming_soon" })]).some((e) => e.includes("ne peut pas être démarré")));
  assert.equal(canStart(uc({ status: "coming_soon", start: undefined })), false);
  assert.equal(canStart(uc({ status: "deprecated", start: undefined })), false);
  assert.equal(canStart(uc({ status: "experimental" })), true);
  assert.equal(canStart(uc({})), true);
  // l'envoi direct à DATEV (API) n'existe pas : à venir, non démarrable
  const submit = USE_CASES.find((u) => u.id === "accounting.datev.submit");
  assert.equal(submit.status, "coming_soon");
  assert.equal(canStart(submit), false);
  // l'export du FICHIER Buchungsstapel (phase 5, téléchargement local) est démarrable, expérimental
  const file = USE_CASES.find((u) => u.id === "accounting.export.datev");
  assert.equal(file.status, "experimental");
  assert.equal(canStart(file), true);
  // la lecture comptable (phase 3) est démarrable mais expérimentale
  const read = USE_CASES.find((u) => u.id === "accounting.read");
  assert.equal(read.status, "experimental");
  assert.equal(canStart(read), true);
  // la préparation DATEV (phase 4, aperçu sans fichier) est expérimentale ; l'export reste à venir
  const prepare = USE_CASES.find((u) => u.id === "accounting.datev.prepare");
  assert.equal(prepare.status, "experimental");
  assert.equal(canStart(prepare), true);
});

test("admin, supplier and both audiences are filtered from the same definitions", () => {
  const catalog = [
    uc({ id: "only.admin", audiences: ["admin"] }),
    uc({ id: "only.supplier", audiences: ["supplier"], start: { route: "/portal", label: "x" } }),
    uc({ id: "for.both", audiences: ["admin", "supplier"] }),
    uc({ id: "old.case", audiences: ["admin", "supplier"], status: "deprecated", start: undefined }),
  ];
  assert.deepEqual(listUseCases("admin", catalog).map((u) => u.id), ["only.admin", "for.both"]);
  assert.deepEqual(listUseCases("supplier", catalog).map((u) => u.id), ["only.supplier", "for.both"]);
  assert.deepEqual(listOtherUseCases("admin", catalog).map((u) => u.id), ["only.supplier"]);
  // une audience élargie apparaît des deux côtés, sans autre modification
  const widened = catalog.map((u) => (u.id === "only.admin" ? { ...u, audiences: ["admin", "supplier"] } : u));
  assert.ok(listUseCases("supplier", widened).some((u) => u.id === "only.admin"));
});

test("deprecated use cases are hidden from both overviews", () => {
  for (const audience of ["admin", "supplier"]) {
    assert.ok(listUseCases(audience).every((u) => u.status !== "deprecated"));
  }
});

test("changing a status is enough to make a use case startable (accounting acceptance criterion)", () => {
  const ready = USE_CASES.map((u) =>
    u.id === "accounting.datev.submit"
      ? { ...u, status: "available", start: { route: "/portal/exports", label: "Start demo" }, steps: ["Export"] }
      : u,
  );
  const item = listUseCases("supplier", ready).find((u) => u.id === "accounting.datev.submit");
  assert.equal(canStart(item), true);
  assert.deepEqual(validateCatalog(ready, appPages()), []);
});

test("supplier use cases stay in the portal, admin use cases in the console", () => {
  const errors = validateCatalog([
    uc({ id: "bad.supplier", audiences: ["supplier"], start: { route: "/systems", label: "x" } }),
    uc({ id: "bad.admin", audiences: ["admin"], start: { route: "/portal/systems", label: "x" } }),
  ]);
  assert.ok(errors.some((e) => e.includes("bad.supplier")) && errors.some((e) => e.includes("bad.admin")));
  // aucun concept interne du Registry n'est proposé au fournisseur
  assert.ok(listUseCases("supplier").every((u) => u.domain !== "registry"));
});

test("supplier section titles hide internal concepts", () => {
  const systems = SECTIONS.find((s) => s.id === "systems");
  assert.equal(sectionTitle(systems, "admin"), "Systems & Registry");
  assert.ok(!/registry/i.test(sectionTitle(systems, "supplier")));
  for (const g of groupBySection(listUseCases("supplier"))) {
    assert.ok(!/registry|snapshot|compare/i.test(sectionTitle(g.section, "supplier")), g.section.id);
  }
});

test("Lexware Office: contacts, accounting and products (preview and send) can be started", () => {
  const lexware = USE_CASES.filter((u) => u.systems.target.includes("lexware"));
  assert.deepEqual(lexware.map((u) => u.id).sort(), [
    "accounting.export.lexware",
    "partner.export.lexware",
    "partner.lexware.prepare",
    "product.export.lexware",
    "product.lexware.prepare",
  ]);
  // aperçu et envoi des contacts (1a / 1b), aperçu des pièces (2c), aperçu et envoi des articles (1a / 1b) :
  // démarrables, expérimentaux
  const started = [
    "partner.lexware.prepare",
    "partner.export.lexware",
    "accounting.export.lexware",
    "product.lexware.prepare",
    "product.export.lexware",
  ];
  for (const id of started) {
    const u = lexware.find((x) => x.id === id);
    assert.equal(u.status, "experimental", id);
    assert.equal(canStart(u), true, id);
    assert.ok(u.expected.some((e) => /until you select records and confirm|before Confirm and send|nothing is sent/i.test(e)), id);
  }
  for (const u of lexware.filter((x) => !started.includes(x.id))) {
    assert.equal(u.status, "coming_soon", u.id);
    assert.equal(u.start, undefined, u.id);
    assert.equal(canStart(u), false, u.id);
    assert.deepEqual(u.audiences, ["supplier"], u.id);
  }
  // les use cases DATEV ne changent pas de statut
  const byId = Object.fromEntries(USE_CASES.map((u) => [u.id, u.status]));
  assert.equal(byId["accounting.export.datev"], "experimental");
  assert.equal(byId["accounting.datev.submit"], "coming_soon");
});

test("sections are ordered and never empty", () => {
  const groups = groupBySection(listUseCases("admin"));
  assert.ok(groups.every((g) => g.items.length > 0));
  assert.deepEqual(groups.map((g) => g.section.order), [...groups.map((g) => g.section.order)].sort((a, b) => a - b));
});

test("every use case declares the systems it really involves", () => {
  for (const u of USE_CASES) assert.ok(u.systems, u.id);
  const byId = Object.fromEntries(USE_CASES.map((u) => [u.id, u.systems]));
  // Lexware Office → fichier DATEV : domain-packs/tests/test_lexware_datev_e2e.py
  assert.deepEqual(byId["accounting.export.datev"], { source: ["odoo", "lexware"], target: ["datev"] });
  assert.deepEqual(byId["partner.export.datev"], { source: ["odoo", "lexware"], target: ["datev"] });
  assert.deepEqual(byId["product.import"], { source: ["file"], target: ["odoo"] });
  // use case interne à la plateforme : tuile GO45, jamais une carte sans logo
  assert.deepEqual(byId["organizations.manage"], { source: ["go45"], target: [] });
  assert.ok(USE_CASES.every((u) => u.systems.source.length > 0));
  // un use case dont le titre nomme un système le déclare
  for (const u of USE_CASES) {
    const all = [...u.systems.source, ...u.systems.target];
    for (const [code, name] of Object.entries(SYSTEM_NAMES))
      if (code !== "file" && code !== "go45" && u.title.includes(name)) assert.ok(all.includes(code), `${u.id} : ${name}`);
  }
});

test("an unknown system, a missing declaration or a use case without any system is refused", () => {
  assert.ok(validateCatalog([uc({ systems: { source: ["sap"], target: [] } })]).some((e) => e.includes("sap")));
  assert.ok(validateCatalog([uc({ systems: undefined })]).some((e) => e.includes("systems")));
  assert.ok(validateCatalog([uc({ systems: { source: [], target: ["datev"] } })]).some((e) => e.includes("go45")));
  assert.ok(validateCatalog([uc({ systems: { source: [], target: [] } })]).some((e) => e.includes("go45")));
});
