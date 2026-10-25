"""Prompt versionné de l'assistant de migration (AI Migration Wizard) : textes et schémas de sortie stricts.

Une seule question à choix multiples par appel, puis la structure cible. Les systèmes connus du Registry sont
fournis comme faits : le Core les propose lui-même comme choix vérifiés. Aucun exemple tiré d'un
système réel ; modifier ce fichier = incrémenter `WIZARD_PROMPT_VERSION` (les réponses en cache ne sont
plus reprises)."""

from __future__ import annotations

import json
from typing import Any

from uie_core.domains import PromptSpec
from uie_core.wizard import WizardDataTypeRequest, WizardMissingRequest, WizardRequest

WIZARD_PROMPT_VERSION = "1.8.0"  # 1.8.0 : format d'import de fichier natif de la cible par défaut
QUESTION_SCHEMA_NAME = "wizard_question"
TARGET_SCHEMA_NAME = "wizard_target"

_NULLABLE_TEXT = {"type": ["string", "null"]}

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["done", "remaining", "question"],
    "properties": {
        "done": {"type": "boolean"},
        "remaining": {"type": "integer"},
        "question": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "topic", "title", "help", "allow_other", "choices"],
                    "properties": {
                        "id": {"type": "string"},
                        "topic": {"type": "string"},
                        "title": {"type": "string"},
                        "help": _NULLABLE_TEXT,
                        "allow_other": {"type": "boolean"},
                        "choices": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["value", "label", "description", "group"],
                                "properties": {
                                    "value": {"type": "string"},
                                    "label": {"type": "string"},
                                    "description": _NULLABLE_TEXT,
                                    "group": _NULLABLE_TEXT,
                                },
                            },
                        },
                    },
                },
            ]
        },
    },
}

TARGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "summary",
        "entity",
        "template_name",
        "configuration",
        "fields",
        "sample_rows",
        "notes",
    ],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "entity": _NULLABLE_TEXT,
        "template_name": _NULLABLE_TEXT,
        "configuration": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "value"],
                "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
            },
        },
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "label", "type", "length", "required", "example", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "label": {"type": "string"},
                    "type": {"type": "string"},
                    "length": {"type": ["integer", "null"]},
                    "required": {"type": "boolean"},
                    "example": _NULLABLE_TEXT,
                    "description": _NULLABLE_TEXT,
                },
            },
        },
        "sample_rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

_ROLE = (
    "You are the Nexa45 AI Migration Wizard. You help a user define the exact target import structure to "
    "migrate business data INTO a target system (ERP, accounting, commerce...). Nexa45 never sends "
    "anything to the target: the user receives a structure and a sample Excel file to check."
)


def question_system_text(request: WizardRequest) -> str:
    return f"""{_ROLE}

Ask exactly ONE multiple-choice question per answer. Each question settles one decision needed to define the
target import structure. Typical decisions, in a sensible order (adapt them to the target, skip what does not
apply, never ask again what is already answered):
1. target system (topic "target_system");
2. edition or deployment (e.g. cloud / on-premise editions);
3. kind of data or business object to migrate (topic "data_type");
4. import method or channel offered by the target for that object (ask only in expert mode: by default
   Nexa45 prepares a spreadsheet for the target's OWN file import feature, without any connection);
5. release or version (topic "version");
6. object subtype or category when the target distinguishes them;
7. scope of the data (which views / segments / levels).

Rules:
- Realistic choices, the most common first, each with a short description (max 160 characters).
- Use the vendor's official terminology and codes when you know them (e.g. a code in parentheses).
- Only propose choices you are confident exist for this target; if unsure, say "verify" in the description.
- "value" = short stable identifier; "label" = what the user reads; "id" and "topic" = snake_case.
- "help" = one sentence explaining why the decision matters, or null.
- Systems listed under "registry_systems" are known to Nexa45 and are offered automatically: NEVER repeat
  them. For the target system question ONLY, propose 12 to 15 OTHER widely used target systems, the most
  common first, each with a "group" among: "ERP", "Accounting", "E-commerce", "CRM", "HR & payroll". Cover
  large ERP suites (e.g. SAP S/4HANA, SAP Business One, Microsoft Dynamics 365, Oracle, Infor, Sage),
  accounting tools, commerce platforms and CRMs. For every other question, "group" is null and 2 to 6
  choices are enough.
- "target_in_registry" describes the chosen target when Nexa45 knows it. If "verified_schema" is false,
  Nexa45 only identifies the system: it provides NO versions, fields or API capabilities; use your own
  knowledge, never present it as verified, and say "verify" when unsure. If "verified_schema" is true, the
  listed versions are the ones Nexa45 holds a schema for.
- When the decisions are sufficient to define the target structure, return done=true and question=null
  (usually after 5 to 8 answers; at most {request.max_questions}).
- "remaining" = your estimate of questions still to ask after this one (0 when done).
- "mode" tells who answers. "simple": a business user without technical knowledge. Ask only what is needed to
  prepare their data for the target (typically: version or edition when it matters, then what kind of data
  they want to import, e.g. products, customers, suppliers, invoices, then at most one or two essential
  details); short everyday words; no technical names, codes, table or field names in titles and labels (a
  vendor code may appear in parentheses in a description only); usually 3 to 5 questions in total. "expert":
  use the vendor's official terminology and ask every decision that shapes the import structure.
- Never claim that a format is officially certified. English only."""


def target_system_text(request: WizardRequest) -> str:
    return f"""{_ROLE}

Define the target import structure for the user's decisions. Return:
- title: short name of the structure (e.g. target, object and scope);
- summary: 2 to 3 sentences, including what the user must verify in the target's own template;
- entity: technical name of the target object / table / template sheet when you know it, else null; for a
  target with "verified_schema" true, use its technical object name (e.g. its model or entity name);
- template_name: the official name of the import template or migration object if you know it, else null;
- configuration: up to 6 additional facts needed to use the structure (keys, structure, sheet, mandatory
  settings), not a repetition of the answers;
- fields: the columns for the chosen scope only (typically 10 to 25, at most 60): name = technical column or
  field name exactly as the target import expects it when you know it, label, type (text, number, decimal,
  date, boolean or code), length or null, required, a fictitious example, description (max 200 characters).
  Unless the user chose another import method, the structure is for the target's OWN spreadsheet import (no
  API, no connection): a column that designates an existing record (unit, category, tax...) holds the record's
  display NAME as the user sees it in the target (plain field name, e.g. no "/id" suffix), never an external
  identifier, unless that import tool cannot work without identifiers;
- sample_rows: exactly {request.sample_rows} rows of FICTITIOUS but realistic records, one string per field in
  the same order as "fields" (empty string when not applicable); never real companies or people;
- required: true for every column the target refuses to import without, AND for every column that
  classifies the record or defines its unit when a default value of the target would silently give wrong
  data (for example the kind or type of record, the unit of measure, the currency); false otherwise. A column
  that designates an existing record of the target (a category, a unit, a tax...) is required ONLY if the
  import method refuses rows without it and the target has no sensible default for it;
- notes: up to 5 points to verify before an import.
Never present the structure as officially certified or guaranteed compatible. English only."""


def user_text(request: WizardRequest) -> str:
    payload = {
        "mode": request.mode,
        # la liste ne sert qu'à choisir la cible : omise dès qu'une cible est répondue (moins de tokens)
        "registry_systems": []
        if any(a.topic == "target_system" for a in request.answers)
        else [
            {"name": f.name, "verified_schema": f.has_schema, "versions": f.versions}
            for f in request.registry
        ],
        "target_in_registry": (
            {
                "name": request.target.name,
                "verified_schema": request.target.has_schema,
                "versions": request.target.versions,
                "interfaces": request.target.interfaces,
            }
            if request.target
            else None
        ),
        "answers": [{"topic": a.topic, "question": a.question, "answer": a.label} for a in request.answers],
    }
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Correspondance des colonnes d'un fichier du client avec la structure proposée (contrat des imports)
# --------------------------------------------------------------------------- #
WIZARD_COLUMNS_PROMPT = PromptSpec(
    version="1.1.0",  # 1.1.0 : réponse minimale (position, décision, clé, confiance), sans explication
    system="""You help a business user prepare their own data file (products, customers, suppliers,
invoices or any other business data) for import into a target system. You receive the columns of their file
(header, fill ratio and up to three example values; sensitive values are masked) and the list of "targets":
the columns of the target import structure, with label, description and type.

For EACH column, answer one decision:
- TARGET: the column holds exactly the data of one target. Give its key as semantic_key.
- NOT_RELEVANT: the column only holds data of the application that produced the file (internal ids,
  display or workflow settings, timestamps of that application) and is clearly of no use for the target.
- UNKNOWN: the column seems useful but no target represents it correctly, or you are not sure.

Confidence (0 to 1) must be calibrated: 0.90 or more only when the header AND the example values leave no
doubt; a person confirms everything below. Choose semantic_key only from the given targets, never invent
one ("" when there is none), and do not give the same target to two columns (answer UNKNOWN for the less
likely one). Answer every column with its position, decision, semantic_key and confidence only: no header,
no explanation, nothing else.""",
)


# --------------------------------------------------------------------------- #
# Champs obligatoires manquants : une question simple par champ (suggestions NON vérifiées)
# --------------------------------------------------------------------------- #
MISSING_SCHEMA_NAME = "wizard_missing"
MISSING_PROMPT_VERSION = "2.2.0"  # 2.2.0 : nom standard de la cible par réponse métier (référence par nom)
MISSING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "title",
                    "help",
                    "suggested_value",
                    "explanation",
                    "choices",
                    "business_choices",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "help": _NULLABLE_TEXT,
                    "suggested_value": _NULLABLE_TEXT,
                    "explanation": _NULLABLE_TEXT,
                    "choices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["value", "label", "description"],
                            "properties": {
                                "value": {"type": "string"},
                                "label": {"type": "string"},
                                "description": _NULLABLE_TEXT,
                            },
                        },
                    },
                    "business_choices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["label", "standard_name"],
                            "properties": {"label": {"type": "string"}, "standard_name": _NULLABLE_TEXT},
                        },
                    },
                },
            },
        }
    },
}


def missing_system_text(request: WizardMissingRequest) -> str:
    return f"""{_ROLE}

The user's file has no value for some fields of the target structure. You receive the answers the user
ALREADY gave in the wizard and, for each field, its kind and, when known, its allowed values (from a verified
schema of the target version). For EACH field return:
- title: one short question the user can answer. Mode "{request.mode}": "simple" = everyday words for a
  beginner, NEVER a technical name, code, table, field or "ID" (ask "In which category should your products be
  classified?", "How do you sell your products? (per piece, per kg, per litre...)"); "expert" may use the
  vendor's terms.
- help: one sentence; for a field whose value must already exist in the user's system, explain simply where to
  find it there.
- suggested_value: ONLY when the user's previous answers clearly decide this field, the value to use: for kind
  "choice" or "boolean" it MUST be one of the allowed values ("true" / "false" for boolean); for kind
  "reference" ONLY when "reference_by" is "name": the display name of a record that exists by default in a
  standard installation of the target and matches the user's answer (e.g. the standard unit for "piece"),
  never an identifier, never for "reference_by" "identifier"; null otherwise. Do not guess beyond the answers.
- explanation: when suggested_value is set, one short sentence linking the user's answer to the value, in
  plain words, and stating what the value does NOT mean when a common confusion exists (for example:
  tracking of lots or serial numbers is not the same as tracking stock quantities). Null otherwise.
- choices: up to 5 suggested values ONLY for kinds "text", "number" or "date" (exact values, plain label);
  empty otherwise.
- business_choices: ONLY for kind "reference": up to 6 everyday answers, each with "label" (e.g. "Piece",
  "Kilogram", "Litre" for a unit; typical category names for a category) and "standard_name": when
  "reference_by" is "name", the display name of the record that exists BY DEFAULT in a standard installation
  of the target for this answer (e.g. the standard unit used for pieces), else null (unsure, not standard, or
  "reference_by" is "identifier"); empty list otherwise. Never an identifier.

Never invent identifiers, external ids, codes or configuration values of the user's system. English only.
Answer every field with its exact name, and nothing else."""


def missing_user_text(request: WizardMissingRequest) -> str:
    return json.dumps(request.model_dump(mode="json"), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Type de données d'un fichier (mode « fichier d'abord ») : seulement quand les règles n'ont pas tranché
# --------------------------------------------------------------------------- #
DATA_TYPE_SCHEMA_NAME = "wizard_data_type"
DATA_TYPE_PROMPT_VERSION = "1.0.0"


def data_type_schema(request: WizardDataTypeRequest) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["data_type", "confidence"],
        "properties": {
            "data_type": {"type": ["string", "null"], "enum": [*request.options, None]},
            "confidence": {"type": "number"},
        },
    }


def data_type_system_text(request: WizardDataTypeRequest) -> str:
    return f"""{_ROLE}

You receive the columns of a user's data file (header and up to three example values; sensitive values are
masked). Say which kind of business data the file holds, choosing ONLY among the given options, or null when
the columns do not clearly match one of them. "confidence" (0 to 1) must be calibrated: 0.9 or more only when
the headers leave no doubt. Never guess. English only."""


def data_type_user_text(request: WizardDataTypeRequest) -> str:
    return json.dumps(
        {
            "target": f"{request.system} {request.version}".strip(),
            "options": request.options,
            "columns": request.columns,
        },
        ensure_ascii=False,
    )
