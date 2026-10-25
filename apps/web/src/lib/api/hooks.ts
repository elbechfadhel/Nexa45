"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type Schemas, unwrap } from "./client";

// Les snapshots publiés sont immuables : leurs lectures peuvent rester en cache longtemps.
const IMMUTABLE = { staleTime: Infinity, gcTime: 30 * 60_000 } as const;

export function useSystems() {
  return useQuery({
    queryKey: ["systems"],
    queryFn: () => unwrap(api.GET("/admin/systems")),
  });
}

export function useSnapshot(id: string | undefined) {
  return useQuery({
    queryKey: ["snapshot", id],
    enabled: !!id,
    queryFn: () =>
      unwrap(api.GET("/admin/schema-snapshots/{snapshot_id}", { params: { path: { snapshot_id: id! } } })),
    ...IMMUTABLE,
  });
}

export function useEntities(id: string, search: string, limit: number, offset: number) {
  return useQuery({
    queryKey: ["entities", id, search, limit, offset],
    queryFn: () =>
      unwrap(
        api.GET("/admin/schema-snapshots/{snapshot_id}/entities", {
          params: { path: { snapshot_id: id }, query: { search: search || undefined, limit, offset } },
        }),
      ),
    placeholderData: keepPreviousData,
    ...IMMUTABLE,
  });
}

export function useEntity(id: string | undefined) {
  return useQuery({
    queryKey: ["entity", id],
    enabled: !!id,
    queryFn: () => unwrap(api.GET("/admin/entities/{entity_id}", { params: { path: { entity_id: id! } } })),
    ...IMMUTABLE,
  });
}

export function useFieldSearch(snapshotId: string, search: string) {
  return useQuery({
    queryKey: ["fields", snapshotId, search],
    enabled: search.length >= 2,
    queryFn: () =>
      unwrap(
        api.GET("/admin/schema-snapshots/{snapshot_id}/fields", {
          params: { path: { snapshot_id: snapshotId }, query: { search, limit: 50 } },
        }),
      ),
    ...IMMUTABLE,
  });
}

/** system (+ produit) + version → dernier snapshot publié (la résolution est faite par l'API). */
export function useResolve(system: string | undefined, version: string | undefined, product?: string) {
  return useQuery({
    queryKey: ["resolve", system, product, version],
    enabled: !!system && !!version,
    queryFn: () =>
      unwrap(
        api.GET("/admin/snapshots/resolve", {
          params: { query: { system: system!, version: version!, ...(product ? { product } : {}) } },
        }),
      ),
  });
}

export function useCompare(from: string | undefined, to: string | undefined, entity: string | undefined) {
  return useQuery({
    queryKey: ["compare", from, to, entity],
    enabled: !!from && !!to,
    queryFn: () =>
      unwrap(
        api.GET("/admin/schema-compare", {
          params: { query: { from: from!, to: to!, entity: entity || undefined } },
        }),
      ),
    ...IMMUTABLE,
  });
}

// --------------------------------------------------------------------------- //
// Instances client (état mutable : pas de cache infini, invalidation après action)
// --------------------------------------------------------------------------- //
export function useInstances() {
  return useQuery({ queryKey: ["instances"], queryFn: () => unwrap(api.GET("/admin/instances")) });
}

export function useInstance(id: string) {
  return useQuery({
    queryKey: ["instance", id],
    queryFn: () => unwrap(api.GET("/admin/instances/{instance_id}", { params: { path: { instance_id: id } } })),
  });
}

export function useInstanceDiff(id: string, sequence: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["instance-diff", id, sequence ?? "latest"],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/schema-diff", {
          params: { path: { instance_id: id }, query: { sequence } },
        }),
      ),
  });
}

function useInstanceAction<T>(id: string, run: () => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["instance", id] });
      qc.invalidateQueries({ queryKey: ["instances"] });
      qc.invalidateQueries({ queryKey: ["instance-diff", id] });
    },
  });
}

export function useTestConnection(id: string) {
  return useInstanceAction(id, () =>
    unwrap(api.POST("/admin/instances/{instance_id}/test-connection", { params: { path: { instance_id: id } } })),
  );
}

export function useDiscoverSchema(id: string) {
  return useInstanceAction(id, () =>
    unwrap(api.POST("/admin/instances/{instance_id}/discover-schema", { params: { path: { instance_id: id } } })),
  );
}

export type InstanceCreateBody = import("./client").Schemas["InstanceCreateIn"];

export function useCreateInstance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: InstanceCreateBody) => unwrap(api.POST("/admin/instances", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["instances"] }),
  });
}

export function useDeleteInstance(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.DELETE("/admin/instances/{instance_id}", { params: { path: { instance_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["instances"] }),
  });
}

// --------------------------------------------------------------------------- //
// Product Domain (V1a) : domain snapshots immutables ; seul « le dernier » change après Analyze
// --------------------------------------------------------------------------- //
type DomainFilters = { group?: Schemas["FieldGroup"]; origin?: Schemas["Origin"]; relevance?: Schemas["Relevance"] };

/** Domaine effectif : `snapshotId` précis (immutable) ou le dernier analysé (404 tant que jamais analysé). */
export function useProductDomain(id: string, snapshotId: string | undefined) {
  return useQuery({
    queryKey: ["product-domain", id, snapshotId ?? "latest"],
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/product-domain", {
          params: { path: { instance_id: id }, query: { snapshot_id: snapshotId } },
        }),
      ),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
    ...(snapshotId ? IMMUTABLE : {}),
  });
}

/** Champs filtrés par l'API (group / origin / relevance) pour un domain snapshot précis. */
export function useProductDomainFields(id: string, snapshotId: string | undefined, filters: DomainFilters) {
  return useQuery({
    queryKey: ["product-domain-fields", id, snapshotId, filters],
    enabled: !!snapshotId,
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/product-domain/fields", {
          params: { path: { instance_id: id }, query: { snapshot_id: snapshotId, ...filters } },
        }),
      ),
    placeholderData: keepPreviousData,
    ...IMMUTABLE,
  });
}

export function useProductDomainHistory(id: string) {
  return useQuery({
    queryKey: ["product-domain-history", id],
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/product-domain/snapshots", {
          params: { path: { instance_id: id } },
        }),
      ),
  });
}

export function useAnalyzeProductDomain(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/admin/instances/{instance_id}/product-domain/analyze", {
          params: { path: { instance_id: id } },
        }),
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["product-domain", id, "latest"] });
      qc.invalidateQueries({ queryKey: ["product-domain-history", id] });
      qc.invalidateQueries({ queryKey: ["catalog", id] }); // l'analyse enrichit (V1b)
    },
  });
}

// --------------------------------------------------------------------------- //
// Product Catalog Analysis (V1b) : état de décision persisté ; seul Analyze recalcule
// --------------------------------------------------------------------------- //
const notFound = (e: unknown) => e instanceof ApiError && e.status === 404;

export function useCatalogAnalysis(id: string) {
  return useQuery({
    queryKey: ["catalog", id, "analysis"],
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/product-domain/analysis", {
          params: { path: { instance_id: id } },
        }),
      ),
    retry: (n, e) => !notFound(e) && n < 2,
  });
}

export function useCatalogQuestions(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", id, "questions"],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/product-domain/questions", {
          params: { path: { instance_id: id } },
        }),
      ),
  });
}

export function useCatalogExclusions(id: string, extensionsOnly: boolean, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", id, "exclusions", extensionsOnly],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/product-domain/exclusions", {
          params: { path: { instance_id: id }, query: { extensions_only: extensionsOnly } },
        }),
      ),
  });
}

export function useCatalogDecisions(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", id, "decisions"],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/product-domain/decisions", {
          params: { path: { instance_id: id } },
        }),
      ),
  });
}

// V1c : statut du fournisseur LLM (aucun appel) et « Ask AI » (seul point qui appelle un LLM)
export function useAiStatus(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", id, "ai-status"],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/admin/instances/{instance_id}/product-domain/ai-status", {
          params: { path: { instance_id: id } },
        }),
      ),
  });
}

export function useEnrichAi(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/admin/instances/{instance_id}/product-domain/enrich-ai", {
          params: { path: { instance_id: id } },
          body: { fields: null, force: false, max_fields: 50 },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["catalog", id] }),
  });
}

export function useVocabulary() {
  return useQuery({
    queryKey: ["vocabulary", "product"],
    queryFn: () => unwrap(api.GET("/admin/vocabulary/product")),
    staleTime: 10 * 60_000,
  });
}

export type FieldRef = { entity: string; field: string };
export type FieldDecision =
  | ({ action: "confirm"; semantic_key?: string | null; note?: string | null } & FieldRef)
  | ({ action: "change"; semantic_key: string; note?: string | null } & FieldRef)
  | ({ action: "ignore" | "include" | "reopen"; note?: string | null } & FieldRef);

/** Réponse du supplier (validée par le Core : clé du vocabulaire ou « other »). */
export function useFieldDecision(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (d: FieldDecision) => {
      const params = { path: { instance_id: id, entity: d.entity, field: d.field } };
      switch (d.action) {
        case "confirm":
          return unwrap(
            api.POST("/admin/instances/{instance_id}/product-domain/fields/{entity}/{field}/confirm", {
              params,
              body: { semantic_key: d.semantic_key ?? null, note: d.note ?? null },
            }),
          );
        case "change":
          return unwrap(
            api.POST("/admin/instances/{instance_id}/product-domain/fields/{entity}/{field}/change", {
              params,
              body: { semantic_key: d.semantic_key, note: d.note ?? null },
            }),
          );
        case "ignore":
          return unwrap(
            api.POST("/admin/instances/{instance_id}/product-domain/fields/{entity}/{field}/ignore", {
              params,
              body: { note: d.note ?? null },
            }),
          );
        case "include":
          return unwrap(
            api.POST("/admin/instances/{instance_id}/product-domain/fields/{entity}/{field}/include", {
              params,
              body: { note: d.note ?? null },
            }),
          );
        case "reopen":
          return unwrap(
            api.POST("/admin/instances/{instance_id}/product-domain/fields/{entity}/{field}/reopen", {
              params,
              body: { note: d.note ?? null },
            }),
          );
      }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["catalog", id] }),
  });
}

// --------------------------------------------------------------------------- //
// Suppliers = organisations (V1d, administration plateforme)
// --------------------------------------------------------------------------- //
export function useOrganizations() {
  return useQuery({ queryKey: ["organizations"], queryFn: () => unwrap(api.GET("/admin/organizations")) });
}

export function useOrganization(id: string) {
  return useQuery({
    queryKey: ["organization", id],
    queryFn: () =>
      unwrap(api.GET("/admin/organizations/{organization_id}", { params: { path: { organization_id: id } } })),
  });
}

export function useCreateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["OrganizationCreateIn"]) => unwrap(api.POST("/admin/organizations", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["organizations"] }),
  });
}

/** Après une action sur une organisation : son détail, la liste des suppliers et celle des instances. */
function useOrganizationAction<B, T>(id: string, run: (body: B) => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["organization", id] });
      qc.invalidateQueries({ queryKey: ["organizations"] });
      qc.invalidateQueries({ queryKey: ["instances"] });
    },
  });
}

export function useAddOrganizationUser(id: string) {
  return useOrganizationAction(id, (body: Schemas["MemberCreateIn"]) =>
    unwrap(api.POST("/admin/organizations/{organization_id}/users", { params: { path: { organization_id: id } }, body })),
  );
}

export function useCreateOrganizationInstance(id: string) {
  return useOrganizationAction(id, (body: Schemas["OrganizationInstanceIn"]) =>
    unwrap(
      api.POST("/admin/organizations/{organization_id}/instances", { params: { path: { organization_id: id } }, body }),
    ),
  );
}

export function useAttachInstance(id: string) {
  return useOrganizationAction(id, (instanceId: string) =>
    unwrap(
      api.POST("/admin/organizations/{organization_id}/instances/{instance_id}/attach", {
        params: { path: { organization_id: id, instance_id: instanceId } },
      }),
    ),
  );
}
