"use client";

/** Pièces comptables → système cible relié par son API (phase 2c) : APERÇU seulement, aucun envoi ici.
 *  Hooks de la Supplier API ; aucun nom de système. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "./client";

export type AccountingApiTarget = Schemas["AccountingApiTargetOut"];
export type AccountingApiPreview = Schemas["AccountingApiPreviewOut"];
export type AccountingApiDocument = Schemas["ApiDocumentOut"];
export type AccountingApiAccount = Schemas["AccountNeedOut"];
export type TargetChoice = Schemas["TargetChoiceOut"];

const TARGET = "/api/systems/{system_id}/accounting/api-targets/{target_id}" as const;

export function useAccountingApiTargets(systemId: string, enabled = true) {
  return useQuery({
    queryKey: ["supplier", "accounting-api-targets", systemId],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/systems/{system_id}/accounting/api-targets", { params: { path: { system_id: systemId } } }),
      ),
  });
}

/** Valeurs de la cible pour les comptes (lues dans le système cible, à la demande). */
export function useAccountingApiOptions(systemId: string, targetId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["supplier", "accounting-api-options", systemId, targetId],
    enabled,
    staleTime: 5 * 60 * 1000,
    queryFn: () =>
      unwrap(api.GET(`${TARGET}/options`, { params: { path: { system_id: systemId, target_id: targetId } } })),
  });
}

/** Aperçu d'une société et d'un mois : lecture seule, déclenché explicitement. */
export function useAccountingApiPreview(
  systemId: string,
  targetId: string,
  query: { company_id: string; period: string } | null,
) {
  return useQuery({
    queryKey: ["supplier", "accounting-api-preview", systemId, targetId, query?.company_id, query?.period],
    enabled: !!query,
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET(`${TARGET}/preview`, {
          params: {
            path: { system_id: systemId, target_id: targetId },
            query: query ?? { company_id: "", period: "" },
          },
        }),
      ),
  });
}

/** Valeur de la cible choisie pour un compte (enregistrée dans UIE, jamais dans la cible). */
export function useChooseAccountValue(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { account: string; choice_id: string; code?: string | null; name?: string | null }) =>
      unwrap(
        api.PUT(`${TARGET}/accounts/{account_reference}`, {
          params: { path: { system_id: systemId, target_id: targetId, account_reference: v.account } },
          body: { choice_id: v.choice_id, code: v.code ?? null, name: v.name ?? null },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "accounting-api-preview", systemId, targetId] }),
  });
}

// --------------------------------------------------------------------------- //
// Envoi (phase 2c, étape 2) : sélection + confirmation explicites ; jamais rejoué pour une même clé
// --------------------------------------------------------------------------- //
export type AccountingApiSendRun = Schemas["ApiSendRunOut"];
export type AccountingApiSendItem = Schemas["ApiSendItemOut"];

function refresh(qc: ReturnType<typeof useQueryClient>, systemId: string, targetId: string) {
  // après un envoi ou une décision : l'aperçu (états d'envoi) et l'historique sont relus
  qc.invalidateQueries({ queryKey: ["supplier", "accounting-api-preview", systemId, targetId] });
  qc.invalidateQueries({ queryKey: ["supplier", "accounting-api-sends", systemId, targetId] });
}

/** Envoie les pièces choisies. `request_key` est généré à l'ouverture de la confirmation : un double clic ou
 *  un nouvel essai réseau renvoie le MÊME envoi, jamais un second. Jamais de nouvel essai automatique. */
export function useSendAccountingDocuments(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: (body: { request_key: string; company_id: string; period: string; references: string[] }) =>
      unwrap(
        api.POST(`${TARGET}/sends`, {
          params: { path: { system_id: systemId, target_id: targetId } },
          body: { ...body, confirm: true },
        }),
      ),
    onSettled: () => refresh(qc, systemId, targetId),
  });
}

export function useAccountingApiSends(systemId: string, targetId: string) {
  return useQuery({
    queryKey: ["supplier", "accounting-api-sends", systemId, targetId],
    queryFn: () =>
      unwrap(api.GET(`${TARGET}/sends`, { params: { path: { system_id: systemId, target_id: targetId } } })),
  });
}

export function useAccountingApiSend(systemId: string, targetId: string, runId: string | null) {
  return useQuery({
    queryKey: ["supplier", "accounting-api-sends", systemId, targetId, runId],
    enabled: !!runId,
    queryFn: () =>
      unwrap(
        api.GET(`${TARGET}/sends/{run_id}`, {
          params: { path: { system_id: systemId, target_id: targetId, run_id: runId ?? "" } },
        }),
      ),
  });
}

/** Issue inconnue vérifiée par l'utilisateur dans la cible : « créée » ou « pas créée » (lecture seule). */
export function useResolveAccountingUnknown(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: (v: { runId: string; itemId: string; company_id: string; period: string; created: boolean }) =>
      unwrap(
        api.POST(`${TARGET}/sends/{run_id}/items/{item_id}/resolve`, {
          params: { path: { system_id: systemId, target_id: targetId, run_id: v.runId, item_id: v.itemId } },
          body: { company_id: v.company_id, period: v.period, created: v.created },
        }),
      ),
    onSettled: () => refresh(qc, systemId, targetId),
  });
}

export function useClearAccountValue(systemId: string, targetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (account: string) =>
      unwrap(
        api.DELETE(`${TARGET}/accounts/{account_reference}`, {
          params: { path: { system_id: systemId, target_id: targetId, account_reference: account } },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ["supplier", "accounting-api-preview", systemId, targetId] }),
  });
}
