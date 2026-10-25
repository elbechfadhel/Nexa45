"use client";

/** Session de la console (V1d) : utilisateur courant, connexion, déconnexion, organisation courante.
 *  Le jeton n'est jamais visible ici : cookie HttpOnly posé par l'API, envoyé par le navigateur. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type MeOut, unwrap } from "./api/client";

export const ME_KEY = ["auth", "me"] as const;

/** Utilisateur connecté ; `null` sans session valide (401), jamais une erreur dans ce cas. */
export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async (): Promise<MeOut | null> => {
      try {
        return await unwrap(api.GET("/auth/me"));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    retry: false,
    staleTime: 60_000,
  });
}

export const isAdmin = (me: MeOut | null | undefined) => me?.user.platform_role === "ADMIN";

/** Page d'arrivée après connexion : console admin, sinon le portail supplier. */
export const homeFor = (me: MeOut) => (isAdmin(me) ? "/overview" : "/portal");

/** Le portail est réservé aux membres d'une organisation ; la console aux administrateurs plateforme. */
export const isPortalPath = (path: string) => path === "/portal" || path.startsWith("/portal/");

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) => unwrap(api.POST("/auth/login", { body })),
    onSuccess: (me) => {
      qc.clear(); // aucune donnée d'une session précédente
      qc.setQueryData(ME_KEY, me);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.POST("/auth/logout");
    },
    onSettled: () => {
      qc.clear();
      qc.setQueryData(ME_KEY, null);
    },
  });
}

export function useSelectOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (organization_id: string) => unwrap(api.POST("/auth/context", { body: { organization_id } })),
    onSuccess: (me) => {
      qc.clear();
      qc.setQueryData(ME_KEY, me);
    },
  });
}
