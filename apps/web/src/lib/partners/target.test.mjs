// Tests de la présentation de l'aperçu « clients / fournisseurs → système cible » (node --test).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  actionLabel,
  confirmationCounts,
  countsSentence,
  filterByStatus,
  keepSelectable,
  kindLabel,
  preparationStatus,
  rolesLabel,
  selectableRefs,
  sendable,
  sendItemStatus,
  sendRunStatus,
  sendState,
} from "./target.mjs";

test("preparation status and send history are two separate axes", () => {
  assert.deepEqual(preparationStatus("ready"), { label: "Ready", tone: "success" });
  assert.deepEqual(preparationStatus("warning"), { label: "Warning", tone: "warning" });
  assert.deepEqual(preparationStatus("blocked"), { label: "Blocked", tone: "danger" });
  assert.deepEqual(sendState("new"), { label: "New", tone: "info" });
  assert.deepEqual(sendState("already_sent"), { label: "Already sent", tone: "neutral" });
  assert.deepEqual(sendState("changed_since_sent"), { label: "Changed since sent", tone: "warning" });
  assert.notEqual(preparationStatus("ready").label, sendState("new").label);
});

test("kind and roles are shown in plain words", () => {
  assert.equal(kindLabel("company"), "Company");
  assert.equal(kindLabel("person"), "Person");
  assert.equal(kindLabel("unknown"), "Unknown");
  assert.equal(rolesLabel(["customer"]), "Customer");
  assert.equal(rolesLabel(["customer", "supplier"]), "Customer & Supplier");
  assert.equal(rolesLabel([]), "No role");
});

test("counts come from the backend; a blocked partner is never counted as sendable", () => {
  const c = { partners: 6, ready: 3, warning: 1, blocked: 2 };
  assert.equal(countsSentence(c), "6 read · 3 ready · 1 with warnings · 2 blocked");
  assert.equal(sendable(c), 4);
  const partners = [{ status: "ready" }, { status: "blocked" }, { status: "warning" }, { status: "ready" }];
  assert.equal(filterByStatus(partners, "all").length, 4);
  assert.equal(filterByStatus(partners, "blocked").length, 1);
  assert.equal(filterByStatus(partners, "ready").length, 2);
});

test("the send pages name no system and send only after an explicit confirmation", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const shared = readFileSync(join(here, "..", "..", "components", "target-send.tsx"), "utf8");
  for (const domain of ["partners", "products"]) {
    const dir = join(here, "..", "..", "app", "portal", "systems", "[systemId]", domain, "[targetId]");
    const view = readFileSync(join(dir, "preview-view.tsx"), "utf8");
    for (const code of [view, shared]) {
      // aucun système nommé : source et cible viennent de l'API
      assert.doesNotMatch(code, /odoo|lexware|datev|sap\b/i, domain);
      // envoi : jamais d'appel direct depuis la page (hooks seulement)
      assert.doesNotMatch(code, /method:\s*"(POST|PUT|DELETE)"|api\.(POST|PUT|DELETE)/, domain);
    }
    // le concept est générique : « Source reference », jamais l'identifiant d'un système particulier
    assert.match(view, /Source reference/, domain);
    // une confirmation explicite (composant partagé) et une clé d'envoi générée à l'ouverture
    assert.match(view, /<Confirmation/, domain);
    assert.match(view, /randomUUID\(\)/, domain);
    assert.match(view, /selectableRefs\(/, domain); // la page ne décide pas elle-même de ce qui est envoyable
    assert.match(view, /notice/, domain);
  }
  assert.match(shared, /Confirm and send/);
});

test("send: only backend-sendable records can be selected, blocked never", () => {
  const partners = [
    { source_reference: "a", sendable: true, action: "create" },
    { source_reference: "b", sendable: false, action: "none" },
    { source_reference: "c", sendable: true, action: "update" },
  ];
  assert.deepEqual(selectableRefs(partners), ["a", "c"]);
  assert.deepEqual(keepSelectable(["a", "b", "x"], selectableRefs(partners)), ["a"]);
  assert.deepEqual(confirmationCounts(partners, ["a", "c"]), { total: 2, create: 1, update: 1 });
  assert.equal(actionLabel("none"), null);
  assert.equal(actionLabel("update")?.label, "Will update");
});

test("send: statuses are labels of backend decisions", () => {
  assert.equal(sendItemStatus("unknown").label, "Unknown outcome");
  assert.equal(sendItemStatus("needs_decision").tone, "warning");
  assert.equal(sendItemStatus("skipped").label, "Skipped");
  assert.equal(sendRunStatus("partial").label, "Partly sent");
});
