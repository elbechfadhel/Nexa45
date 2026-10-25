// Tests du formulaire de connexion piloté par les champs du connecteur (node --test, sans dépendance).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  emptyValues,
  fillFrom,
  inputType,
  isComplete,
  submittedValues,
  summaryRows,
  testedSignature,
} from "./form.mjs";

// Deux formulaires tels que deux connecteurs les déclarent (valeurs inventées) : la page n'en connaît aucun.
const ERP = [
  { name: "url", label: "Server URL", kind: "url", required: true },
  { name: "database", label: "Database", kind: "text", required: true },
  { name: "username", label: "Username", kind: "text", required: true },
  { name: "password", label: "Password or API key", kind: "secret", required: true },
];
const API_KEY_ONLY = [{ name: "api_key", label: "API key", kind: "secret", required: true }];

test("a system with four fields and a system with one field use the same form logic", () => {
  assert.deepEqual(emptyValues(ERP), { url: "", database: "", username: "", password: "" });
  assert.deepEqual(emptyValues(API_KEY_ONLY), { api_key: "" });

  const erp = { url: " https://erp.example.test ", database: "main", username: "admin", password: " p w " };
  assert.equal(isComplete("My ERP", ERP, erp), true);
  assert.equal(isComplete("", ERP, erp), false);
  assert.equal(isComplete("My ERP", ERP, { ...erp, database: "  " }), false);

  assert.equal(isComplete("Books", API_KEY_ONLY, { api_key: "k-123" }), true);
  assert.equal(isComplete("Books", API_KEY_ONLY, { api_key: "" }), false);
});

test("only declared fields are sent; a secret is sent untouched, other values are trimmed", () => {
  const sent = submittedValues(ERP, {
    url: " https://erp.example.test ",
    database: " main ",
    username: "admin",
    password: " p w ",
    injected: "x",
  });
  assert.deepEqual(sent, {
    url: "https://erp.example.test",
    database: "main",
    username: "admin",
    password: " p w ",
  });
});

test("a secret is masked, never summarised, and a test only holds for the exact tested values", () => {
  const values = { api_key: "k-123" };
  assert.equal(inputType(API_KEY_ONLY[0], false), "password");
  assert.equal(inputType(API_KEY_ONLY[0], true), "text");
  assert.equal(inputType(ERP[0], false), "url");
  assert.deepEqual(summaryRows(API_KEY_ONLY, values), []);

  const erp = { url: "https://erp.example.test", database: "main", username: "admin", password: "secret-value" };
  const rows = summaryRows(ERP, erp);
  assert.deepEqual(rows.map((r) => r.label), ["Server URL", "Database", "Username"]);
  assert.ok(!JSON.stringify(rows).includes("secret-value"));

  const before = testedSignature("books", API_KEY_ONLY, values);
  assert.equal(before, testedSignature("books", API_KEY_ONLY, { api_key: "k-123" }));
  assert.notEqual(before, testedSignature("books", API_KEY_ONLY, { api_key: "k-124" }));
  assert.notEqual(before, testedSignature("other", API_KEY_ONLY, values));
});

test("development fill only touches declared fields", () => {
  assert.deepEqual(fillFrom(API_KEY_ONLY, { url: "x", api_key: "k" }), { api_key: "k" });
  assert.deepEqual(fillFrom(ERP, { url: "u", database: "d" }), { url: "u", database: "d", username: "", password: "" });
});

test("the connect page has no system-specific condition and no hardcoded connection field", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const dir = join(here, "..", "..", "app", "portal", "systems", "connect");
  const page = readFileSync(join(dir, "page.tsx"), "utf8");
  const steps = readFileSync(join(dir, "wizard-steps.tsx"), "utf8");
  for (const source of [page, steps]) {
    // aucun branchement sur un code de système, aucun champ de connexion écrit dans la page
    assert.doesNotMatch(source, /[!=]==\s*["'](odoo|lexware|datev|sap)["']/);
    assert.doesNotMatch(source, /(form|values)\.(url|database|username|password|api_key)\b/);
    assert.doesNotMatch(source, /label=["'](Database|Username|API key|Password|Server URL)/);
  }
  // les champs affichés sont ceux du connecteur
  assert.match(steps, /connector\.fields\.map/);
  assert.match(page, /connector\.fields/);
});
