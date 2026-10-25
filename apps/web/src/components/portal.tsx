"use client";

/** Portail supplier (V1d) : réservé aux membres d'une organisation. Avec plusieurs organisations, l'utilisateur
 *  choisit laquelle ouvrir (POST /auth/context : l'API vérifie le membership). `PortalGate` garde chaque page
 *  du portail. */
import Link from "next/link";

import { FormError } from "@/components/form";
import { LoadingRows } from "@/components/registry";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { MeOut } from "@/lib/api/client";
import { isAdmin, useMe, useSelectOrganization } from "@/lib/auth";

export type Membership = MeOut["memberships"][number];

/** Rend `children(organisation courante)` ; sinon explique l'absence d'organisation ou propose le choix. */
export function PortalGate({ children }: { children: (org: Membership, me: MeOut) => React.ReactNode }) {
  const me = useMe().data;
  const select = useSelectOrganization();
  if (!me) return <LoadingRows />;

  const current = me.memberships.find((m) => m.organization_id === me.current_organization_id);

  if (me.memberships.length === 0) {
    return (
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>No supplier organization</CardTitle>
          <CardDescription>
            Your account is not a member of any supplier organization, so there is no portal to show.
          </CardDescription>
        </CardHeader>
        {isAdmin(me) && (
          <CardContent>
            <Link href="/systems" className="text-brand hover:underline">
              Open the admin console
            </Link>
          </CardContent>
        )}
      </Card>
    );
  }

  if (!current) {
    return (
      <div className="page-stack max-w-2xl">
        <div className="space-y-1.5">
          <h1 className="hero-badge">Choose an organization</h1>
          <p className="page-description">Your account belongs to several suppliers. Which one do you want to open?</p>
        </div>
        <div className="grid gap-3">
          {me.memberships.map((m) => (
            <Button
              key={m.organization_id}
              variant="outline"
              className="justify-start"
              disabled={select.isPending}
              onClick={() => select.mutate(m.organization_id)}
            >
              {m.organization_name}
            </Button>
          ))}
        </div>
        {select.error && <FormError error={select.error} />}
      </div>
    );
  }

  return <>{children(current, me)}</>;
}

/** Autres organisations du compte : bascule en un clic. */
export function OtherOrganizations({ me, current }: { me: MeOut; current: Membership }) {
  const select = useSelectOrganization();
  const others = me.memberships.filter((m) => m.organization_id !== current.organization_id);
  if (!others.length) return null;
  return (
    <div className="meta">
      Also member of:{" "}
      {others.map((m, i) => (
        <span key={m.organization_id}>
          {i > 0 && ", "}
          <button type="button" className="text-brand hover:underline" onClick={() => select.mutate(m.organization_id)}>
            {m.organization_name}
          </button>
        </span>
      ))}
    </div>
  );
}
