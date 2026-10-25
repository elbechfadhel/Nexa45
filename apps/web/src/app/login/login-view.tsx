"use client";

/** Connexion (V1d) : email + mot de passe → cookie de session HttpOnly posé par l'API. Le mot de passe ne
 *  reste dans l'état local que le temps de l'envoi. Retour à la page demandée (`?next=`, chemin interne
 *  seulement), sinon console admin ou portail selon le rôle. */
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Field } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, type MeOut } from "@/lib/api/client";
import { homeFor, isAdmin, isPortalPath, useLogin, useMe } from "@/lib/auth";

/** Chemin interne uniquement (jamais « //hote » ni une URL absolue : pas de redirection ouverte). */
function safeNext(next: string | null): string | undefined {
  return next && next.startsWith("/") && !next.startsWith("//") && next !== "/login" ? next : undefined;
}

function destination(me: MeOut, next: string | undefined): string {
  // un supplier ne peut pas ouvrir la console admin : on ne l'y renvoie pas
  if (next && (isAdmin(me) || isPortalPath(next))) return next;
  return homeFor(me);
}

export function LoginView() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const me = useMe();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  // déjà connecté : rien à faire ici (une seule navigation, cf. AuthGate)
  const left = useRef(false);
  useEffect(() => {
    if (me.data && !left.current) {
      left.current = true;
      router.replace(destination(me.data, next));
    }
  }, [me.data, next, router]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password || login.isPending) return;
    login.mutate(
      { email: email.trim(), password },
      {
        onSuccess: (who) => {
          left.current = true;
          router.replace(destination(who, next));
        },
        onSettled: () => setPassword(""),
      },
    );
  };

  const error = login.error;
  const message =
    error instanceof ApiError && error.status === 401
      ? "Invalid email or password."
      : error instanceof ApiError && error.status === 429
        ? "Too many attempts. Please wait a few minutes and try again."
        : error
          ? "The server could not be reached. Please try again."
          : undefined;

  return (
    // mise en page et fond de la page de connexion de Transliate (frontend/src/pages/LoginPage.tsx) :
    // présentation à gauche, carte de connexion à droite ; empilées sous lg (globals.css, data-page-background)
    <div
      data-page-background="login"
      className="grid grid-cols-[minmax(0,1fr)] items-center gap-14 py-8 lg:grid-cols-2 lg:py-16"
    >
      <section className="min-w-0">
        <span className="hero-badge">Secure access</span>
        <h1 className="h-hero mt-6 max-w-2xl">Access your Nexa45 platform.</h1>
        <p className="mt-4 text-sm font-semibold tracking-[0.18em] text-muted-foreground uppercase">
          Connect. Understand. Integrate.
        </p>
        <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
          One integration layer for your ERP and accounting systems — Odoo, DATEV, Lexware Office and more. Your
          data is understood once and checked before anything is written.
        </p>
        <div className="mt-8 inline-flex rounded-full border border-border bg-card px-4 py-2 text-sm text-muted-foreground shadow-sm">
          Encrypted credentials · Role-based access · Tenant isolation
        </div>
      </section>

      <section className="mx-auto w-full max-w-md">
      <Card className="rounded-2xl shadow-[var(--shadow-panel)] [--card-spacing:--spacing(8)]">
        <CardHeader>
          <p className="eyebrow">Authentication</p>
          <CardTitle className="mt-1 text-2xl">Sign in</CardTitle>
          <CardDescription>Sign in to access your workspace.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-5">
            <Field label="Email address">
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                placeholder="name@company.com"
                autoFocus
                required
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </Field>
            {message && (
              <div role="alert" className="rounded-md border border-danger/35 bg-danger/10 p-3 text-sm text-danger">
                {message}
              </div>
            )}
            <Button type="submit" className="w-full" disabled={!email.trim() || !password || login.isPending}>
              {login.isPending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
      </section>
    </div>
  );
}
