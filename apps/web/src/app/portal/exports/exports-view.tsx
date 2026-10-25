"use client";

/** Exports (PARTNER) : vos clients et fournisseurs, lus dans votre système, préparés pour un format de fichier
 *  (ex. DATEV Debitoren/Kreditoren). 1. Export settings (profil, aucune valeur proposée par défaut : plages de
 *  comptes selon votre comptabilité) ; 2. Preview (lecture seule, aucun numéro réservé) ; 3. Download file
 *  (numéros réservés, journal). Rien n'est envoyé automatiquement au système cible. */
import { CheckCircle2, Download, Eye, Save } from "lucide-react";
import { Fragment, useState } from "react";

import { Field, FormError } from "@/components/form";
import { OtherOrganizations, PortalGate } from "@/components/portal";
import { ErrorBox, formatDate, LoadingRows } from "@/components/registry";
import { Pill, type Tone } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError } from "@/lib/api/client";
import {
  type ExportPreview,
  type ExportProfile,
  type ExportTarget,
  useExportPreview,
  useExportProfile,
  useExportRuns,
  useExportTargets,
  useGenerateExport,
  useSaveExportProfile,
  useSupplierSystems,
} from "@/lib/api/supplier-hooks";
import { useUrlState } from "@/lib/use-url-state";

const STATUS: Record<string, { tone: Tone; label: string }> = {
  ready: { tone: "success", label: "Ready" },
  warning: { tone: "warning", label: "Warning" },
  blocked: { tone: "danger", label: "Blocked" },
};

export function ExportsView() {
  return (
    <PortalGate>
      {(org, me) => (
        <div className="page-stack">
          <div className="space-y-1.5">
            <h1 className="hero-badge">Exports</h1>
            <p className="page-description">
              Export your customers and suppliers to a file for your accounting. Nothing is sent automatically:
              you download the file and import it yourself.
            </p>
          </div>
          <Targets />
          <Runs />
          <OtherOrganizations me={me} current={org} />
        </div>
      )}
    </PortalGate>
  );
}

function Targets() {
  const targets = useExportTargets();
  if (targets.error) return <ErrorBox error={targets.error} />;
  if (!targets.data) return <LoadingRows rows={2} />;
  const available = targets.data.filter((t) => t.format_version);
  if (!available.length) {
    return (
      <Card size="sm" className="max-w-2xl">
        <CardContent>No export format is available yet.</CardContent>
      </Card>
    );
  }
  return (
    <>
      {available.map((t) => (
        <TargetExport key={`${t.system}/${t.product}`} target={t} />
      ))}
    </>
  );
}

function TargetExport({ target }: { target: ExportTarget }) {
  const profile = useExportProfile(target.system, target.product);
  return (
    <div className="page-stack">
      <h2 className="section-title">
        {target.name} <span className="meta">· version {target.format_version}</span>
      </h2>
      {profile.error && <ErrorBox error={profile.error} />}
      {profile.data && (
        // remonté quand le profil enregistré change : le formulaire repart des valeurs sauvegardées
        <ProfileForm key={profile.data.updated_at ?? "new"} target={target} profile={profile.data} />
      )}
      {profile.data?.complete && <PreviewSection target={target} />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// 1. Export settings
// --------------------------------------------------------------------------- //
type Values = Record<string, unknown>;

function ProfileForm({ target, profile }: { target: ExportTarget; profile: ExportProfile }) {
  const save = useSaveExportProfile(target.system, target.product);
  const [values, setValues] = useState<Values>(profile.values);
  const [open, setOpen] = useState(!profile.complete);
  const errors =
    save.error instanceof ApiError
      ? ((save.error.body as { errors?: Record<string, string> } | undefined)?.errors ?? {})
      : {};
  const set = (name: string, v: unknown) => setValues((cur) => ({ ...cur, [name]: v }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3">
          1. Export settings
          {profile.complete ? <Pill tone="success">Complete</Pill> : <Pill tone="warning">To fill in</Pill>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {!open ? (
          <div className="flex flex-wrap items-center gap-3">
            <span className="meta">
              Saved {profile.updated_at ? formatDate(profile.updated_at) : ""} — these settings must match your
              accounting setup.
            </span>
            <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
              Edit settings
            </Button>
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate(values, { onSuccess: () => setOpen(false) });
            }}
          >
            <p className="meta">
              Fill in these settings as configured in your accounting or by your tax advisor. GO45 proposes no
              default account ranges.
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              {profile.params.map((p) => (
                <div key={p.name} className={p.type === "range" || p.type === "boolean" ? "sm:col-span-2" : ""}>
                  <Param param={p} value={values[p.name]} onChange={(v) => set(p.name, v)} />
                  {errors[p.name] && <p className="mt-1 text-sm text-danger">{errors[p.name]}</p>}
                </div>
              ))}
            </div>
            {save.error && !Object.keys(errors).length ? <FormError error={save.error} /> : null}
            <div className="flex gap-2">
              <Button type="submit" disabled={save.isPending}>
                <Save /> {save.isPending ? "Saving…" : "Save settings"}
              </Button>
              {profile.complete && (
                <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
              )}
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

/** Aide affichée sous un paramètre : format attendu (déclaré par le binding) puis aide libre. */
function hintOf(param: ExportProfile["params"][number]): string | undefined {
  return [param.format_help, param.help].filter(Boolean).join(" ") || undefined;
}

function Param({
  param,
  value,
  onChange,
}: {
  param: ExportProfile["params"][number];
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const label = `${param.label}${param.required ? " *" : ""}`;
  if (param.type === "boolean") {
    return (
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
        <span>{param.label}</span>
      </label>
    );
  }
  if (param.type === "choice") {
    return (
      <Field label={label} hint={hintOf(param)}>
        <select
          value={String(value ?? param.default ?? "")}
          onChange={(e) => onChange(e.target.value)}
          className="input"
        >
          {param.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </Field>
    );
  }
  if (param.type === "range") {
    const range = (value as { start?: unknown; end?: unknown } | undefined) ?? {};
    return (
      <Field label={label} hint={hintOf(param)}>
        <div className="flex items-center gap-2">
          <Input
            inputMode="numeric"
            placeholder="From"
            value={String(range.start ?? "")}
            onChange={(e) => onChange({ ...range, start: e.target.value })}
          />
          <span className="meta">to</span>
          <Input
            inputMode="numeric"
            placeholder="To"
            value={String(range.end ?? "")}
            onChange={(e) => onChange({ ...range, end: e.target.value })}
          />
        </div>
      </Field>
    );
  }
  return (
    <Field label={label} hint={hintOf(param)}>
      <Input
        type={param.type === "date" ? "date" : "text"}
        inputMode={param.type === "integer" ? "numeric" : undefined}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

// --------------------------------------------------------------------------- //
// 2. Preview, 3. Download
// --------------------------------------------------------------------------- //
function PreviewSection({ target }: { target: ExportTarget }) {
  const systems = useSupplierSystems();
  const url = useUrlState();
  // sources possibles : systèmes qui FOURNISSENT des partenaires (analysés ou déclarés), d'après l'API
  const sources = systems.data?.filter((s) => s.provides.includes("partner"));
  const systemId = url.get("system") ?? sources?.[0]?.id;
  const [asked, setAsked] = useState(false);
  const preview = useExportPreview(target.system, target.product, systemId, asked);
  const generate = useGenerateExport(target.system, target.product);

  if (systems.error) return <ErrorBox error={systems.error} />;
  if (!sources) return <LoadingRows rows={2} />;
  const p = preview.data;
  const writable = p ? p.counts.ready + p.counts.warnings : 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>2. Preview and download</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="System">
            <select
              value={systemId ?? ""}
              onChange={(e) => {
                url.set({ system: e.target.value });
                setAsked(false);
              }}
              className="input w-auto min-w-64"
            >
              {sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} · {s.system}
                </option>
              ))}
            </select>
          </Field>
          <Button variant="outline" disabled={!systemId || preview.isFetching} onClick={() => (asked ? preview.refetch() : setAsked(true))}>
            <Eye /> {preview.isFetching ? "Reading your partners…" : asked ? "Refresh preview" : "Preview"}
          </Button>
          {p && (
            <Button disabled={!writable || generate.isPending} onClick={() => systemId && generate.mutate(systemId)}>
              <Download /> {generate.isPending ? "Generating…" : `Download file (${writable} records)`}
            </Button>
          )}
        </div>
        <p className="meta">
          The preview reads your system and reserves nothing. Account numbers marked « New » are reserved only
          when you download the file; blocked records are not written.
        </p>
        {preview.error && <FormError error={preview.error} />}
        {generate.error && <FormError error={generate.error} />}
        {generate.data && (
          <div className="flex items-center gap-2 text-success">
            <CheckCircle2 className="size-5" /> Downloaded {generate.data}
          </div>
        )}
        {preview.isFetching && <LoadingRows rows={4} />}
        {p && !preview.isFetching && <PreviewResult preview={p} />}
      </CardContent>
    </Card>
  );
}

function PreviewResult({ preview: p }: { preview: ExportPreview }) {
  const [open, setOpen] = useState<string | null>(null);
  const c = p.counts;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Pill tone="neutral" variant="neutral">
          {c.partners} partners · {c.records} records
        </Pill>
        <Pill tone="success">{c.ready} ready</Pill>
        <Pill tone="warning">{c.warnings} with warnings</Pill>
        <Pill tone="danger">{c.blocked} blocked</Pill>
        <Pill tone="info">{c.new_accounts} new account numbers</Pill>
        <span className="meta self-center">Encoding {p.encoding}</span>
      </div>
      {p.truncated && <p className="text-warning">Only part of your partners was read (limit reached).</p>}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Partner</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Account</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Issues</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {p.records.map((r) => {
            const key = `${r.name}|${r.role}`;
            return (
              <Fragment key={key}>
                <TableRow className="cursor-pointer" onClick={() => setOpen(open === key ? null : key)}>
                  <TableCell className="font-medium">{r.name}</TableCell>
                  <TableCell className="capitalize">{r.role}</TableCell>
                  <TableCell>
                    <span className="tech">{r.account_number ?? "—"}</span>
                    {r.account_new && r.account_number && (
                      <Pill tone="info" className="ml-2">
                        New
                      </Pill>
                    )}
                  </TableCell>
                  <TableCell>
                    <Pill tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Pill>
                  </TableCell>
                  <TableCell className="text-sm">
                    {r.issues.map((i) => (
                      <div key={i.message} className={i.level === "blocking" ? "text-danger" : "text-warning"}>
                        {i.message}
                      </div>
                    ))}
                  </TableCell>
                </TableRow>
                {open === key && (
                  <TableRow>
                    <TableCell colSpan={5}>
                      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2 lg:grid-cols-3">
                        {r.columns.map((col) => (
                          <div key={col.label} className="flex gap-2">
                            <dt className="meta">{col.label}</dt>
                            <dd className="break-all">{col.value}</dd>
                          </div>
                        ))}
                      </dl>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>

      {p.not_exported.length > 0 && (
        <div>
          <div className="font-medium">Not exported</div>
          <ul className="meta">
            {p.not_exported.map((n) => (
              <li key={`${n.name}`}>
                {n.name} — {n.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {p.source_issues.length > 0 && (
        <div>
          <div className="font-medium">Read from your system</div>
          <ul className="meta">
            {p.source_issues.map((i) => (
              <li key={`${i.name}-${i.message}`}>
                {i.name}: {i.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Runs() {
  const runs = useExportRuns();
  if (runs.error || !runs.data?.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Generated files</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Format</TableHead>
              <TableHead>System</TableHead>
              <TableHead>Records</TableHead>
              <TableHead>New accounts</TableHead>
              <TableHead>Not written</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.data.map((r) => (
              <TableRow key={r.id}>
                <TableCell>{formatDate(r.created_at)}</TableCell>
                <TableCell>{r.target}</TableCell>
                <TableCell>{r.system ?? "—"}</TableCell>
                <TableCell>
                  {r.records}
                  {r.warnings > 0 && <span className="meta"> ({r.warnings} with warnings)</span>}
                </TableCell>
                <TableCell>{r.new_accounts}</TableCell>
                <TableCell>{r.blocked}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
