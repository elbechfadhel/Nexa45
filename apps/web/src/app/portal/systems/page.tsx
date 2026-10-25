"use client";

/** My systems (V2b.1) : les systèmes du fournisseur et « Connect a system ». */
import { Plus } from "lucide-react";
import Link from "next/link";

import { OtherOrganizations, PortalGate } from "@/components/portal";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useSupplierSystems } from "@/lib/api/supplier-hooks";

import { ConnectionBadge } from "./connection-badge";

export default function MySystemsPage() {
  return (
    <PortalGate>
      {(org, me) => (
        <div className="page-stack">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1.5">
              <h1 className="hero-badge">My systems</h1>
              <p className="page-description">The systems your catalog comes from.</p>
            </div>
            <Link href="/portal/systems/connect" className={buttonVariants()}>
              <Plus /> Connect a system
            </Link>
          </div>
          <SystemList />
          <OtherOrganizations me={me} current={org} />
        </div>
      )}
    </PortalGate>
  );
}

function SystemList() {
  const systems = useSupplierSystems();
  if (systems.error) return <ErrorBox error={systems.error} />;
  if (!systems.data) return <LoadingRows />;
  if (!systems.data.length) {
    return (
      <Card size="sm" className="max-w-2xl">
        <CardContent className="space-y-1">
          <div className="item-title">No system connected yet</div>
          <p className="meta">Connect your system to let us read your product catalog.</p>
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="grid gap-4.5 md:grid-cols-2 xl:grid-cols-3">
      {systems.data.map((s) => (
        <Card key={s.id} size="sm">
          <CardContent className="flex h-full flex-col gap-3">
            <div className="space-y-1">
              <div className="item-title">{s.name}</div>
              <div className="meta">{s.system}</div>
            </div>
            <ConnectionBadge system={s} />
            <Link
              href={`/portal/systems/${s.id}`}
              className={`${buttonVariants({ variant: "outline", size: "sm" })} mt-auto self-start`}
            >
              Open
            </Link>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
