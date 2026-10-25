"use client";

/** Supprimer un système (V2b.1a) : suppression définitive après confirmation (saisie du nom). Rien n'est
 *  modifié dans le système du fournisseur (accès en lecture seule). */
import { Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { FormError } from "@/components/form";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { type SupplierSystem, useRemoveSystem } from "@/lib/api/supplier-hooks";

export function RemoveSystem({ system }: { system: SupplierSystem }) {
  const router = useRouter();
  const remove = useRemoveSystem(system.id);
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");

  if (!open) {
    return (
      <div>
        <Button variant="outline" className="text-danger" onClick={() => setOpen(true)}>
          <Trash2 /> Remove this system
        </Button>
      </div>
    );
  }

  return (
    <Card className="border-danger/40 bg-danger/5">
      <CardHeader>
        <CardTitle className="text-danger">Remove “{system.name}”?</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p>
          The connection, the catalog analysis, your answers and the synced products will be deleted. This cannot be
          undone. Nothing is changed in your {system.system.split(" ")[0]}.
        </p>
        <label className="block space-y-1.5">
          <span className="block text-sm font-medium">
            To confirm, type the system name: <span className="tech">{system.name}</span>
          </span>
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
        </label>
        {remove.error && <FormError error={remove.error} />}
        <div className="flex gap-2">
          <Button
            variant="destructive"
            disabled={typed !== system.name || remove.isPending}
            onClick={() => remove.mutate(undefined, { onSuccess: () => router.push("/portal/systems") })}
          >
            {remove.isPending ? "Removing…" : "Remove system"}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setOpen(false);
              setTyped("");
            }}
          >
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
