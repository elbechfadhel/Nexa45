"use client";

/** Suppression d'une instance avec confirmation (saisie du nom). Le détail de ce qui est supprimé
 *  vient de l'API ; le snapshot standard de référence est conservé. */
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorBox } from "@/components/registry";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { InstanceDetail } from "@/lib/api/client";
import { useDeleteInstance } from "@/lib/api/hooks";

export function DeleteInstancePanel({ i, onCancel }: { i: InstanceDetail; onCancel: () => void }) {
  const router = useRouter();
  const qc = useQueryClient();
  const del = useDeleteInstance(i.id);
  const [typed, setTyped] = useState("");
  const baselines = [...new Set(i.snapshots.map((s) => s.base_snapshot_label ?? s.base_snapshot_id))];

  return (
    <Card className="border-danger/40 bg-danger/5">
      <CardHeader>
        <CardTitle className="text-danger">Delete instance “{i.name}”?</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-base">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <div className="mb-1 font-medium">Permanently deleted</div>
            <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
              <li>the instance and its connection ({i.connection?.endpoint ?? "—"})</li>
              <li>its secret (removed from the SecretStore)</li>
              <li>{i.capabilities.length} capabilities</li>
              <li>
                {i.snapshots.length} instance snapshot(s) and their diffs
              </li>
            </ul>
          </div>
          <div>
            <div className="mb-1 font-medium">Kept, unchanged</div>
            <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
              {baselines.length ? (
                baselines.map((b) => <li key={b}>standard snapshot {b}</li>)
              ) : (
                <li>
                  the standard Registry ({i.system} {i.version})
                </li>
              )}
            </ul>
          </div>
        </div>
        <label className="block space-y-1.5">
          <span className="block text-sm font-medium">
            To confirm, type the instance name: <span className="tech">{i.name}</span>
          </span>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            className="max-w-sm font-mono"
            aria-label="Name of the instance to delete"
            autoFocus
          />
        </label>
        {del.error && <ErrorBox error={del.error} />}
        <div className="flex gap-2">
          <Button
            variant="destructive"
            disabled={typed !== i.name || del.isPending}
            onClick={() =>
              del.mutate(undefined, {
                onSuccess: () => {
                  router.push("/instances");
                  // après la navigation : sinon la page encore affichée relancerait ses requêtes (404)
                  setTimeout(() => {
                    qc.removeQueries({ queryKey: ["instance", i.id] });
                    qc.removeQueries({ queryKey: ["instance-diff", i.id] });
                  }, 0);
                },
              })
            }
          >
            {del.isPending ? "Deleting…" : "Delete permanently"}
          </Button>
          <Button variant="ghost" onClick={onCancel} disabled={del.isPending}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
