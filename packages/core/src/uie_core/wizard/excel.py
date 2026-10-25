"""Fichier Excel d'exemple de l'assistant de migration : données fictives, champs, mode d'emploi.

Trois feuilles : « Data » (une colonne par champ, une ligne par enregistrement FICTIF), « Fields » (type,
longueur, obligatoire, exemple, statut, description) et « Read me » (statut de la structure, configuration
cible, points à vérifier). Statuts : Verified schema (schéma publié du Registry), Known system (système
identifié, sans schéma), AI proposal (proposé par le LLM). Le fichier n'est jamais présenté comme un modèle
officiel de la cible."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime

import xlsxwriter

from uie_core.wizard.model import ItemStatus, TargetProposal, Verification

STATUS = {
    ItemStatus.VERIFIED_SCHEMA: "Verified schema",
    ItemStatus.KNOWN_SYSTEM: "Known system",
    ItemStatus.AI_PROPOSAL: "AI proposal",
    ItemStatus.YOUR_ANSWER: "Your answer",
    ItemStatus.FROM_YOUR_FILE: "From your file",
}
LEVEL = {
    Verification.VERIFIED: "Verified schema: every field was found in a published Nexa45 Registry schema",
    Verification.PARTIAL: "Partly verified schema: fields marked “AI proposal” are not verified",
    Verification.AI: "AI proposal: not a verified import schema",
}


def field_status(verified: bool) -> str:
    return STATUS[ItemStatus.VERIFIED_SCHEMA if verified else ItemStatus.AI_PROPOSAL]


def filename(p: TargetProposal) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", p.title).strip("_")[:80] or "target_structure"
    return f"{slug}_sample.xlsx"


def render(p: TargetProposal, now: datetime | None = None) -> bytes:
    now = now or datetime.now(UTC)
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(
        buf, {"in_memory": True, "strings_to_numbers": False, "strings_to_formulas": False}
    )
    bold = wb.add_format({"bold": True, "bg_color": "#E8EFFB", "border": 1})
    wrap = wb.add_format({"text_wrap": True, "valign": "top"})
    title = wb.add_format({"bold": True, "font_size": 14})

    data = wb.add_worksheet("Data")
    for col, f in enumerate(p.fields):
        data.write_string(0, col, f.name, bold)
        data.set_column(col, col, max(12, min(40, len(f.name) + 4)))
    for row, values in enumerate(p.rows, start=1):
        for col, value in enumerate(values):
            data.write_string(row, col, value)
    data.freeze_panes(1, 0)

    fields = wb.add_worksheet("Fields")
    head = ["Field", "Label", "Type", "Length", "Required", "Example", "Status", "Description"]
    for col, h in enumerate(head):
        fields.write_string(0, col, h, bold)
    for row, f in enumerate(p.fields, start=1):
        fields.write_string(row, 0, f.name)
        fields.write_string(row, 1, f.label)
        fields.write_string(row, 2, f.type)
        if f.length is not None:
            fields.write_number(row, 3, f.length)
        fields.write_string(row, 4, "Yes" if f.required else "No")
        fields.write_string(row, 5, f.example or "")
        fields.write_string(row, 6, field_status(f.verified))
        fields.write_string(row, 7, f.description or "", wrap)
    for col, width in enumerate([28, 32, 12, 8, 10, 24, 18, 60]):
        fields.set_column(col, col, width)
    fields.freeze_panes(1, 0)

    about = wb.add_worksheet("Read me")
    about.set_column(0, 0, 28)
    about.set_column(1, 1, 70)
    about.set_column(2, 2, 18)
    about.write_string(0, 0, p.title, title)
    about.write_string(1, 0, "Status", bold)
    about.write_string(1, 1, LEVEL[p.verification], wrap)
    about.write_string(2, 0, "Sample data", bold)
    about.write_string(2, 1, "All records in the “Data” sheet are fictitious examples.", wrap)
    about.write_string(3, 0, "Generated", bold)
    about.write_string(3, 1, now.strftime("%Y-%m-%d %H:%M UTC") + " by the Nexa45 AI Migration Wizard")
    r = 5
    if p.summary:
        about.write_string(r, 0, "Summary", bold)
        about.write_string(r, 1, p.summary, wrap)
        r += 2
    about.write_string(r, 0, "Target configuration", bold)
    about.write_string(r, 2, "Status", bold)
    r += 1
    for c in p.configuration:
        about.write_string(r, 0, c.label)
        about.write_string(r, 1, c.value, wrap)
        about.write_string(r, 2, STATUS[c.status])
        r += 1
    if p.notes:
        r += 1
        about.write_string(r, 0, "Before any import", bold)
        for n in p.notes:
            about.write_string(r, 1, n, wrap)
            r += 1
    wb.close()
    return buf.getvalue()
