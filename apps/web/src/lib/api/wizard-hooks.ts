"use client";

/** Hooks de l'AI Migration Wizard (Supplier API `/api/wizard/*`). L'IA n'est appelée que par le serveur ; le
 *  fichier du client n'est jamais stocké : il est renvoyé pour chaque étape (correspondances, aperçu, fichier). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_URL, type Schemas, unwrap } from "./client";

export type WizardStep = Schemas["WizardStepOut"];
export type WizardQuestion = Schemas["WizardQuestionOut"];
export type WizardAnswer = Schemas["WizardAnswerIn"];
export type TargetProposal = Schemas["TargetProposalOut"];
export type FileMapping = Schemas["FileMappingOut"];
export type ColumnMapping = Schemas["ColumnMappingOut"];
export type PreparedFile = Schemas["PreparedFileOut"];
export type ValueQuestion = Schemas["ValueQuestionOut"];
export type MappingDecision = { position: number; target: string | null }; // partie JSON du formulaire
export type WizardMode = "simple" | "expert";
export type MissingInfo = Schemas["MissingInfoOut"];
export type MissingQuestion = Schemas["MissingQuestionOut"];
export type WizardTemplate = Schemas["WizardTemplateOut"];
export type QuickTargetChoice = Schemas["QuickTargetChoiceOut"];
export type QuickStart = Schemas["QuickStartOut"];
export type QuickQuestion = Schemas["QuickQuestionOut"];
export type Fill = {
  field: string;
  common: string | null;
  per_row: Record<number, string>;
  confirmed: boolean;
  hint?: string; // réponse métier sans valeur écrite (signalée)
  unknown?: boolean; // « I don't know » (signalé)
  auto?: boolean; // appliquée d'office d'après les réponses du parcours
};

export function useWizardNext() {
  return useMutation({
    mutationFn: ({ answers, mode }: { answers: WizardAnswer[]; mode: WizardMode }) =>
      unwrap(api.POST("/api/wizard/next", { body: { answers, mode } })),
  });
}

export function useWizardProposal() {
  return useMutation({
    mutationFn: (answers: WizardAnswer[]) =>
      unwrap(api.POST("/api/wizard/proposal", { body: { answers, mode: "expert" } })),
  });
}

/** Structure renvoyée au serveur : les statuts « vérifié » y sont toujours recalculés (Registry). */
function proposalBody(proposal: TargetProposal) {
  return {
    title: proposal.title,
    summary: proposal.summary,
    entity: proposal.entity,
    template_name: proposal.template_name,
    configuration: proposal.configuration,
    fields: proposal.fields.map((f) => ({
      name: f.name,
      label: f.label,
      type: f.type,
      length: f.length,
      required: f.required,
      example: f.example,
      description: f.description,
    })),
    rows: proposal.rows,
    notes: proposal.notes,
  };
}

/** Échec d'une étape ; `dataTypes` : le type de données n'a pas été reconnu, l'utilisateur le choisit. */
export class WizardFailure extends Error {
  constructor(
    message: string,
    readonly dataTypes: string[] = [],
  ) {
    super(message);
  }
}

async function failure(res: Response, fallback: string): Promise<Error> {
  const body = await res.json().catch(() => ({}));
  const detail = body.detail;
  if (detail && typeof detail === "object" && detail.code === "data_type_unknown") {
    return new WizardFailure(String(detail.message ?? fallback), Array.isArray(detail.data_types) ? detail.data_types : []);
  }
  return new WizardFailure(typeof detail === "string" ? detail : `${fallback} (${res.status})`);
}

function download(blob: Blob, name: string) {
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  a.click();
  URL.revokeObjectURL(href);
}

function fileName(res: Response, fallback: string): string {
  return /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallback;
}

/** Excel d'exemple : statuts « vérifié » recalculés par le serveur (Registry), puis téléchargement. */
export function useWizardExcel() {
  return useMutation({
    mutationFn: async ({ answers, proposal }: { answers: WizardAnswer[]; proposal: TargetProposal }) => {
      const res = await fetch(`${API_URL}/api/wizard/excel`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers, proposal: proposalBody(proposal) }),
      });
      if (!res.ok) throw await failure(res, "Excel failed");
      const name = fileName(res, "sample.xlsx");
      download(await res.blob(), name);
      return name;
    },
  });
}

type FileInput = {
  file: File;
  answers: WizardAnswer[];
  proposal: TargetProposal;
  decisions?: MappingDecision[];
  fills?: Fill[];
  mode?: WizardMode;
  previous?: { header: string; target: string | null }[]; // choix retenus pour le même fichier
  choices?: Record<string, string>; // décisions sur les valeurs : question → option
  confirmed?: number[]; // colonnes confirmées ou corrigées par l'utilisateur (mémoire de l'organisation)
};

function form({ file, answers, proposal, decisions, fills, mode, previous, choices, confirmed }: FileInput): FormData {
  const data = new FormData();
  data.append("file", file);
  data.append(
    "payload",
    JSON.stringify({
      answers,
      proposal: proposalBody(proposal),
      decisions: decisions ?? [],
      fills: fills ?? [],
      mode: mode ?? "simple",
      previous: previous ?? [],
      choices: choices ?? {},
      confirmed: confirmed ?? [],
    }),
  );
  return data;
}

async function post(path: string, input: FileInput): Promise<Response> {
  const res = await fetch(`${API_URL}/api/wizard/file/${path}`, {
    method: "POST",
    credentials: "include",
    body: form(input),
  });
  if (!res.ok) throw await failure(res, "The file could not be processed");
  return res;
}

/** Colonnes du fichier → champs de la structure (nom exact, puis IA ; incertaines à confirmer). */
export function useWizardMapping() {
  return useMutation({
    mutationFn: async (input: FileInput) => (await (await post("mapping", input)).json()) as FileMapping,
  });
}

/** Aperçu du fichier préparé (valeurs du client copiées telles quelles). */
export function useWizardPreview() {
  return useMutation({
    mutationFn: async (input: FileInput) => (await (await post("preview", input)).json()) as PreparedFile,
  });
}

/** Fichier préparé pour la cible : téléchargement seulement, jamais envoyé à un système. */
export function useWizardPrepared() {
  return useMutation({
    mutationFn: async (input: FileInput) => {
      const res = await post("prepared", input);
      const name = fileName(res, "prepared.xlsx");
      download(await res.blob(), name);
      return name;
    },
  });
}

/** Cibles du démarrage par le fichier : vos systèmes, puis les versions du Registry, puis les systèmes connus. */
export function useWizardTargets() {
  return useQuery({ queryKey: ["supplier", "wizard-targets"], queryFn: () => unwrap(api.GET("/api/wizard/targets")) });
}

/** Démarrage par le fichier (mode Simple) : type de données, structure, correspondances et au plus 3 questions
 * présélectionnées, en un appel. */
export function useWizardQuickStart() {
  return useMutation({
    mutationFn: async ({ file, target, dataType }: { file: File; target: string; dataType?: string }) => {
      const data = new FormData();
      data.append("file", file);
      data.append("payload", JSON.stringify({ target, mode: "simple", ...(dataType ? { data_type: dataType } : {}) }));
      const res = await fetch(`${API_URL}/api/wizard/quick-start`, {
        method: "POST",
        credentials: "include",
        body: data,
      });
      if (!res.ok) throw await failure(res, "The file could not be analyzed");
      return (await res.json()) as QuickStart;
    },
  });
}

/** Format choisi dès le dépôt du fichier, parmi `targets` (valeurs de /targets) : la seule cible qui reçoit le
 * type reconnu par les règles du serveur (aucun LLM) ; `target` null : rien à présélectionner. */
export function useWizardDetect() {
  return useMutation({
    mutationFn: async ({ file, targets }: { file: File; targets: string[] }) => {
      const data = new FormData();
      data.append("file", file);
      data.append("payload", JSON.stringify({ targets }));
      const res = await fetch(`${API_URL}/api/wizard/detect-target`, {
        method: "POST",
        credentials: "include",
        body: data,
      });
      if (!res.ok) throw await failure(res, "The file could not be read");
      return (await res.json()) as Schemas["WizardDetectOut"];
    },
  });
}

/** Questions PRÉVISIBLES avant le fichier (valeurs admises, unités, catégories…) ; avec `fills` : réponses validées
 * par le serveur (422 si une valeur sort des contraintes du Registry). */
export function useWizardEarly() {
  return useMutation({
    mutationFn: ({
      answers,
      proposal,
      mode,
      fills = [],
    }: {
      answers: WizardAnswer[];
      proposal: TargetProposal;
      mode: WizardMode;
      fills?: Fill[];
    }) =>
      unwrap(
        api.POST("/api/wizard/early", {
          body: {
            answers,
            proposal: proposalBody(proposal),
            mode,
            fills: fills.map((f) => ({ ...f, auto: f.auto ?? false, unknown: f.unknown ?? false })),
          },
        }),
      ),
  });
}

/** Questions pour compléter les champs obligatoires sans valeur (règles et Registry d'abord, puis IA). */
export function useWizardMissing() {
  return useMutation({
    mutationFn: async (input: FileInput) => (await (await post("missing", input)).json()) as MissingInfo,
  });
}

// --------------------------------------------------------------------------- //
// Modèles de questionnaire de l'utilisateur : questions et réponses seulement (jamais de fichier ni de donnée)
// --------------------------------------------------------------------------- //
const TEMPLATES = ["supplier", "wizard-templates"] as const;

export function useWizardTemplates() {
  return useQuery({ queryKey: TEMPLATES, queryFn: () => unwrap(api.GET("/api/wizard/templates")) });
}

export function useSaveWizardTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, steps }: { name: string; steps: { question: WizardQuestion; answer: WizardAnswer }[] }) =>
      unwrap(api.POST("/api/wizard/templates", { body: { name, steps } })),
    onSettled: () => qc.invalidateQueries({ queryKey: TEMPLATES }),
  });
}

export function useRenameWizardTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      unwrap(
        api.PATCH("/api/wizard/templates/{template_id}", { params: { path: { template_id: id } }, body: { name } }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: TEMPLATES }),
  });
}

export function useDeleteWizardTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await api.DELETE("/api/wizard/templates/{template_id}", {
        params: { path: { template_id: id } },
      });
      if (error || !response.ok) throw new Error(`Delete failed (${response.status})`);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: TEMPLATES }),
  });
}
