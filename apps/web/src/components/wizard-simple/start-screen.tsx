"use client";

/** Écran 1 du mode Simple : déposer le fichier et choisir la cible. Vos systèmes connectés d'abord, puis les
 *  systèmes du Registry, puis les autres (structure proposée par l'IA) ; recherche au-delà de quelques choix. */
import { FileSpreadsheet, Loader2, Search, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { RadioList } from "@/components/ui/radio-list";
import { Card, CardContent } from "@/components/ui/card";
import { useWizardDetect, useWizardTargets } from "@/lib/api/wizard-hooks";
import { cn } from "@/lib/utils";
import { entryTargets, type WizardEntry } from "@/lib/wizard/entries.mjs";
import {
  afterDetect,
  defaultTarget,
  detectCandidates,
  formatChoices,
  openTile,
  soleSystem,
  type SystemTile,
  systemTileDetail,
  targetGroups,
  targetSubtitle,
  targetTiles,
  targetTitle,
} from "@/lib/wizard/simple.mjs";
import { SIMPLE_LABELS as L } from "@/lib/wizard/simple-labels.mjs";

/** Au-delà, la liste propose une recherche. */
const SEARCH_FROM = 8;

export function StartScreen({
  entry,
  file,
  onFile,
  target,
  onTarget,
  onStart,
}: {
  entry: WizardEntry;
  file: File | null;
  onFile: (file: File) => void;
  target: string;
  onTarget: (value: string) => void;
  onStart: () => void;
}) {
  const targets = useWizardTargets();
  const [search, setSearch] = useState("");
  // cibles du point d'entrée seulement (Wizard général : toutes)
  const all = useMemo(() => entryTargets(targets.data ?? [], entry), [targets.data, entry]);
  const groups = targetGroups(all, search);
  // système à plusieurs formats ouvert : son format se choisit sous les tuiles
  const [openSystem, setOpenSystem] = useState("");
  const tiles = groups.flatMap((g) => targetTiles(g.items));
  const open = openTile(tiles, openSystem, target);
  const sole = soleSystem(tiles);
  const heading = entry.targetHeading ?? L.targetHeading;
  // option cochée : le système ouvert (ses formats ou versions se choisissent dessous), sinon la cible
  const selected = open ? open.key : target;
  function pickTile(key: string) {
    const tile = tiles.find((t) => t.key === key);
    if (tile?.kind === "system") {
      setOpenSystem(tile.system);
      if (!tile.formats.some((f) => f.value === target)) onTarget("");
    } else {
      setOpenSystem("");
      onTarget(key);
    }
  }
  // point d'entrée d'un système : le format est choisi dès le dépôt du fichier (règles du serveur, sans LLM)
  const detect = useWizardDetect();
  const [auto, setAuto] = useState(false);
  // fichier déjà soumis à la détection : elle part dès que le fichier ET les formats sont là (un fichier déposé
  // avant la fin du chargement des formats est détecté ensuite, jamais oublié)
  const detected = useRef<File | null>(null);
  const candidates = sole ? detectCandidates(sole).join("|") : "";
  useEffect(() => {
    if (!entry.detect || !candidates || !file || detected.current === file) return;
    detected.current = file;
    const f = file;
    detect.mutate(
      { file: f, targets: candidates.split("|") },
      {
        onSuccess: (result) => {
          if (detected.current !== f) return; // un autre fichier a été déposé entre-temps
          const next = afterDetect(target, auto, result);
          setAuto(next.auto);
          if (next.target !== target) onTarget(next.target);
        },
      },
    );
  }, [entry.detect, candidates, file, detect, target, auto, onTarget]);
  function pick(f: File) {
    onFile(f);
  }
  function choose(value: string) {
    setAuto(false);
    onTarget(value);
  }

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
          <FileDrop file={file} onPick={pick} />
        </CardContent>
      </Card>

      <Card className="bg-transparent shadow-none">
        <CardContent className="space-y-4">
          <h2 className="item-title">{heading}</h2>
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
          {sole ? (
            // un seul système à l'écran (point d'entrée d'un système) : ses choix directement, sans groupe ni tuile
            <div className="space-y-2">
              <Choices tile={sole} target={target} onTarget={choose} label={heading} />
              {detect.isPending && (
                <p className="meta flex items-center gap-2 text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> {L.detecting}
                </p>
              )}
              {auto && target && !detect.isPending && <p className="meta text-muted-foreground">{L.autoChosen}</p>}
            </div>
          ) : (
          <div className="space-y-5">
            {groups.map((g) => (
              <section key={g.group} className="space-y-2.5">
                <div>
                  <h3 className="eyebrow">{L.groups[g.group]}</h3>
                  <p className="meta text-muted-foreground">{L.groupHelp[g.group]}</p>
                </div>
                <RadioList
                  label={L.groups[g.group]}
                  layout={g.group === "known" ? "columns" : "list"}
                  value={selected}
                  onChange={pickTile}
                  options={targetTiles(g.items).map((tile) =>
                    tile.kind === "system"
                      ? {
                          value: tile.key,
                          label: tile.label,
                          hint: systemTileDetail(tile),
                          content: <Choices tile={tile} target={target} onTarget={onTarget} />,
                        }
                      : {
                          value: tile.key,
                          label: targetTitle(tile.target),
                          hint:
                            tile.target.group === "instance" ? (
                              <span className="inline-flex flex-wrap items-center gap-2">
                                {targetSubtitle(tile.target)}
                                <StatusBadge tone={tile.target.connected ? "success" : "neutral"}>
                                  {tile.target.connected ? L.connected : L.notConnected}
                                </StatusBadge>
                              </span>
                            ) : (
                              targetSubtitle(tile.target) || undefined
                            ),
                        },
                  )}
                />
              </section>
            ))}
          </div>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-end gap-3">
        {/* sans fichier : le fichier à donner ; fichier sans format reconnu : le format à choisir */}
        {(!file || !target) && (
          <p className="meta text-muted-foreground">
            {/* fichier refusé par le serveur (format, relevé illisible) : sa raison, jamais « non reconnu » */}
            {(file ? (detect.error?.message ?? entry.targetHint) : entry.startHint) ?? L.startHint}
          </p>
        )}
        <Button onClick={onStart} disabled={!file || !target}>
          {L.start}
        </Button>
      </div>
    </div>
  );
}

/** Formats ou versions d'un système : sous l'option du système choisi, ou seuls quand le système est le seul à
 *  l'écran (`label` : la question est déjà l'intitulé de la carte, ni second titre ni aide). */
function Choices({
  tile,
  target,
  onTarget,
  label,
}: {
  tile: SystemTile;
  target: string;
  onTarget: (value: string) => void;
  label?: string;
}) {
  const { main, others } = formatChoices(tile.formats);
  return (
    <div className="space-y-3">
      {label === undefined && (
        <div>
          <h4 className="font-medium">{choiceHeading(tile)}</h4>
          <p className="meta text-muted-foreground">{tile.choose === "version" ? L.versionHelp : L.formatHelp}</p>
        </div>
      )}
      <RadioList options={main} value={target} onChange={onTarget} label={label ?? choiceHeading(tile)} />
      {others.length > 0 && (
        <details className="meta" open={others.some((f) => f.value === target)}>
          <summary className="cursor-pointer text-muted-foreground">{L.otherVersions}</summary>
          <RadioList className="mt-2.5" options={others} value={target} onChange={onTarget} label={L.otherVersions} />
        </details>
      )}
    </div>
  );
}

/** Question sous une tuile de système : son format, ou sa version. */
function choiceHeading(tile: { label: string; choose: "format" | "version" }): string {
  return tile.choose === "version" ? L.versionHeading(tile.label) : L.formatHeading(tile.label);
}

function FileDrop({ file, onPick }: { file: File | null; onPick: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const pick = (f: File | undefined) => f && onPick(f);
  return (
    <div>
      {file ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-4">
          <span
            aria-hidden
            className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-brand/10 text-brand"
          >
            <FileSpreadsheet className="size-5" strokeWidth={1.75} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium">{file.name}</div>
            <div className="meta text-muted-foreground">{fileMeta(file)}</div>
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
        accept=".xlsx,.csv,.xml"
        className="hidden"
        onChange={(e) => {
          pick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}

/** Ligne sous le nom du fichier choisi : son format puis sa taille (« XLSX · 8 KB »). */
function fileMeta(file: File): string {
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toUpperCase() : "";
  return ext ? `${ext} · ${size(file.size)}` : size(file.size);
}

function size(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
