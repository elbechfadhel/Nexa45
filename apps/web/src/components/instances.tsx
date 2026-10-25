"use client";

/** Éléments d'affichage des instances : mise en forme des statuts fournis par l'API. */
import { StatusBadge, type Tone } from "@/components/status-badge";
import type { CapabilityOut, InstanceOut } from "@/lib/api/client";

export const CAPABILITY_TONE: Record<string, Tone> = {
  VERIFIED: "success",
  DECLARED: "info",
  UNAVAILABLE: "danger",
  UNKNOWN: "neutral",
};

export function CapabilityBadge({ cap, compact = false }: { cap: CapabilityOut; compact?: boolean }) {
  return (
    <StatusBadge
      mono
      tone={CAPABILITY_TONE[cap.status] ?? "neutral"}
      title={`${cap.status}${cap.detail ? ` — ${cap.detail}` : ""}`}
    >
      {cap.capability}
      {!compact && <span className="font-normal opacity-70">{cap.status}</span>}
    </StatusBadge>
  );
}

export function ConnectionStatus({ instance }: { instance: InstanceOut }) {
  const c = instance.connection;
  if (!c?.last_test_at) return <StatusBadge tone="neutral">Not tested</StatusBadge>;
  return c.last_test_status === "OK" ? (
    <StatusBadge tone="success">Connected</StatusBadge>
  ) : (
    <StatusBadge tone="danger">Connection failed</StatusBadge>
  );
}
