"use client";

/** Connect a system — wizard en 4 étapes (Select system → Connection details → Test connection → Review & save).
 *  Le formulaire de l'étape 2 est celui que le CONNECTEUR du système déclare (GET /api/connectors → `fields`) :
 *  cette page n'écrit aucun champ de connexion et ne teste aucun code de système (étapes : `wizard-steps.tsx`,
 *  logique pure : `lib/connect/form.mjs`). Test connection = POST /api/systems/check-connection (rien n'est
 *  enregistré, version DÉTECTÉE par GO45, jamais choisie) ; Save = POST /api/systems (le serveur re-teste avant
 *  d'enregistrer). Un secret ne vit que dans ce formulaire, jamais affiché ailleurs, effacé après
 *  l'enregistrement. Un test ne vaut que pour EXACTEMENT les valeurs testées. */
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { PortalGate } from "@/components/portal";
import { Button } from "@/components/ui/button";
import {
  type ConnectionCheck,
  useCheckConnection,
  useConnectors,
  useConnectSystem,
} from "@/lib/api/supplier-hooks";
import {
  emptyValues,
  fillFrom,
  isComplete,
  submittedValues,
  summaryRows,
  testedSignature,
} from "@/lib/connect/form.mjs";

import { Stepper } from "./stepper";
import { ConnectionDetails, Review, SelectSystem, TestResult, type Values } from "./wizard-steps";

type Step = 1 | 2 | 3 | 4;

export default function ConnectSystemPage() {
  return (
    <PortalGate>
      {() => (
        <div className="page-stack">
          <div>
            <h1 className="hero-badge">Connect a system</h1>
          </div>
          <Wizard />
        </div>
      )}
    </PortalGate>
  );
}

function Wizard() {
  const router = useRouter();
  const connectors = useConnectors();
  const check = useCheckConnection();
  const connect = useConnectSystem();
  const [step, setStep] = useState<Step>(1);
  const [system, setSystem] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [values, setValues] = useState<Values>({});
  const [tested, setTested] = useState<{ values: string; result: ConnectionCheck } | null>(null);

  const connector = connectors.data?.find((c) => c.system === system);
  const fields = connector?.fields ?? [];
  const signature = connector ? testedSignature(connector.system, fields, values) : "";
  const complete = !!connector && isComplete(name, fields, values);
  const result = tested?.values === signature ? tested.result : null; // invalidé dès qu'une valeur change
  const verified = !!result?.ok && result.supported;

  const select = (code: string) => {
    setSystem(code);
    const next = connectors.data?.find((c) => c.system === code);
    setValues(emptyValues(next?.fields ?? [])); // les valeurs d'un autre système ne suivent jamais
    setTested(null);
  };

  const runTest = () => {
    if (!connector || !complete || check.isPending) return;
    check.mutate(
      { system: connector.system, fields: submittedValues(fields, values) },
      {
        onSuccess: (r) => {
          setTested({ values: signature, result: r });
          setStep(3);
        },
      },
    );
  };

  const save = () => {
    if (!connector || !verified || connect.isPending) return;
    connect.mutate(
      { system: connector.system, name: name.trim(), fields: submittedValues(fields, values) },
      {
        onSuccess: (created) => {
          setValues({}); // aucun secret ne reste dans la page
          router.push(`/portal/systems/${created.id}`);
        },
      },
    );
  };

  return (
    <div className="space-y-6">
      <Stepper current={step} />

      {step === 1 && (
        <SelectSystem
          selected={system}
          connectors={connectors.data ?? []}
          loading={connectors.isPending}
          onSelect={select}
          onContinue={() => system && connector && setStep(2)}
        />
      )}

      {step === 2 && connector && (
        <ConnectionDetails
          connector={connector}
          name={name}
          setName={setName}
          values={values}
          setValues={setValues}
          complete={complete}
          testing={check.isPending}
          error={check.error}
          devFill={
            // aide de test locale : retirée des builds de production (condition évaluée à la compilation)
            process.env.NODE_ENV === "development" ? (
              <DevFill
                system={connector.system}
                onFill={(devName, dev) => {
                  setName(devName);
                  setValues(fillFrom(connector.fields, dev));
                }}
              />
            ) : null
          }
          onBack={() => setStep(1)}
          onTest={runTest}
        />
      )}

      {step === 3 && connector && (
        <TestResult
          result={result}
          rows={summaryRows(fields, values)}
          testing={check.isPending}
          onBack={() => setStep(2)}
          onRetry={runTest}
          onContinue={() => verified && setStep(4)}
        />
      )}

      {step === 4 && connector && (
        <Review
          connector={connector}
          name={name}
          result={result}
          rows={summaryRows(fields, values)}
          verified={verified}
          saving={connect.isPending}
          error={connect.error}
          onBack={() => setStep(3)}
          onSave={save}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// DEV uniquement
// --------------------------------------------------------------------------- //
type DevValues = { configured: boolean; system?: string; name?: string } & Record<string, unknown>;

/** DEV uniquement : remplit le formulaire avec une instance LOCALE de test (valeurs de apps/web/.env.local,
 *  servies par /dev/connect-fill avec le système auquel elles s'appliquent). Ne teste ni n'enregistre rien :
 *  chaque étape reste un clic du testeur. Absent si les valeurs ne concernent pas le système choisi. */
function DevFill({
  system,
  onFill,
}: {
  system: string;
  onFill: (name: string, values: Record<string, unknown>) => void;
}) {
  const dev = useQuery({
    queryKey: ["dev", "connect-fill"],
    queryFn: async (): Promise<DevValues> => {
      const r = await fetch("/dev/connect-fill", { cache: "no-store" });
      return r.ok ? r.json() : { configured: false };
    },
    staleTime: 0,
    gcTime: 0,
  });
  const v = dev.data;
  if (!v?.configured || v.system !== system) return null;
  return (
    <Button type="button" variant="outline" size="sm" onClick={() => onFill(v.name ?? "", v)}>
      Fill {v.name ?? "test system"} (DEV)
    </Button>
  );
}
