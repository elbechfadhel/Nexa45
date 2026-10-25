"use client";

/** Étapes du wizard « Connect a system ». Le formulaire de l'étape 2 est celui que le CONNECTEUR du système
 *  déclare (`connector.fields`) : aucun champ de connexion n'est écrit ici et aucun code de système n'est
 *  testé. Un secret est masqué, jamais rappelé dans un récapitulatif. Logique pure : `lib/connect/form.mjs`. */
import { ArrowLeft, ArrowRight, CheckCircle2, ChevronRight, Eye, EyeOff, Info, XCircle } from "lucide-react";
import { useState } from "react";

import { Field, FormError } from "@/components/form";
import { Pill } from "@/components/status-badge";
import { SystemLogo } from "@/components/system-logo";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { ConnectionCheck, Connector } from "@/lib/api/supplier-hooks";
import { inputType } from "@/lib/connect/form.mjs";
import { cn } from "@/lib/utils";

export type Values = Record<string, string>;
export type Row = { label: string; value: string; technical: boolean };

// Systèmes annoncés mais pas encore connectables. Tout système réellement connectable vient de l'API
// (GET /api/connectors) et s'ajoute de lui-même, sans entrée ici.
const PLANNED = [
  { code: "sap", name: "SAP" },
  { code: "dynamics", name: "Microsoft Dynamics 365" },
  { code: "datev", name: "DATEV" },
  { code: "lexware", name: "Lexware Office" },
  { code: "custom", name: "Custom / Other ERP" },
];

// --------------------------------------------------------------------------- //
// Étape 1
// --------------------------------------------------------------------------- //
export function SelectSystem({
  selected,
  connectors,
  loading,
  onSelect,
  onContinue,
}: {
  selected: string | null;
  connectors: Connector[];
  loading: boolean;
  onSelect: (code: string) => void;
  onContinue: () => void;
}) {
  // connectables d'abord (API), puis les systèmes annoncés qui ne le sont pas encore
  const available = new Set(connectors.map((c) => c.system));
  const cards: { code: string; name: string; versions: string[] | null }[] = [
    ...connectors.map((c) => ({ code: c.system, name: c.name, versions: c.versions })),
    ...PLANNED.filter((p) => !available.has(p.code)).map((p) => ({ ...p, versions: null })),
  ];
  return (
    <Card>
      <CardContent className="space-y-5">
        <div className="space-y-1">
          <h2 className="section-title">What system do you want to connect?</h2>
          <p className="text-muted-foreground">Choose the system you want to connect to your workspace.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {cards.map((card) => {
            const enabled = !!card.versions;
            const isSelected = selected === card.code;
            return (
              <button
                key={card.code}
                type="button"
                disabled={!enabled}
                onClick={() => onSelect(card.code)}
                aria-pressed={isSelected}
                className={cn(
                  "flex items-center gap-4 rounded-card-sm border p-4.5 text-left transition-colors",
                  enabled ? "hover:border-brand/50" : "cursor-not-allowed opacity-60",
                  isSelected ? "border-brand bg-brand/5 ring-1 ring-brand/40" : "border-border bg-muted/30",
                )}
              >
                <SystemLogo code={card.code} name={card.name} />
                <span className="min-w-0 flex-1 space-y-1.5">
                  <span className="item-title block">{card.name}</span>
                  {enabled ? (
                    <Pill tone="success">Available</Pill>
                  ) : (
                    <Pill tone="neutral" variant="neutral">
                      {loading ? "Checking…" : "Coming soon"}
                    </Pill>
                  )}
                  <span className="meta block">
                    {card.versions ? `Supported versions: ${card.versions.join(", ")}` : "Planned for a future release"}
                  </span>
                </span>
                {enabled && <ChevronRight className="size-5 text-muted-foreground" />}
              </button>
            );
          })}
        </div>
        <div className="flex justify-end">
          <Button onClick={onContinue} disabled={!selected || !available.has(selected)}>
            Continue <ArrowRight />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Étape 2 : les champs viennent du connecteur
// --------------------------------------------------------------------------- //
export function ConnectionDetails({
  connector,
  name,
  setName,
  values,
  setValues,
  complete,
  testing,
  error,
  devFill,
  onBack,
  onTest,
}: {
  connector: Connector;
  name: string;
  setName: (v: string) => void;
  values: Values;
  setValues: (v: Values | ((v: Values) => Values)) => void;
  complete: boolean;
  testing: boolean;
  error: unknown;
  devFill?: React.ReactNode;
  onBack: () => void;
  onTest: () => void;
}) {
  const [shown, setShown] = useState<Record<string, boolean>>({});

  return (
    <Card>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-4">
            <SystemLogo code={connector.system} name={connector.name} />
            <div>
              <h2 className="section-title">Connect your {connector.name} system</h2>
              <p className="text-muted-foreground">Enter the connection details of your {connector.name} account.</p>
            </div>
          </div>
          {devFill}
        </div>

        <form
          className="space-y-5"
          autoComplete="off"
          onSubmit={(e) => {
            e.preventDefault();
            onTest();
          }}
        >
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="System name" hint="A name to identify this system in your workspace.">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={`Main ${connector.name}`} />
            </Field>
            {connector.fields.map((field) => {
              const secret = field.kind === "secret";
              const visible = !!shown[field.name];
              const input = (
                <Input
                  type={inputType(field, visible)}
                  value={values[field.name] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.value }))}
                  placeholder={field.placeholder ?? undefined}
                  autoComplete={secret ? "new-password" : "off"}
                  className={secret ? "pr-10" : undefined}
                />
              );
              return (
                <Field key={field.name} label={field.label} hint={field.help ?? undefined}>
                  {secret ? (
                    <div className="relative">
                      {input}
                      <button
                        type="button"
                        onClick={() => setShown((s) => ({ ...s, [field.name]: !visible }))}
                        aria-label={visible ? `Hide ${field.label}` : `Show ${field.label}`}
                        className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground"
                      >
                        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                      </button>
                    </div>
                  ) : (
                    input
                  )}
                </Field>
              );
            })}
          </div>

          <div className="flex items-start gap-2 rounded-md border border-info/30 bg-info/10 p-3 text-sm">
            <Info className="mt-0.5 size-4 shrink-0 text-info" />
            <span>
              Your credentials are only used to test the connection and to store the system securely. They are never
              displayed again and are stored encrypted. GO45 detects your {connector.name} version automatically.
            </span>
          </div>

          {error ? <FormError error={error} /> : null}

          <div className="flex items-center justify-between gap-3">
            <Button type="button" variant="outline" onClick={onBack}>
              <ArrowLeft /> Back
            </Button>
            <Button type="submit" disabled={!complete || testing}>
              {testing ? "Testing connection…" : "Test connection"} {!testing && <ArrowRight />}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function Rows({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="grid gap-x-6 gap-y-2 rounded-card-sm border border-border bg-muted/30 p-4.5 sm:grid-cols-[180px_1fr]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="meta">{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Ce que l'on sait du système joint : version détectée, faits renvoyés par le test (ex. société du compte),
 *  puis les valeurs NON secrètes saisies. Jamais un secret. */
function knownRows(result: ConnectionCheck | null, rows: Row[]): [string, React.ReactNode][] {
  return [
    ["Version", result?.detected ?? "—"],
    ...(result?.details ?? []).map((d): [string, React.ReactNode] => [d.label, d.value]),
    ...rows.map((r): [string, React.ReactNode] => [
      r.label,
      r.technical ? (
        <span key={r.label} className="tech break-all">
          {r.value}
        </span>
      ) : (
        r.value
      ),
    ]),
  ];
}

// --------------------------------------------------------------------------- //
// Étape 3
// --------------------------------------------------------------------------- //
export function TestResult({
  result,
  rows,
  testing,
  onBack,
  onRetry,
  onContinue,
}: {
  result: ConnectionCheck | null;
  rows: Row[];
  testing: boolean;
  onBack: () => void;
  onRetry: () => void;
  onContinue: () => void;
}) {
  if (!result) {
    // valeurs modifiées depuis le test : il faut retester
    return (
      <Card>
        <CardContent className="space-y-4">
          <p>The connection details changed since the last test. Test the connection again.</p>
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft /> Back
          </Button>
        </CardContent>
      </Card>
    );
  }
  const ok = result.ok && result.supported;
  return (
    <Card>
      <CardContent className="space-y-5">
        <div className="flex items-start gap-4">
          {result.ok ? (
            <CheckCircle2 className={cn("size-12 shrink-0", ok ? "text-success" : "text-warning")} />
          ) : (
            <XCircle className="size-12 shrink-0 text-danger" />
          )}
          <div className="space-y-1">
            <h2 className={cn("section-title", result.ok ? (ok ? "text-success" : "") : "text-danger")}>
              {result.ok ? "Connection successful" : "Connection failed"}
            </h2>
            {result.detected && <div className="font-medium">{result.detected} detected</div>}
            {ok && <p className="text-muted-foreground">This version is supported by GO45.</p>}
            {result.ok && !result.supported && <p className="text-danger">This version is not supported yet.</p>}
            {!result.ok && result.message && <p className="text-muted-foreground">{result.message}</p>}
          </div>
        </div>

        {result.ok && <Rows rows={knownRows(result, rows)} />}

        <div className="flex items-center justify-between gap-3">
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft /> Back
          </Button>
          {ok ? (
            <Button onClick={onContinue}>
              Continue <ArrowRight />
            </Button>
          ) : !result.ok ? (
            <Button onClick={onRetry} disabled={testing}>
              {testing ? "Testing connection…" : "Try again"}
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Étape 4
// --------------------------------------------------------------------------- //
export function Review({
  connector,
  name,
  result,
  rows,
  verified,
  saving,
  error,
  onBack,
  onSave,
}: {
  connector: Connector;
  name: string;
  result: ConnectionCheck | null;
  rows: Row[];
  verified: boolean;
  saving: boolean;
  error: unknown;
  onBack: () => void;
  onSave: () => void;
}) {
  const all: [string, React.ReactNode][] = [
    ["System name", name.trim()],
    ["System", connector.name],
    ...knownRows(result, rows),
    [
      "Status",
      <span key="status" className="inline-flex items-center gap-1.5 text-success">
        <CheckCircle2 className="size-4" /> Connection verified
      </span>,
    ],
  ];
  return (
    <Card>
      <CardContent className="space-y-5">
        <div className="flex items-center gap-4">
          <SystemLogo code={connector.system} name={connector.name} />
          <div>
            <h2 className="section-title">Review and save</h2>
            <p className="text-muted-foreground">Check the information below before creating your system.</p>
          </div>
        </div>
        <Rows rows={all} />
        {error ? <FormError error={error} /> : null}
        <div className="flex items-center justify-between gap-3">
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft /> Back
          </Button>
          <Button onClick={onSave} disabled={!verified || saving}>
            {saving ? "Saving…" : "Save system"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
