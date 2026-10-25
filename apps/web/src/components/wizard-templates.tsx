"use client";

/** Barre latérale des modèles de questionnaire du Wizard (inspirée d'un historique de conversations) : sobre,
 *  compacte, repliable. Un modèle = les questions et réponses du questionnaire initial, sauvegardées pour
 *  l'utilisateur (serveur) ; jamais un fichier, une correspondance ni une donnée. Logique pure :
 *  `lib/wizard/templates.mjs`. */
import { Check, FileText, Loader2, PanelLeftClose, PanelLeftOpen, Pencil, Plus, Save, Trash2, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type WizardAnswer,
  type WizardQuestion,
  type WizardTemplate,
  useDeleteWizardTemplate,
  useRenameWizardTemplate,
  useSaveWizardTemplate,
  useWizardTemplates,
} from "@/lib/api/wizard-hooks";
import { cn } from "@/lib/utils";
import { cleanName, defaultTemplateName, SIDEBAR_KEY, templateMeta, templateSteps } from "@/lib/wizard/templates.mjs";

type Step = { question: WizardQuestion; answer: WizardAnswer };

export function TemplateSidebar({
  history,
  busy,
  onNew,
  onUse,
}: {
  history: Step[];
  busy: boolean;
  onNew: () => void;
  onUse: (template: WizardTemplate) => void;
}) {
  // préférence d'affichage seulement (par navigateur) ; les modèles, eux, sont sur le serveur. Lue au premier
  // rendu : le portail n'est rendu que côté client, après l'authentification.
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) !== "closed";
    } catch {
      return true; // stockage indisponible : barre ouverte
    }
  });
  function toggle() {
    setOpen((o) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, o ? "closed" : "open");
      } catch {
        // stockage indisponible
      }
      return !o;
    });
  }

  if (!open) {
    return (
      <aside className="flex gap-1 lg:sticky lg:top-4 lg:flex-col" aria-label="Templates">
        <Button variant="ghost" size="icon" onClick={toggle} aria-label="Show templates" title="Templates">
          <PanelLeftOpen className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onNew}
          disabled={busy}
          aria-label="New migration"
          title="New migration"
        >
          <Plus className="size-4" />
        </Button>
      </aside>
    );
  }
  return (
    <aside
      className="w-full shrink-0 space-y-3 rounded-card border bg-background/70 p-3 lg:sticky lg:top-4 lg:w-64"
      aria-label="Templates"
    >
      <div className="flex items-center justify-between">
        <p className="eyebrow text-muted-foreground">Templates</p>
        <Button variant="ghost" size="icon" onClick={toggle} aria-label="Hide templates" title="Hide templates">
          <PanelLeftClose className="size-4" />
        </Button>
      </div>
      <Button variant="outline" size="sm" className="w-full justify-start" onClick={onNew} disabled={busy}>
        <Plus className="size-4" /> New migration
      </Button>
      <TemplateList history={history} busy={busy} onUse={onUse} />
    </aside>
  );
}

function TemplateList({
  history,
  busy,
  onUse,
}: {
  history: Step[];
  busy: boolean;
  onUse: (template: WizardTemplate) => void;
}) {
  const templates = useWizardTemplates();
  const save = useSaveWizardTemplate();
  const [naming, setNaming] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const names = (templates.data ?? []).map((t) => t.name);

  function submit() {
    const name = cleanName(naming ?? "");
    if (!name) return;
    save.mutate({ name, steps: templateSteps(history) }, { onSuccess: (t) => (setNaming(null), setActive(t.id)) });
  }

  return (
    <div className="space-y-3">
      {naming === null ? (
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start"
          disabled={history.length === 0 || busy}
          title={history.length === 0 ? "Answer at least one question first" : undefined}
          onClick={() => {
            save.reset();
            setNaming(defaultTemplateName(history, names));
          }}
        >
          <Save className="size-4" /> Save current answers
        </Button>
      ) : (
        <NameInput
          value={naming}
          label="Template name"
          pending={save.isPending}
          onChange={setNaming}
          onSubmit={submit}
          onCancel={() => setNaming(null)}
        />
      )}
      {save.error && <p className="meta text-danger">{save.error.message}</p>}

      {templates.isLoading ? (
        <p className="meta text-muted-foreground">Loading…</p>
      ) : templates.error ? (
        <p className="meta text-danger">{templates.error.message}</p>
      ) : names.length === 0 ? (
        <p className="meta text-muted-foreground">
          No template yet. Save your answers to start a new migration without the questions.
        </p>
      ) : (
        <ul className="space-y-0.5">
          {(templates.data ?? []).map((t) => (
            <TemplateItem
              key={t.id}
              template={t}
              active={active === t.id}
              busy={busy}
              onUse={() => {
                setActive(t.id);
                onUse(t);
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function TemplateItem({
  template: t,
  active,
  busy,
  onUse,
}: {
  template: WizardTemplate;
  active: boolean;
  busy: boolean;
  onUse: () => void;
}) {
  const rename = useRenameWizardTemplate();
  const remove = useDeleteWizardTemplate();
  const [naming, setNaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  if (naming !== null) {
    return (
      <li className="py-1">
        <NameInput
          value={naming}
          label={`New name for ${t.name}`}
          pending={rename.isPending}
          onChange={setNaming}
          onSubmit={() => {
            const name = cleanName(naming);
            if (name) rename.mutate({ id: t.id, name }, { onSuccess: () => setNaming(null) });
          }}
          onCancel={() => setNaming(null)}
        />
        {rename.error && <p className="meta text-danger">{rename.error.message}</p>}
      </li>
    );
  }
  return (
    <li
      className={cn(
        "group flex items-center gap-1 rounded-md px-2 py-1.5 hover:bg-muted",
        active && "bg-muted font-medium",
      )}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 items-start gap-2 text-left disabled:opacity-50"
        onClick={onUse}
        disabled={busy}
        title={`Use: ${t.name}`}
        aria-label={`Use ${t.name}`}
      >
        <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0">
          <span className="block truncate">{t.name}</span>
          <span className="meta block text-muted-foreground">{templateMeta(t)}</span>
        </span>
      </button>
      {deleting ? (
        <span className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Confirm delete ${t.name}`}
            title="Delete"
            disabled={remove.isPending}
            onClick={() => remove.mutate(t.id)}
          >
            {remove.isPending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4 text-danger" />}
          </Button>
          <Button variant="ghost" size="icon" aria-label="Keep" title="Keep" onClick={() => setDeleting(false)}>
            <X className="size-4" />
          </Button>
        </span>
      ) : (
        <span className="flex items-center opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Rename ${t.name}`}
            title="Rename"
            onClick={() => setNaming(t.name)}
          >
            <Pencil className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Delete ${t.name}`}
            title="Delete"
            onClick={() => setDeleting(true)}
          >
            <Trash2 className="size-3.5" />
          </Button>
        </span>
      )}
    </li>
  );
}

function NameInput({
  value,
  label,
  pending,
  onChange,
  onSubmit,
  onCancel,
}: {
  value: string;
  label: string;
  pending: boolean;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  return (
    <form
      className="flex items-center gap-1"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <input
        className="input h-8 min-w-0 flex-1"
        aria-label={label}
        autoFocus
        maxLength={120}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onCancel()}
      />
      <Button
        type="submit"
        variant="ghost"
        size="icon"
        aria-label="Save"
        title="Save"
        disabled={pending || !cleanName(value)}
      >
        {pending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
      </Button>
      <Button type="button" variant="ghost" size="icon" aria-label="Cancel" title="Cancel" onClick={onCancel}>
        <X className="size-4" />
      </Button>
    </form>
  );
}
