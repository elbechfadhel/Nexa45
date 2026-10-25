import { StatusBadge } from "@/components/status-badge";
import type { SupplierSystem } from "@/lib/api/supplier-hooks";

/** Statut de connexion d'un système, en mots simples. */
export function ConnectionBadge({ system }: { system: SupplierSystem }) {
  const status = system.connection.status;
  if (status === "connected") return <StatusBadge tone="success">Connected</StatusBadge>;
  if (status === "failed") return <StatusBadge tone="danger">Connection failed</StatusBadge>;
  return <StatusBadge tone="neutral">Not tested yet</StatusBadge>;
}
