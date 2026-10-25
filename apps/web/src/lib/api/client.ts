/**
 * Client HTTP typé de l'API Admin : les types viennent de `schema.d.ts`, généré depuis
 * `openapi.json` (`pnpm gen:api`). Aucune règle métier ici : on transporte ce que renvoie l'API.
 */
import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type SystemNode = Schemas["SystemNode"];
export type SnapshotSummary = Schemas["SnapshotSummary"];
export type SnapshotDetail = Schemas["SnapshotDetail"];
export type EntitySummary = Schemas["EntitySummary"];
export type EntityPage = Schemas["EntityPage"];
export type EntityDetail = Schemas["EntityDetail"];
export type FieldOut = Schemas["FieldOut"];
export type FieldHit = Schemas["FieldHit"];
export type RelationOut = Schemas["RelationOut"];
export type CompareOut = Schemas["CompareOut"];
export type ChangeOut = Schemas["ChangeOut"];
export type InstanceOut = Schemas["InstanceOut"];
export type InstanceDetail = Schemas["InstanceDetail"];
export type InstanceSnapshotOut = Schemas["InstanceSnapshotOut"];
export type CapabilityOut = Schemas["CapabilityOut"];
export type SchemaDiffOut = Schemas["SchemaDiffOut"];
export type InstanceChangeOut = Schemas["InstanceChangeOut"];
export type InstanceDiffSummary = Schemas["InstanceDiffSummary"];
export type DiffItem = Schemas["DiffItem"];
export type DomainSnapshotOut = Schemas["DomainSnapshotOut"];
export type DomainSnapshotHistoryOut = Schemas["DomainSnapshotHistoryOut"];
export type DomainEntityDef = Schemas["DomainEntityDef"];
export type DomainFieldDef = Schemas["DomainFieldDef"];
export type DomainMissingElement = Schemas["DomainMissingElement"];
export type BindingView = Schemas["BindingView"];
export type AiStatus = Schemas["AiStatus"];
export type AiEnrichmentResult = Schemas["AiEnrichmentResult"];
export type CatalogAnalysisSummary = Schemas["CatalogAnalysisSummary"];
export type VocabularyOut = Schemas["VocabularyOut"];

export type MeOut = Schemas["MeOut"];
export type OrganizationOut = Schemas["OrganizationOut"];
export type OrganizationDetail = Schemas["OrganizationDetail"];
export type MemberOut = Schemas["MemberOut"];

// Par défaut, proxy de même origine (next.config.ts → API) : le cookie de session HttpOnly suit
// naturellement. NEXT_PUBLIC_UIE_API_URL (URL absolue) reste possible : CORS avec credentials côté API.
export const API_URL = process.env.NEXT_PUBLIC_UIE_API_URL ?? "/uie-api";

export const api = createClient<paths>({ baseUrl: API_URL, credentials: "include" });

// Session absente ou expirée (401) : la garde d'authentification redirige vers /login. La sécurité reste
// côté API ; ceci n'est que la navigation. /auth/login (mauvais mot de passe) et /auth/me (lu par la
// garde elle-même) ne déclenchent pas la redirection.
let onUnauthorized: (() => void) | undefined;
export function setUnauthorizedHandler(handler: (() => void) | undefined) {
  onUnauthorized = handler;
}
api.use({
  onResponse({ request, response }) {
    const path = new URL(request.url).pathname;
    if (response.status === 401 && !path.endsWith("/auth/login") && !path.endsWith("/auth/me")) {
      onUnauthorized?.();
    }
    return undefined;
  },
});

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown, // `detail` FastAPI brut (texte, ou liste d'erreurs de validation)
    readonly body?: unknown, // corps complet (ex. `errors` : un message par paramètre d'un profil)
  ) {
    super(message);
  }
}

/** Renvoie `data` ou lève une ApiError lisible (detail FastAPI). */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined || data === undefined) {
    const raw = typeof error === "object" && error !== null && "detail" in error ? error.detail : undefined;
    const detail = raw === undefined ? response.statusText : typeof raw === "string" ? raw : JSON.stringify(raw);
    throw new ApiError(response.status, `${response.status} ${detail}`, raw, error);
  }
  return data;
}
