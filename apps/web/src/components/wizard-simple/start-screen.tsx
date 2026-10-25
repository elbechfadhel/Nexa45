"use client";

/** Écran 1 du mode Simple : déposer le fichier et choisir la cible. Vos systèmes connectés d'abord, puis les
 *  systèmes du Registry, puis les autres (structure proposée par l'IA) ; recherche au-delà de quelques choix. */
import { Loader2, Search, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useWizardTargets } from "@/lib/api/wizard-hooks";
import { cn } from "@/lib/utils";
import { defaultTarget, formatChoices, openTile, targetGroups, targetTiles, targetTitle } from "@/lib/wizard/simple.mjs";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

/** Au-delà, la liste propose une recherche. */
const SEARCH_FROM = 8;

export function StartScreen({
  file,
  onFile,
  target,
  onTarget,
  onStart,
}: {
  file: File | null;
  onFile: (file: File) => void;
  target: string;
  onTarget: (value: string) => void;
  onStart: () => void;
}) {
  const targets = useWizardTargets();
  const [search, setSearch] = useState("");
  const all = useMemo(() => targets.data ?? [], [targets.data]);
  const groups = targetGroups(all, search);
  // système à plusieurs formats ouvert : son format se choisit sous les tuiles
  const [openSystem, setOpenSystem] = useState("");
  const open = openTile(
    groups.flatMap((g) => targetTiles(g.items)),
    openSystem,
    target,
  );

  // votre système connecté est présélectionné (jamais un système au hasard)
  useEffect(() => {
    if (!target && all.length > 0) {
      const found = defaultTarget(all);
      if (found) onTarget(found);
    }
  }, [all, target, onTarget]);

  return (
    <div className="space-y-6">
      <Card className="bg-transparent shadow-none">
        <CardContent className="space-y-3">
          <h2 className="item-title">{L.fileHeading}</h2>
          <FileDrop file={file} onPick={onFile} />
        </CardContent>
      </Card>

      <Card className="bg-transparent shadow-none">
        <CardContent className="space-y-4">
          <h2 className="item-title">{L.targetHeading}</h2>
          {all.length > SEARCH_FROM && (
            <label className="relative block max-w-sm">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                className="input w-full pl-9"
                placeholder={L.targetSearch}
                aria-label={L.targetSearch}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
          )}
          {targets.isPending && (
            <p className="meta flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> {L.targetLoading}
            </p>
          )}
          {targets.isSuccess && groups.length === 0 && <p className="meta text-muted-foreground">{L.targetNone}</p>}
          <div className="space-y-5" role="radiogroup" aria-label={L.targetHeading}>
            {groups.map((g) => (
              <section key={g.group} className="space-y-2">
                <div>
                  <h3 className="eyebrow">{L.groups[g.group]}</h3>
                  <p className="meta text-muted-foreground">{L.groupHelp[g.group]}</p>
                </div>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {targetTiles(g.items).map((tile) => {
                    if (tile.kind === "system") {
                      const selected = open?.key === tile.key;
                      return (
                        <button
                          key={tile.key}
                          type="button"
                          role="radio"
                          aria-checked={selected}
                          onClick={() => {
                            setOpenSystem(tile.system);
                            if (!tile.formats.some((f) => f.value === target)) onTarget("");
                          }}
                          className={tileClass(selected)}
                        >
                          <span className="w-full truncate font-medium">{tile.label}</span>
                          <span className="meta w-full truncate text-muted-foreground">
                            {L.formatCount(tile.formats.length)}
                          </span>
                        </button>
                      );
                    }
                    const t = tile.target;
                    const selected = t.value === target;
                    return (
                      <button
                        key={tile.key}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => {
                          setOpenSystem("");
                          onTarget(t.value);
                        }}
                        className={tileClass(selected)}
                      >
                        <span className="w-full truncate font-medium">{targetTitle(t)}</span>
                        {t.detail && <span className="meta w-full truncate text-muted-foreground">{t.detail}</span>}
                        {t.group === "instance" && (
                          <StatusBadge tone={t.connected ? "success" : "neutral"}>
                            {t.connected ? L.connected : L.notConnected}
                          </StatusBadge>
                        )}
                      </button>
                    );
                  })}
                </div>
                {open && g.items.some((t) => open.formats.includes(t)) && (
                  <div className="space-y-2 rounded-lg border border-border p-3">
                    <div>
                      <h4 className="font-medium">{L.formatHeading(open.label)}</h4>
                      <p className="meta text-muted-foreground">{L.formatHelp}</p>
                    </div>
                    <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={L.formatHeading(open.label)}>
                      {formatChoices(open.formats).main.map((f) => (
                        <FormatButton key={f.value} label={f.label} selected={f.value === target} onClick={() => onTarget(f.value)} />
                      ))}
                    </div>
                    {formatChoices(open.formats).others.length > 0 && (
                      <details className="meta" open={formatChoices(open.formats).others.some((f) => f.value === target)}>
                        <summary className="cursor-pointer text-muted-foreground">{L.otherVersions}</summary>
                        <div className="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-label={L.otherVersions}>
                          {formatChoices(open.formats).others.map((f) => (
                            <FormatButton
                              key={f.value}
                              label={f.label}
                              selected={f.value === target}
                              onClick={() => onTarget(f.value)}
                            />
                          ))}
                        </div>
                      </details>
                    )}
                  </div>
                )}
              </section>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-end gap-3">
        {(!file || !target) && <p className="meta text-muted-foreground">{L.startHint}</p>}
        <Button onClick={onStart} disabled={!file || !target}>
          {L.start}
        </Button>
      </div>
    </div>
  );
}

function FormatButton({ label, selected, onClick }: { label: string; selected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onClick}
      className={cn(tileClass(selected), "w-auto px-3 py-2")}
    >
      {label}
    </button>
  );
}

function tileClass(selected: boolean): string {
  return cn(
    "flex min-w-0 flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors",
    selected ? "border-brand bg-brand/5" : "border-border hover:border-brand/60",
  );
}

function FileDrop({ file, onPick }: { file: File | null; onPick: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const pick = (f: File | undefined) => f && onPick(f);
  return (
    <div>
      {file ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-4">
          <FileBadge name={file.name} />
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium">{file.name}</div>
            <div className="meta text-muted-foreground">{size(file.size)}</div>
          </div>
          <Button variant="outline" size="sm" onClick={() => input.current?.click()}>
            {L.fileChange}
          </Button>
        </div>
      ) : (
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
            pick(e.dataTransfer.files[0]);
          }}
          className={cn(
            "flex w-full flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center transition-colors",
            dragging ? "border-brand bg-brand/5" : "border-border hover:border-brand/60",
          )}
        >
          <span className="flex size-10 items-center justify-center rounded-full bg-brand/10 text-brand">
            <Upload className="size-5" strokeWidth={1.75} />
          </span>
          <span className="font-medium">{L.fileDrop}</span>
          <span className="meta">{L.fileTypes}</span>
        </button>
      )}
      <input
        ref={input}
        type="file"
        accept=".xlsx,.csv"
        className="hidden"
        onChange={(e) => {
          pick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}

/** Tuile du fichier choisi : son format (CSV, XLSX) en DM Mono, sur le fond bleu léger de la charte. */
function FileBadge({ name }: { name: string }) {
  const ext = name.includes(".") ? name.split(".").pop()!.toUpperCase().slice(0, 4) : "";
  return (
    <span
      aria-hidden
      className="tech flex h-11 w-10 shrink-0 items-end justify-center rounded-md border border-brand/25 bg-brand/5 pb-1.5 text-[10px] font-semibold text-brand"
    >
      {ext}
    </span>
  );
}

function size(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
