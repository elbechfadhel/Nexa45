"use client";

/** Garde de navigation (V1d) : sans session → /login ; console technique réservée aux administrateurs
 *  plateforme, portail /portal aux membres d'une organisation. La sécurité reste côté API (401 / 403) :
 *  cette garde évite seulement d'afficher un écran que l'API refuserait. */
import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { ErrorBox, LoadingRows } from "@/components/registry";
import { setUnauthorizedHandler } from "@/lib/api/client";
import { isAdmin, isPortalPath, ME_KEY, useMe } from "@/lib/auth";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const onLogin = pathname === "/login";

  // un 401 sur n'importe quel appel (session expirée ou révoquée) ramène à l'état « non connecté »
  useEffect(() => {
    setUnauthorizedHandler(() => qc.setQueryData(ME_KEY, null));
    return () => setUnauthorizedHandler(undefined);
  }, [qc]);

  const redirect =
    onLogin || me.data === undefined
      ? undefined
      : me.data === null
        ? `/login?next=${encodeURIComponent(pathname)}`
        : !isPortalPath(pathname) && !isAdmin(me.data)
          ? "/portal"
          : undefined;

  // une seule navigation par cible : tant que la transition est en cours, la page se re-rend avec la même
  // cible et relancer router.replace en boucle bloquerait l'onglet
  const sent = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (redirect && sent.current !== redirect) {
      sent.current = redirect;
      router.replace(redirect);
    }
    if (!redirect) sent.current = undefined;
  }, [redirect, router]);

  if (onLogin) return children;
  if (me.error) return <ErrorBox error={me.error} />;
  if (me.data === undefined || redirect) return <LoadingRows />;
  return children;
}
