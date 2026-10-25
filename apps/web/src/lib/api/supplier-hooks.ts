"use client";

/** Hooks de l'espace fournisseur (Supplier API `/api/*`). Aucune donnée technique ni du système source :
 *  on affiche ce que renvoient les DTO fournisseur. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_URL, type Schemas, unwrap } from "./client";

export type SupplierSystem = Schemas["SupplierSystemOut"];
export type CatalogSummary = Schemas["CatalogOut"];
export type SupplierQuestion = Schemas["QuestionOut"];

// --------------------------------------------------------------------------- //
// Analyse du catalogue côté fournisseur (V2b.2) : même moteur V1, DTO fournisseur
// --------------------------------------------------------------------------- //
/** Domaine de l'analyse : le catalogue produit (PRODUCT), les clients / fournisseurs (PARTNER) ou les pièces
 *  comptables (ACCOUNTING). Même moteur, mêmes questions et réponses (D8) ; seules les adresses de l'API changent. */
export type AnalysisDomain = "catalog" | "partners" | "accounting";

export function useCatalogSummary(systemId: string, domain: AnalysisDomain = "catalog") {
  const params = { params: { path: { system_id: systemId } } };
  return useQuery({
    queryKey: ["supplier", `${domain}-summary`, systemId],
    queryFn: () =>
      unwrap(
        domain === "accounting"
          ? api.GET("/api/systems/{system_id}/accounting/analysis", params)
          : domain === "partners"
            ? api.GET("/api/systems/{system_id}/partners/analysis", params)
            : api.GET("/api/systems/{system_id}/catalog", params),
      ),
  });
}

export function useSupplierQuestions(systemId: string, enabled: boolean, domain: AnalysisDomain = "catalog") {
  const params = { params: { path: { system_id: systemId } } };
  return useQuery({
    queryKey: ["supplier", `${domain}-questions`, systemId],
    enabled,
    queryFn: () =>
      unwrap(
        domain === "accounting"
          ? api.GET("/api/systems/{system_id}/accounting/questions", params)
          : domain === "partners"
            ? api.GET("/api/systems/{system_id}/partners/questions", params)
            : api.GET("/api/systems/{system_id}/catalog/questions", params),
      ),
  });
}

export function useSupplierVocabulary(enabled: boolean, domain: AnalysisDomain = "catalog") {
  return useQuery({
    queryKey: ["supplier", `${domain}-vocabulary`],
    enabled,
    queryFn: () =>
      unwrap(
        domain === "accounting"
          ? api.GET("/api/accounting/vocabulary")
          : domain === "partners"
            ? api.GET("/api/partners/vocabulary")
            : api.GET("/api/catalog/vocabulary"),
      ),
    staleTime: 10 * 60_000,
  });
}

export type Answer =
  | { action: "confirm"; body: Schemas["ConfirmAnswerIn"] }
  | { action: "change"; body: Schemas["ChangeAnswerIn"] }
  | { action: "ignore"; body: Schemas["NoteAnswerIn"] };

/** « Mark all as not relevant » : le MÊME ignore que le bouton unitaire, envoyé champ par champ (aucune API
 *  nouvelle), puis un seul rafraîchissement. S'arrête à la première erreur (les champs déjà traités le restent). */
export function useIgnoreAll(systemId: string, domain: AnalysisDomain = "catalog") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (fields: { entity: string; field: string }[]) => {
      for (const { entity, field } of fields) {
        const req = { params: { path: { system_id: systemId, entity, field } }, body: { note: null } };
        await unwrap(
          domain === "accounting"
            ? api.POST("/api/systems/{system_id}/accounting/fields/{entity}/{field}/ignore", req)
            : domain === "partners"
              ? api.POST("/api/systems/{system_id}/partners/fields/{entity}/{field}/ignore", req)
              : api.POST("/api/systems/{system_id}/catalog/fields/{entity}/{field}/ignore", req),
        );
      }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

/** Réponse du fournisseur à une question (confirm / change / ignore) : même logique que V1. */
export function useAnswerQuestion(systemId: string, domain: AnalysisDomain = "catalog") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ entity, field, answer }: { entity: string; field: string; answer: Answer }) => {
      const params = { path: { system_id: systemId, entity, field } };
      if (domain === "accounting") {
        if (answer.action === "confirm")
          return unwrap(
            api.POST("/api/systems/{system_id}/accounting/fields/{entity}/{field}/confirm", { params, body: answer.body }),
          );
        if (answer.action === "change")
          return unwrap(
            api.POST("/api/systems/{system_id}/accounting/fields/{entity}/{field}/change", { params, body: answer.body }),
          );
        return unwrap(
          api.POST("/api/systems/{system_id}/accounting/fields/{entity}/{field}/ignore", { params, body: answer.body }),
        );
      }
      if (domain === "partners") {
        if (answer.action === "confirm")
          return unwrap(
            api.POST("/api/systems/{system_id}/partners/fields/{entity}/{field}/confirm", { params, body: answer.body }),
          );
        if (answer.action === "change")
          return unwrap(
            api.POST("/api/systems/{system_id}/partners/fields/{entity}/{field}/change", { params, body: answer.body }),
          );
        return unwrap(
          api.POST("/api/systems/{system_id}/partners/fields/{entity}/{field}/ignore", { params, body: answer.body }),
        );
      }
      if (answer.action === "confirm")
        return unwrap(
          api.POST("/api/systems/{system_id}/catalog/fields/{entity}/{field}/confirm", { params, body: answer.body }),
        );
      if (answer.action === "change")
        return unwrap(
          api.POST("/api/systems/{system_id}/catalog/fields/{entity}/{field}/change", { params, body: answer.body }),
        );
      return unwrap(
        api.POST("/api/systems/{system_id}/catalog/fields/{entity}/{field}/ignore", { params, body: answer.body }),
      );
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}
export type CatalogSync = Schemas["CatalogSyncOut"];
export type ProductListItem = Schemas["ProductListItem"];
export type ProductDetail = Schemas["ProductDetailOut"];
export type Price = Schemas["PriceOut"];

export function useSupplierSystems() {
  return useQuery({ queryKey: ["supplier", "systems"], queryFn: () => unwrap(api.GET("/api/systems")) });
}

export function useCatalogSync(systemId: string | undefined) {
  return useQuery({
    queryKey: ["supplier", "sync", systemId],
    enabled: !!systemId,
    queryFn: () =>
      unwrap(api.GET("/api/systems/{system_id}/catalog/sync", { params: { path: { system_id: systemId! } } })),
  });
}

function useSupplierAction<T>(run: () => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

export function useAnalyzeCatalog(systemId: string, domain: AnalysisDomain = "catalog") {
  const params = { params: { path: { system_id: systemId } } };
  return useSupplierAction(() =>
    unwrap(
      domain === "accounting"
        ? api.POST("/api/systems/{system_id}/accounting/analyze", params)
        : domain === "partners"
          ? api.POST("/api/systems/{system_id}/partners/analyze", params)
          : api.POST("/api/systems/{system_id}/catalog/analyze", params),
    ),
  );
}

export function useSyncCatalog(systemId: string) {
  return useSupplierAction(() =>
    unwrap(api.POST("/api/systems/{system_id}/catalog/sync", { params: { path: { system_id: systemId } } })),
  );
}

export function useProducts(systemId: string | undefined, search: string, page: number, pageSize = 25) {
  return useQuery({
    queryKey: ["supplier", "products", systemId, search, page, pageSize],
    enabled: !!systemId,
    queryFn: () =>
      unwrap(
        api.GET("/api/products", {
          params: { query: { system_id: systemId, search: search || undefined, page, page_size: pageSize } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useProduct(productId: string) {
  return useQuery({
    queryKey: ["supplier", "product", productId],
    queryFn: () => unwrap(api.GET("/api/products/{product_id}", { params: { path: { product_id: productId } } })),
  });
}

/** URL d'image (même origine : le cookie de session suit ; le navigateur met en cache). */
export function productImageUrl(productId: string, size: "thumb" | "large" = "thumb", variantId?: string) {
  const base = `${API_URL}/api/products/${productId}`;
  return `${variantId ? `${base}/variants/${variantId}` : base}/image?size=${size}`;
}

export function formatPrice(price: Price | null | undefined): string {
  if (!price) return "—";
  if (!price.currency) return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 }).format(price.amount);
  try {
    return new Intl.NumberFormat("fr-FR", { style: "currency", currency: price.currency }).format(price.amount);
  } catch {
    return `${price.amount} ${price.currency}`;
  }
}

// --------------------------------------------------------------------------- //
// Connecter un système (V2b.1)
// --------------------------------------------------------------------------- //
export type ConnectForm = Schemas["CheckConnectionIn"];
export type ConnectionCheck = Schemas["ConnectionCheckOut"];
export type Connector = Schemas["ConnectorOut"];

export function useSupplierSystem(systemId: string) {
  return useQuery({
    queryKey: ["supplier", "system", systemId],
    queryFn: () => unwrap(api.GET("/api/systems/{system_id}", { params: { path: { system_id: systemId } } })),
  });
}

/** Teste la connexion sans rien enregistrer. */
export function useCheckConnection() {
  return useMutation({
    mutationFn: (body: ConnectForm) => unwrap(api.POST("/api/systems/check-connection", { body })),
  });
}

/** Enregistre le système (le serveur re-teste avant) ; le mot de passe n'est jamais renvoyé. */
export function useConnectSystem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["ConnectSystemIn"]) => unwrap(api.POST("/api/systems", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

/** Supprime définitivement un système du fournisseur (V2b.1a) ; rien n'est modifié dans le système lui-même. */
export function useRemoveSystem(systemId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.DELETE("/api/systems/{system_id}", { params: { path: { system_id: systemId } } })),
    // la liste seulement : la page du système supprimé ne doit pas se recharger (404) avant la redirection
    onSuccess: () => qc.invalidateQueries({ queryKey: ["supplier", "systems"] }),
  });
}

/** Systèmes connectables et versions prises en charge (lues dans le Registry). */
export function useConnectors() {
  return useQuery({ queryKey: ["supplier", "connectors"], queryFn: () => unwrap(api.GET("/api/connectors")) });
}

// --------------------------------------------------------------------------- //
// Imports de fichiers catalogue (V2c.1) : compatibilité seulement, aucune écriture vers le système
// --------------------------------------------------------------------------- //
export type CatalogImport = Schemas["ImportOut"];
export type CatalogImportDetail = Schemas["ImportDetailOut"];
export type ImportColumn = Schemas["ImportColumnOut"];
export type ImportPreview = Schemas["ImportPreviewOut"];
export type ImportRun = Schemas["ImportRunOut"];
export type ImportTarget = Schemas["ImportTargetOut"];

export function useImports() {
  return useQuery({ queryKey: ["supplier", "imports"], queryFn: () => unwrap(api.GET("/api/imports")) });
}

export function useImport(importId: string) {
  return useQuery({
    queryKey: ["supplier", "import", importId],
    queryFn: () => unwrap(api.GET("/api/imports/{import_id}", { params: { path: { import_id: importId } } })),
  });
}

/** Preview d'import (V2d.1, lecture seule) : relu à chaque ouverture, jamais mis en cache longtemps. */
export function useImportPreview(importId: string) {
  return useQuery({
    queryKey: ["supplier", "import-preview", importId],
    staleTime: 0,
    queryFn: () =>
      unwrap(api.GET("/api/imports/{import_id}/preview", { params: { path: { import_id: importId } } })),
  });
}

/** Import réel (V2d.2) : la clé de requête rend l'appel idempotent (double clic, nouvel essai). */
export function useRunImport(importId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (requestKey: string) =>
      unwrap(
        api.POST("/api/imports/{import_id}/runs", {
          params: { path: { import_id: importId } },
          body: { request_key: requestKey },
        }),
      ),
    onSuccess: (run) => qc.setQueryData(["supplier", "import-run", importId, run.id], run),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "imports"] }),
  });
}

export function useResumeImportRun(importId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/imports/{import_id}/runs/{run_id}/resume", {
          params: { path: { import_id: importId, run_id: runId } },
        }),
      ),
    onSuccess: (run) => qc.setQueryData(["supplier", "import-run", importId, run.id], run),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "imports"] }),
  });
}

export function useImportRun(importId: string, runId: string) {
  return useQuery({
    queryKey: ["supplier", "import-run", importId, runId],
    queryFn: () =>
      unwrap(
        api.GET("/api/imports/{import_id}/runs/{run_id}", {
          params: { path: { import_id: importId, run_id: runId } },
        }),
      ),
  });
}

export function useImportTargets(importId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["supplier", "import-targets", importId],
    enabled,
    queryFn: () =>
      unwrap(api.GET("/api/imports/{import_id}/targets", { params: { path: { import_id: importId } } })),
  });
}

/** Dépôt du fichier (multipart) : seule la forme du fichier est analysée, rien n'est envoyé au système. */
export function useUploadImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) =>
      unwrap(
        api.POST("/api/imports", {
          body: { file: "" },
          bodySerializer: () => {
            const form = new FormData();
            form.append("file", file);
            return form;
          },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["supplier", "imports"] }),
  });
}

function useImportAction<V>(importId: string, run: (vars: V) => Promise<CatalogImportDetail>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: (detail) => qc.setQueryData(["supplier", "import", importId], detail),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "imports"] }),
  });
}

export function useAnalyzeImport(importId: string) {
  return useImportAction(importId, (systemId: string) =>
    unwrap(
      api.POST("/api/imports/{import_id}/analyze", {
        params: { path: { import_id: importId } },
        body: { system_id: systemId },
      }),
    ),
  );
}

export function useChooseTarget(importId: string) {
  return useImportAction(importId, ({ position, targetId }: { position: number; targetId: string }) =>
    unwrap(
      api.POST("/api/imports/{import_id}/columns/{position}/target", {
        params: { path: { import_id: importId, position } },
        body: { target_id: targetId },
      }),
    ),
  );
}

export function useIgnoreColumn(importId: string) {
  return useImportAction(importId, (position: number) =>
    unwrap(
      api.POST("/api/imports/{import_id}/columns/{position}/ignore", {
        params: { path: { import_id: importId, position } },
      }),
    ),
  );
}

/** « Ignore all unmatched columns » : les colonnes Unmatched deviennent Ignored (elles restent affichées). */
export function useIgnoreAiNotRelevant(importId: string) {
  return useImportAction(importId, () =>
    unwrap(
      api.POST("/api/imports/{import_id}/ignore-ai-not-relevant", {
        params: { path: { import_id: importId } },
      }),
    ),
  );
}

export function useIgnoreUnmatched(importId: string) {
  return useImportAction(importId, () =>
    unwrap(
      api.POST("/api/imports/{import_id}/ignore-unmatched", { params: { path: { import_id: importId } } }),
    ),
  );
}

// --------------------------------------------------------------------------- //
// Export des partenaires vers un format de fichier (PARTNER) : profil, aperçu, fichier, journal
// --------------------------------------------------------------------------- //
export type ExportTarget = Schemas["ExportTargetOut"];
export type ExportProfile = Schemas["ExportProfileOut"];
export type ExportPreview = Schemas["ExportPreviewOut"];
export type ExportRun = Schemas["ExportRunOut"];

export function useExportTargets() {
  return useQuery({ queryKey: ["supplier", "export-targets"], queryFn: () => unwrap(api.GET("/api/exports/targets")) });
}

export function useExportProfile(system: string | undefined, product: string | undefined) {
  return useQuery({
    queryKey: ["supplier", "export-profile", system, product],
    enabled: !!system && !!product,
    queryFn: () =>
      unwrap(api.GET("/api/exports/{system}/{product}/profile", { params: { path: { system: system!, product: product! } } })),
  });
}

export function useSaveExportProfile(system: string, product: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      unwrap(api.PUT("/api/exports/{system}/{product}/profile", { params: { path: { system, product } }, body: { values } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["supplier", "export-preview"] }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "export-profile"] }),
  });
}

/** Aperçu : lecture du système source à la demande (rien n'est réservé) ; lancé explicitement. */
export function useExportPreview(system: string, product: string, systemId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["supplier", "export-preview", system, product, systemId],
    enabled: enabled && !!systemId,
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET("/api/exports/{system}/{product}/preview", {
          params: { path: { system, product }, query: { system_id: systemId! } },
        }),
      ),
  });
}

export function useExportRuns() {
  return useQuery({ queryKey: ["supplier", "export-runs"], queryFn: () => unwrap(api.GET("/api/exports/runs")) });
}

/** Génère le fichier et le propose au téléchargement (rien n'est envoyé au système cible). */
export function useGenerateExport(system: string, product: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (systemId: string) => {
      const res = await fetch(`${API_URL}/api/exports/${system}/${product}/files`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_id: systemId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `Export failed (${res.status})`);
      }
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "export.csv";
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = name;
      a.click();
      URL.revokeObjectURL(href);
      return name;
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["supplier", "export-runs"] });
      qc.invalidateQueries({ queryKey: ["supplier", "export-preview"] });
    },
  });
}

// --------------------------------------------------------------------------- //
// Lecture comptable (ACCOUNTING, phase 3) : pièces d'UNE société pour UN mois, lecture seule
// --------------------------------------------------------------------------- //
export type AccountingRead = Schemas["AccountingReadOut"];
export type AccountingDocument = Schemas["AccountingDocumentOut"];

export function useAccountingCompanies(systemId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["supplier", "accounting-companies", systemId],
    enabled,
    queryFn: () =>
      unwrap(api.GET("/api/systems/{system_id}/accounting/companies", { params: { path: { system_id: systemId } } })),
  });
}

/** Lecture explicite (bouton « Read accounting ») : jamais déclenchée à l'ouverture de la page. */
export function useReadAccounting(systemId: string) {
  return useMutation({
    mutationFn: ({ companyId, period }: { companyId: string; period: string }) =>
      unwrap(
        api.GET("/api/systems/{system_id}/accounting/documents", {
          params: { path: { system_id: systemId }, query: { company_id: companyId, period } },
        }),
      ),
  });
}

// --------------------------------------------------------------------------- //
// Préparation d'un format cible (ACCOUNTING, phase 4) : réglages, correspondances, aperçu — aucun fichier
// --------------------------------------------------------------------------- //
export type AccountingTarget = Schemas["AccountingTargetOut"];
export type AccountingTargetSettings = Schemas["AccountingTargetSettingsOut"];
export type AccountingPreparation = Schemas["AccountingPreparationOut"];
export type PreparedDocument = Schemas["PreparedDocumentOut"];
export type PreparedBooking = Schemas["BookingOut"];
export type AccountingMapping = Schemas["MappingOut"];

type TargetPath = { systemId: string; system: string; product: string };
const targetParams = (t: TargetPath) => ({
  path: { system_id: t.systemId, target_system: t.system, target_product: t.product },
});

export function useAccountingTargets(enabled: boolean) {
  return useQuery({
    queryKey: ["supplier", "accounting-targets"],
    enabled,
    queryFn: () => unwrap(api.GET("/api/accounting/targets")),
  });
}

export function useAccountingTargetSettings(t: TargetPath | null) {
  return useQuery({
    queryKey: ["supplier", "accounting-target-settings", t?.systemId, t?.system, t?.product],
    enabled: !!t,
    queryFn: () =>
      unwrap(
        api.GET("/api/systems/{system_id}/accounting/targets/{target_system}/{target_product}/settings", {
          params: targetParams(t!),
        }),
      ),
  });
}

export function useSaveAccountingTargetSettings(t: TargetPath) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      unwrap(
        api.PUT("/api/systems/{system_id}/accounting/targets/{target_system}/{target_product}/settings", {
          params: targetParams(t),
          body: { values },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

/** Aperçu de la préparation (lecture seule, aucun fichier) : relu après chaque décision de correspondance. */
export function useAccountingPreparation(
  t: TargetPath | null,
  query: { companyId: string; period: string; version: string } | null,
) {
  return useQuery({
    queryKey: ["supplier", "accounting-preparation", t?.systemId, t?.system, t?.product, query],
    enabled: !!t && !!query,
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET("/api/systems/{system_id}/accounting/targets/{target_system}/{target_product}/preparation", {
          params: {
            ...targetParams(t!),
            query: { company_id: query!.companyId, period: query!.period, version: query!.version },
          },
        }),
      ),
  });
}

export type MappingDecision = {
  kind: "account" | "tax" | "category";
  ref: string;
  value?: string | null;
  noValue?: boolean;
  acceptSuggestion?: boolean;
  sourceCode?: string;
  sourceName?: string | null;
};

/** Décision explicite sur une correspondance (jamais appliquée automatiquement) ; `clear` la retire. */
export function useDecideMapping(t: TargetPath) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (d: MappingDecision & { clear?: boolean }) => {
      const params = { path: { ...targetParams(t).path, kind: d.kind, ref: d.ref } };
      const url = "/api/systems/{system_id}/accounting/targets/{target_system}/{target_product}/mappings/{kind}/{ref}";
      if (d.clear) return unwrap(api.DELETE(url, { params }));
      return unwrap(
        api.PUT(url, {
          params,
          body: {
            value: d.value ?? null,
            no_value: d.noValue ?? false,
            accept_suggestion: d.acceptSuggestion ?? false,
            source_code: d.sourceCode ?? null,
            source_name: d.sourceName ?? null,
          },
        }),
      );
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "accounting-preparation"] }),
  });
}

// --------------------------------------------------------------------------- //
// Fichier du format cible (ACCOUNTING, phase 5) : aperçu exact, téléchargement local, fichiers générés
// --------------------------------------------------------------------------- //
export type AccountingFilePreview = Schemas["AccountingFilePreviewOut"];
export type AccountingExportRun = Schemas["AccountingExportRunOut"];
type FileQuery = { companyId: string; period: string; version: string; includeExported?: boolean };

/** Aperçu explicite du fichier (bouton « Preview DATEV file ») : contenu exact, rien n'est enregistré. */
export function useAccountingFilePreview(t: TargetPath) {
  return useMutation({
    mutationFn: (q: FileQuery) =>
      unwrap(
        api.GET("/api/systems/{system_id}/accounting/targets/{target_system}/{target_product}/file-preview", {
          params: {
            ...targetParams(t),
            query: {
              company_id: q.companyId,
              period: q.period,
              version: q.version,
              include_exported: q.includeExported ?? false,
            },
          },
        }),
      ),
  });
}

export function useAccountingFiles(t: TargetPath | null) {
  return useQuery({
    queryKey: ["supplier", "accounting-files", t?.systemId, t?.system, t?.product],
    enabled: !!t,
    queryFn: () =>
      unwrap(
        api.GET("/api/systems/{system_id}/accounting/targets/{target_system}/{target_product}/files", {
          params: targetParams(t!),
        }),
      ),
  });
}

/** Génère le fichier APERÇU (`contentToken`) et le propose au téléchargement. Refusé si le contenu a changé
 *  depuis l'aperçu. Rien n'est envoyé au système cible, le système source n'est pas modifié. */
export function useDownloadAccountingFile(t: TargetPath) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (q: FileQuery & { contentToken: string; confirmDuplicates?: boolean }) => {
      const url = `${API_URL}/api/systems/${t.systemId}/accounting/targets/${t.system}/${t.product}/files`;
      const res = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company_id: q.companyId,
          period: q.period,
          version: q.version,
          content_token: q.contentToken,
          include_exported: q.includeExported ?? false,
          confirm_duplicates: q.confirmDuplicates ?? false,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `Export failed (${res.status})`);
      }
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "export.csv";
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = name;
      a.click();
      URL.revokeObjectURL(href);
      return { name, size: blob.size };
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["supplier", "accounting-files"] });
      qc.invalidateQueries({ queryKey: ["supplier", "export-runs"] });
    },
  });
}

// --------------------------------------------------------------------------- //
// Historique d'export par pièce (idempotence) : autoriser une pièce modifiée, adopter un ancien fichier
// --------------------------------------------------------------------------- //
export type ExportDocumentState = Schemas["ExportDocumentOut"];

/** Décision explicite sur une pièce MODIFIÉE depuis son export : autoriser un nouvel export, ou retirer
 *  l'autorisation. L'historique n'est jamais modifié. */
export function useReleaseDocument(t: TargetPath) {
  return useMutation({
    mutationFn: (d: { ref: string; companyId: string; period: string; version: string; revoke?: boolean }) => {
      const url = "/api/systems/{system_id}/accounting/targets/{target_system}/{target_product}/documents/{ref}/release";
      const path = { ...targetParams(t).path, ref: d.ref };
      if (d.revoke)
        return unwrap(
          api.DELETE(url, {
            params: { path, query: { company_id: d.companyId, period: d.period, version: d.version } },
          }),
        );
      return unwrap(
        api.POST(url, { params: { path }, body: { company_id: d.companyId, period: d.period, version: d.version } }),
      );
    },
  });
}

/** Adopte comme « déjà exportées » les pièces d'un fichier généré avant le suivi (refusé si l'identité ne peut
 *  pas être prouvée). */
export function useAdoptLegacyFile(t: TargetPath) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fileId: string) =>
      unwrap(
        api.POST("/api/systems/{system_id}/accounting/targets/{target_system}/{target_product}/files/{file_id}/adopt", {
          params: { path: { ...targetParams(t).path, file_id: fileId } },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "accounting-files"] }),
  });
}

// --------------------------------------------------------------------------- //
// Préparer les clients / fournisseurs pour un système cible relié par son API (aperçu, aucune écriture)
// --------------------------------------------------------------------------- //
export type PartnerTarget = Schemas["PartnerTargetOut"];
export type PartnerTargetPreview = Schemas["PartnerTargetPreviewOut"];
export type PreparedPartner = Schemas["PreparedPartnerOut"];

/** Systèmes connectés qui peuvent recevoir les clients et fournisseurs de ce système. */
export function usePartnerTargets(systemId: string, enabled = true) {
  return useQuery({
    queryKey: ["supplier", "partner-targets", systemId],
    enabled,
    queryFn: () =>
      unwrap(api.GET("/api/systems/{system_id}/partners/targets", { params: { path: { system_id: systemId } } })),
  });
}

/** Aperçu de ce qui serait envoyé au système cible (lecture seule : rien n'est envoyé ni enregistré). */
export function usePartnerTargetPreview(systemId: string, targetId: string) {
  return useQuery({
    queryKey: ["supplier", "partner-target-preview", systemId, targetId],
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET("/api/systems/{system_id}/partners/targets/{target_id}/preview", {
          params: { path: { system_id: systemId, target_id: targetId } },
        }),
      ),
  });
}

// --------------------------------------------------------------------------- //
// Envoi (phase 1b) : sélection + confirmation explicites ; aucun nom de système ici
// --------------------------------------------------------------------------- //
export type SendRun = Schemas["SendRunOut"];
export type SendItem = Schemas["SendItemOut"];
export type PartnerMatch = Schemas["MatchOut"];

const TARGET_PATH = "/api/systems/{system_id}/partners/targets/{target_id}" as const;

function targetKeys(systemId: string, targetId: string) {
  return { path: { system_id: systemId, target_id: targetId } };
}

/** Envoie les partenaires choisis. `request_key` est généré à l'ouverture de la confirmation : un double clic
 *  ou un nouvel essai réseau renvoie le MÊME envoi, jamais un second. */
export function useSendPartners(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { request_key: string; references: string[]; create_new: string[] }) =>
      unwrap(
        api.POST(`${TARGET_PATH}/sends`, { params: targetKeys(systemId, targetId), body: { ...body, confirm: true } }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

export function usePartnerSends(systemId: string, targetId: string) {
  return useQuery({
    queryKey: ["supplier", "partner-sends", systemId, targetId],
    queryFn: () => unwrap(api.GET(`${TARGET_PATH}/sends`, { params: targetKeys(systemId, targetId) })),
  });
}

export function usePartnerSend(systemId: string, targetId: string, runId: string | null) {
  return useQuery({
    queryKey: ["supplier", "partner-send", systemId, targetId, runId],
    enabled: !!runId,
    queryFn: () =>
      unwrap(
        api.GET(`${TARGET_PATH}/sends/{run_id}`, {
          params: { path: { system_id: systemId, target_id: targetId, run_id: runId ?? "" } },
        }),
      ),
  });
}

export function useResumePartnerSend(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      unwrap(
        api.POST(`${TARGET_PATH}/sends/{run_id}/resume`, {
          params: { path: { system_id: systemId, target_id: targetId, run_id: runId } },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

/** Fiches EXISTANTES du système cible qui pourraient être ce partenaire (lecture seule, à la demande). */
export function usePartnerMatches(systemId: string, targetId: string, reference: string | null) {
  return useQuery({
    queryKey: ["supplier", "partner-matches", systemId, targetId, reference],
    enabled: !!reference,
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET(`${TARGET_PATH}/matches`, {
          params: { path: { system_id: systemId, target_id: targetId }, query: { reference: reference ?? "" } },
        }),
      ),
  });
}

export function useLinkPartner(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reference: string; target_record_id: string }) =>
      unwrap(api.POST(`${TARGET_PATH}/links`, { params: targetKeys(systemId, targetId), body })),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

export function useUnlinkPartner(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reference: string) =>
      unwrap(
        api.DELETE(`${TARGET_PATH}/links`, {
          params: { path: { system_id: systemId, target_id: targetId }, query: { reference } },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

export function useResolveUnknown(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { runId: string; itemId: string; created: boolean; target_record_id?: string | null }) =>
      unwrap(
        api.POST(`${TARGET_PATH}/sends/{run_id}/items/{item_id}/resolve`, {
          params: { path: { system_id: systemId, target_id: targetId, run_id: v.runId, item_id: v.itemId } },
          body: { created: v.created, target_record_id: v.target_record_id ?? null },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

// --------------------------------------------------------------------------- //
// Produits → système cible relié par son API (articles 1a) : aperçu et décisions explicites, aucun envoi
// --------------------------------------------------------------------------- //
export type ProductTarget = Schemas["ProductTargetOut"];
export type ProductTargetPreview = Schemas["ProductTargetPreviewOut"];
export type PreparedProduct = Schemas["PreparedProductOut"];

const PRODUCT_TARGET_PATH = "/api/systems/{system_id}/products/targets/{target_id}" as const;

/** Systèmes connectés qui peuvent recevoir les produits de ce système (aucun nom de système ici). */
export function useProductTargets(systemId: string, enabled = true) {
  return useQuery({
    queryKey: ["supplier", "product-targets", systemId],
    enabled,
    queryFn: () =>
      unwrap(api.GET("/api/systems/{system_id}/products/targets", { params: { path: { system_id: systemId } } })),
  });
}

/** Aperçu de ce que chaque produit deviendrait dans le système cible (lecture seule). */
export function useProductTargetPreview(systemId: string, targetId: string) {
  return useQuery({
    queryKey: ["supplier", "product-target-preview", systemId, targetId],
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET(`${PRODUCT_TARGET_PATH}/preview`, {
          params: { path: { system_id: systemId, target_id: targetId } },
        }),
      ),
  });
}

/** Base des prix de la source (nets ou bruts) : décision explicite, jamais une valeur par défaut. */
export function useSetPriceBasis(systemId: string, targetId: string) {
  const qc = useQueryClient();
  const path = { system_id: systemId, target_id: targetId };
  return useMutation({
    mutationFn: (basis: "net" | "gross" | null) =>
      basis === null
        ? unwrap(api.DELETE(`${PRODUCT_TARGET_PATH}/price-basis`, { params: { path } }))
        : unwrap(api.PUT(`${PRODUCT_TARGET_PATH}/price-basis`, { params: { path }, body: { price_basis: basis } })),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "product-target-preview", systemId, targetId] }),
  });
}

/** Taux choisi pour une taxe de vente de la source (`rate` null : retirer le choix). */
export function useSetTaxRate(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { tax: string; label: string; rate: string | null }) => {
      const path = { system_id: systemId, target_id: targetId, tax_reference: v.tax };
      return v.rate === null
        ? unwrap(api.DELETE(`${PRODUCT_TARGET_PATH}/tax-rates/{tax_reference}`, { params: { path } }))
        : unwrap(
            api.PUT(`${PRODUCT_TARGET_PATH}/tax-rates/{tax_reference}`, {
              params: { path },
              body: { rate: v.rate, label: v.label },
            }),
          );
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "product-target-preview", systemId, targetId] }),
  });
}

// --------------------------------------------------------------------------- //
// Envoi des produits (articles 1b) : mêmes réponses que l'envoi des partenaires ; sélection + confirmation
// explicites ; aucun nom de système ici
// --------------------------------------------------------------------------- //
/** Envoie les produits choisis (`request_key` généré à l'ouverture de la confirmation : jamais deux envois). */
export function useSendProducts(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { request_key: string; references: string[]; create_new: string[] }) =>
      unwrap(
        api.POST(`${PRODUCT_TARGET_PATH}/sends`, {
          params: targetKeys(systemId, targetId),
          body: { ...body, confirm: true },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

export function useProductSends(systemId: string, targetId: string) {
  return useQuery({
    queryKey: ["supplier", "product-sends", systemId, targetId],
    queryFn: () => unwrap(api.GET(`${PRODUCT_TARGET_PATH}/sends`, { params: targetKeys(systemId, targetId) })),
  });
}

export function useProductSend(systemId: string, targetId: string, runId: string | null) {
  return useQuery({
    queryKey: ["supplier", "product-send", systemId, targetId, runId],
    enabled: !!runId,
    queryFn: () =>
      unwrap(
        api.GET(`${PRODUCT_TARGET_PATH}/sends/{run_id}`, {
          params: { path: { system_id: systemId, target_id: targetId, run_id: runId ?? "" } },
        }),
      ),
  });
}

export function useResumeProductSend(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      unwrap(
        api.POST(`${PRODUCT_TARGET_PATH}/sends/{run_id}/resume`, {
          params: { path: { system_id: systemId, target_id: targetId, run_id: runId } },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

/** Articles EXISTANTS du système cible qui pourraient être ce produit (même numéro d'article, lecture seule). */
export function useProductMatches(systemId: string, targetId: string, reference: string | null) {
  return useQuery({
    queryKey: ["supplier", "product-matches", systemId, targetId, reference],
    enabled: !!reference,
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET(`${PRODUCT_TARGET_PATH}/matches`, {
          params: { path: { system_id: systemId, target_id: targetId }, query: { reference: reference ?? "" } },
        }),
      ),
  });
}

export function useLinkProduct(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reference: string; target_record_id: string }) =>
      unwrap(api.POST(`${PRODUCT_TARGET_PATH}/links`, { params: targetKeys(systemId, targetId), body })),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

export function useUnlinkProduct(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reference: string) =>
      unwrap(
        api.DELETE(`${PRODUCT_TARGET_PATH}/links`, {
          params: { path: { system_id: systemId, target_id: targetId }, query: { reference } },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}

export function useResolveProductUnknown(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { runId: string; itemId: string; created: boolean; target_record_id?: string | null }) =>
      unwrap(
        api.POST(`${PRODUCT_TARGET_PATH}/sends/{run_id}/items/{item_id}/resolve`, {
          params: { path: { system_id: systemId, target_id: targetId, run_id: v.runId, item_id: v.itemId } },
          body: { created: v.created, target_record_id: v.target_record_id ?? null },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier"] }),
  });
}
