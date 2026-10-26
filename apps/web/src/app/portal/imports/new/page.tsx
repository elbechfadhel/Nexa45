"use client";

/** New import (V2c.1) — 3 étapes : Upload (.xlsx / .csv : nom, lignes, colonnes) → Target system (systèmes de
 *  l'organisation dont l'analyse du catalogue est terminée ; sélection automatique s'il n'y en a qu'un) →
 *  Analyze compatibility (lecture seule, rien n'est envoyé au système). `?import=<id>` reprend un fichier déjà
 *  déposé à l'étape 2. */
import { ArrowLeft, ArrowRight, FileSpreadsheet, Loader2, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";

import { FormError } from "@/components/form";
import { PortalGate } from "@/components/portal";
import { ErrorBox, LoadingRows } from "@/components/registry";
import { Button, buttonVariants } from "@/components/ui/button";
import { RadioList } from "@/components/ui/radio-list";
import { Card, CardContent } from "@/components/ui/card";
import {
  type CatalogImport,
  useAnalyzeImport,
  useImport,
  useSupplierSystems,
  useUploadImport,
} from "@/lib/api/supplier-hooks";
import { cn } from "@/lib/utils";

import { Stepper } from "../../systems/connect/stepper";

const STEPS = ["Upload file", "Target system", "Analyze compatibility"] as const;

export default function NewImportPage() {
  return (
    <PortalGate>
      {() => (
        <div className="page-stack">
          <div>
            <h1 className="hero-badge">New import</h1>
          </div>
          <Suspense fallback={<LoadingRows />}>
            <Resume />
          </Suspense>
        </div>
      )}
    </PortalGate>
  );
}

function Resume() {
  const resume = useSearchParams().get("import");
  if (!resume) return <Wizard initial={null} />;
  return <ResumeExisting importId={resume} />;
}

function ResumeExisting({ importId }: { importId: string }) {
  const existing = useImport(importId);
  if (existing.error) return <ErrorBox error={existing.error} />;
  if (!existing.data) return <LoadingRows />;
  return <Wizard initial={existing.data} />;
}

function Wizard({ initial }: { initial: CatalogImport | null }) {
  const [file, setFile] = useState<CatalogImport | null>(initial);
  const [step, setStep] = useState<1 | 2 | 3>(initial ? 2 : 1);
  const [systemId, setSystemId] = useState<string | null>(initial?.system?.id ?? null);

  return (
    <Card className="max-w-3xl">
      <CardContent className="space-y-8">
        <Stepper current={step} steps={STEPS} />
        {step === 1 && (
          <UploadStep
            uploaded={file}
            onUploaded={setFile}
            onNext={() => setStep(2)}
          />
        )}
        {step === 2 && file && (
          <SystemStep
            file={file}
            selected={systemId}
            onSelect={setSystemId}
            onBack={() => setStep(1)}
            onNext={() => setStep(3)}
          />
        )}
        {step === 3 && file && systemId && (
          <AnalyzeStep file={file} systemId={systemId} onBack={() => setStep(2)} />
        )}
      </CardContent>
    </Card>
  );
}

function FileSummary({ file }: { file: CatalogImport }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border p-4">
      <FileSpreadsheet className="size-8 shrink-0 text-brand" />
      <div className="min-w-0">
        <div className="truncate font-medium">{file.filename}</div>
        <div className="meta">
          {file.rows} rows · {file.columns} columns
        </div>
      </div>
    </div>
  );
}

function UploadStep({
  uploaded,
  onUploaded,
  onNext,
}: {
  uploaded: CatalogImport | null;
  onUploaded: (f: CatalogImport) => void;
  onNext: () => void;
}) {
  const upload = useUploadImport();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const send = (f: File | undefined) => f && upload.mutate(f, { onSuccess: onUploaded });

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h2 className="section-title">Upload your product file</h2>
        <p className="meta">An .xlsx or .csv file, one product per row, column names in the first row.</p>
      </div>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          send(e.dataTransfer.files[0]);
        }}
        className={cn(
          "flex w-full flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center transition-colors",
          dragging ? "border-brand bg-brand/5" : "border-border hover:border-brand/60",
        )}
      >
        {upload.isPending ? (
          <Loader2 className="size-6 animate-spin text-brand" />
        ) : (
          <Upload className="size-6 text-muted-foreground" />
        )}
        <span className="font-medium">{upload.isPending ? "Reading your file…" : "Choose a file or drop it here"}</span>
        <span className="meta">.xlsx or .csv · up to 10 MB</span>
      </button>
      <input
        ref={input}
        type="file"
        accept=".xlsx,.csv"
        className="hidden"
        onChange={(e) => {
          send(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {upload.error ? <FormError error={upload.error} /> : null}
      {uploaded && <FileSummary file={uploaded} />}
      <div className="flex justify-end">
        <Button disabled={!uploaded || upload.isPending} onClick={onNext}>
          Next <ArrowRight />
        </Button>
      </div>
    </div>
  );
}

function SystemStep({
  file,
  selected,
  onSelect,
  onBack,
  onNext,
}: {
  file: CatalogImport;
  selected: string | null;
  onSelect: (id: string) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const systems = useSupplierSystems();
  const ready = systems.data?.filter((s) => s.catalog.status === "complete") ?? [];
  const others = systems.data?.filter((s) => s.catalog.status !== "complete") ?? [];
  // un seul système prêt : choisi d'office
  const chosen = selected ?? (ready.length === 1 ? ready[0].id : null);

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h2 className="section-title">Which system is this file for?</h2>
        <p className="meta">We compare your columns with the catalog of this system.</p>
      </div>
      <FileSummary file={file} />
      {systems.error ? <ErrorBox error={systems.error} /> : null}
      {!systems.data ? (
        <LoadingRows rows={2} />
      ) : !ready.length ? (
        <div className="space-y-2 rounded-lg border border-border p-4">
          <div className="font-medium">No system is ready yet</div>
          <p className="meta">Connect a system and finish its catalog analysis first.</p>
          <Link href="/portal/systems" className={buttonVariants({ variant: "outline", size: "sm" })}>
            My systems
          </Link>
        </div>
      ) : (
        <RadioList
          label="Target system"
          value={chosen ?? ""}
          onChange={onSelect}
          options={[
            ...ready.map((s) => ({ value: s.id, label: s.name, hint: `${s.system} · catalog analyzed` })),
            // pas encore prêts : visibles, jamais choisissables
            ...others.map((s) => ({
              value: s.id,
              label: s.name,
              hint: `${s.system} · finish its catalog analysis first`,
              disabled: true,
            })),
          ]}
        />
      )}
      <div className="flex justify-between">
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft /> Back
        </Button>
        <Button
          disabled={!chosen}
          onClick={() => {
            if (chosen) onSelect(chosen);
            onNext();
          }}
        >
          Next <ArrowRight />
        </Button>
      </div>
    </div>
  );
}

function AnalyzeStep({ file, systemId, onBack }: { file: CatalogImport; systemId: string; onBack: () => void }) {
  const router = useRouter();
  const analyze = useAnalyzeImport(file.id);
  const systems = useSupplierSystems();
  const system = systems.data?.find((s) => s.id === systemId);

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h2 className="section-title">Analyze compatibility</h2>
        <p className="meta">
          We check each column of your file against the catalog of {system?.name ?? "your system"}. Nothing is
          sent to your system.
        </p>
      </div>
      <FileSummary file={file} />
      {analyze.error ? <FormError error={analyze.error} /> : null}
      <div className="flex justify-between">
        <Button variant="outline" onClick={onBack} disabled={analyze.isPending}>
          <ArrowLeft /> Back
        </Button>
        <Button
          disabled={analyze.isPending}
          onClick={() =>
            analyze.mutate(systemId, { onSuccess: () => router.push(`/portal/imports/${file.id}`) })
          }
        >
          {analyze.isPending ? <Loader2 className="animate-spin" /> : null}
          Analyze compatibility
        </Button>
      </div>
    </div>
  );
}
