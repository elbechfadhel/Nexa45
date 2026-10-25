/** Éléments de formulaire partagés de la console : champ avec libellé / aide, erreur d'API lisible. */
import { ApiError } from "@/lib/api/client";

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      {/* .label du pack : 12px, majuscules */}
      <span className="label mb-0">{label}</span>
      {children}
      {hint && <span className="meta block">{hint}</span>}
    </label>
  );
}

export function FormError({ error }: { error: unknown }) {
  const detail = error instanceof ApiError ? error.detail : undefined;
  const lines = Array.isArray(detail)
    ? detail.map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")} : ${d.msg}`)
    : [typeof detail === "string" ? detail : error instanceof Error ? error.message : String(error)];
  return (
    <div className="rounded-lg border border-danger/35 bg-danger/10 p-3 text-sm text-danger">
      {lines.map((l) => (
        <div key={l}>{l}</div>
      ))}
    </div>
  );
}
